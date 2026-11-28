// Reports how a dashboard feels on this device to welkom, which logs it to
// Logfire under whose device it is.
//
// One report per cold page load, one each time the page comes back on screen
// after a while away (the companion app foregrounded, which is how a phone
// mostly opens it), and one per move to another view. Each report watches for
// WINDOW_MS, or until the next one starts:
//
//  - view_ms: until the view had its cards: the last card to connect before
//    a second without any (later ones are conditional cards flipping, not
//    the view arriving);
//  - per live camera: when its first new frame was drawn, and which player
//    drew it (HA starts WebRTC and HLS together and shows whichever wins);
//  - stalls: animation frames over STALL_MS. Counted from frames rather than
//    the Long Tasks API, which iOS doesn't have.
//
// It finds cards and cameras by wrapping their elements' connectedCallback, so
// it never walks the DOM: the Oasis view has 15,000 nodes, and polling them
// would cause the stutter it is meant to measure. Only the handful of camera
// players are polled, each a querySelector or two.
//
// The beacon goes to /welkom-timings on Home Assistant's own origin, which
// Traefik sends to welkom through forward auth. Where that route doesn't exist
// it 404s, and nothing else happens.

const WINDOW_MS = 20000;
const STALL_MS = 100;
const PAUSE_MS = 3000; // a gap this long is the page frozen or the display asleep, not a stutter
const VIEW_QUIET_MS = 1000; // no new card for this long: the view has arrived
const RESUME_AFTER_MS = 10000; // shorter absences aren't an app being reopened
const POLL_MS = 100;
const MAX_MS = 600000; // welkom's limit; it clamps too, but don't rely on it
const ENDPOINT = "/welkom-timings";

const cameras = new Set(); // connected ha-camera-stream elements
let report = null;
let hiddenAt = null;
let loadReported = false;

function cardConnected() {
  const r = report;
  if (!r || r.viewSettled) return;
  const now = performance.now();
  if (r.viewAt !== null && now - r.viewAt >= VIEW_QUIET_MS) {
    r.viewSettled = true;
    return;
  }
  r.viewAt = now;
}

// Patched as each class is defined, not after: HA's templates create these
// elements before their chunk defines them, and the browser upgrades every
// existing one inside define() itself, before a whenDefined callback runs.
// The browser also reads lifecycle callbacks only at define(), so a class
// defined before this script ran can't be patched at all; HA loads both from
// lazy chunks after extra modules, so in practice that doesn't happen.
const WATCHED = {
  "hui-card": { connected: cardConnected },
  "ha-camera-stream": {
    connected: (el) => cameras.add(el),
    disconnected: (el) => cameras.delete(el),
  },
};

function patch(cls, hooks) {
  const proto = cls.prototype;
  if (proto.__welkomTimings) return;
  proto.__welkomTimings = true;
  for (const [name, hook] of [
    ["connectedCallback", hooks.connected],
    ["disconnectedCallback", hooks.disconnected],
  ]) {
    if (!hook) continue;
    const original = proto[name];
    proto[name] = function (...args) {
      try {
        hook(this);
      } catch (_err) {
        // measuring must never break the dashboard
      }
      return original?.apply(this, args);
    };
  }
}

const define = customElements.define;
customElements.define = function (name, cls, options) {
  try {
    if (Object.hasOwn(WATCHED, name)) patch(cls, WATCHED[name]);
  } catch (_err) {
    // never in the way of a definition
  }
  return define.call(this, name, cls, options);
};

// What each camera is showing: [player, element] for HA's video players (the
// one HA has hidden lost the race and is skipped), or its MJPEG <img>.
function picturesOf(stream) {
  const out = [];
  const root = stream.shadowRoot;
  if (!root) return out;
  for (const player of root.querySelectorAll("ha-web-rtc-player, ha-hls-player")) {
    if (player.classList.contains("hidden")) continue;
    const video = player.shadowRoot?.querySelector("video");
    if (video) out.push([player.tagName === "HA-WEB-RTC-PLAYER" ? "webrtc" : "hls", video]);
  }
  const img = root.querySelector("img");
  if (img) out.push(["mjpeg", img]);
  return out;
}

function showing(r, el) {
  if (el.tagName === "IMG") return el.complete && el.naturalWidth > 0;
  if (!r.baseline.has(el)) r.baseline.set(el, -1);
  return el.videoWidth > 0 && el.readyState >= 2 && el.currentTime > r.baseline.get(el) + 0.01;
}

function visible(el) {
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function start(trigger, t0) {
  if (report) finish();
  const r = {
    trigger,
    t0,
    path: location.pathname.slice(0, 200),
    viewAt: null,
    viewSettled: false,
    cams: new Map(), // entity -> {entity, player, first_frame_ms}
    hiddenCams: new Set(), // streams found to be in a hidden layout copy
    baseline: new WeakMap(), // video -> currentTime when first seen
    stalls: 0,
    stallMs: 0,
    worstStall: 0,
    lastFrame: null,
  };
  report = r;
  // location-changed fires before HA swaps the view, so the cameras connected
  // now belong to the view being left; the new view's are new elements.
  if (trigger === "navigate") for (const stream of cameras) r.hiddenCams.add(stream);
  // Videos already playing when a resume starts must show a *new* frame.
  for (const stream of cameras) {
    for (const [, el] of picturesOf(stream)) {
      if (el.tagName === "VIDEO") r.baseline.set(el, el.currentTime);
    }
  }
  const poll = setInterval(() => {
    if (report !== r) return clearInterval(poll);
    const now = performance.now();
    if (now - r.t0 > WINDOW_MS) return finish();
    for (const stream of cameras) {
      const entity = stream.stateObj?.entity_id;
      if (!entity || r.hiddenCams.has(stream) || r.cams.get(entity)?.first_frame_ms != null) continue;
      for (const [player, el] of picturesOf(stream)) {
        if (!showing(r, el)) continue;
        // Layout only once something shows, not every 100 ms: forcing it on
        // a page this size would add to the stutter being counted.
        if (!visible(stream)) {
          r.hiddenCams.add(stream);
          break;
        }
        r.cams.set(entity, { entity, player, first_frame_ms: Math.round(now - r.t0) });
        break;
      }
    }
  }, POLL_MS);
  const frame = (ts) => {
    if (report !== r) return;
    if (r.lastFrame !== null) {
      const gap = ts - r.lastFrame;
      if (gap > STALL_MS && gap < PAUSE_MS) {
        r.stalls += 1;
        r.stallMs += gap;
        r.worstStall = Math.max(r.worstStall, gap);
      }
    }
    r.lastFrame = ts;
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// Cameras that never showed anything, but only ones on screen: hidden layout
// copies also connect, and would read as failures.
function missingCameras(r) {
  const out = [];
  for (const stream of cameras) {
    const entity = stream.stateObj?.entity_id;
    if (!entity || r.cams.has(entity) || r.hiddenCams.has(stream)) continue;
    try {
      if (visible(stream)) out.push({ entity, player: "none", first_frame_ms: null });
    } catch (_err) {
      // ignore
    }
  }
  return out;
}

function finish() {
  const r = report;
  report = null;
  if (!r) return;
  const nav = r.trigger === "load" ? performance.getEntriesByType("navigation")[0] : null;
  const ms = (v) => (v == null || !isFinite(v) ? null : Math.min(Math.max(Math.round(v), 0), MAX_MS));
  const body = {
    path: r.path,
    trigger: r.trigger,
    standalone:
      window.matchMedia?.("(display-mode: standalone)").matches ||
      navigator.standalone === true ||
      /Home Assistant/.test(navigator.userAgent),
    width: innerWidth,
    height: innerHeight,
    ttfb_ms: ms(nav?.responseStart),
    dom_ready_ms: ms(nav?.domContentLoadedEventEnd),
    view_ms: r.viewAt !== null ? ms(r.viewAt - r.t0) : null,
    cameras: [...r.cams.values(), ...missingCameras(r)].slice(0, 24),
    stalls: Math.min(r.stalls, 100000),
    stall_ms: ms(r.stallMs),
    worst_stall_ms: ms(r.worstStall),
    window_ms: ms(performance.now() - r.t0),
  };
  try {
    navigator.sendBeacon(ENDPOINT, new Blob([JSON.stringify(body)], { type: "application/json" }));
  } catch (_err) {
    // a lost report is fine
  }
}

// Another view inside the app: end the report so far, which belongs to the
// view it started on, and time the new one from here. HA fires
// location-changed for its own navigation; popstate covers back and forward.
// Both can fire for one move, so only an actual change of path counts.
// HA's own redirects (/ to the default dashboard, a dashboard to its first
// view) navigate with replace: those are the same open still arriving, so the
// report carries on under the new path rather than ending empty.
let currentPath = location.pathname;
function viewChanged(ev) {
  const path = location.pathname;
  if (path === currentPath) return;
  currentPath = path;
  if (document.visibilityState !== "visible") return;
  if (ev.type === "location-changed" && ev.detail?.replace && report) {
    report.path = path.slice(0, 200);
    return;
  }
  start("navigate", performance.now());
}
window.addEventListener("location-changed", viewChanged);
window.addEventListener("popstate", viewChanged);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    hiddenAt = performance.now();
    finish(); // send what this report has; a backgrounded page may never run again
  } else if (!loadReported) {
    // Opened in the background: its first time on screen is its load.
    loadReported = true;
    start("load", performance.now());
  } else if (hiddenAt !== null && performance.now() - hiddenAt > RESUME_AFTER_MS) {
    start("resume", performance.now());
  }
});

if (document.visibilityState === "visible") {
  loadReported = true;
  start("load", 0);
}
