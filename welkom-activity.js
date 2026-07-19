// Registers "actively using" pings for the device viewing this dashboard.
//
// Welkom counts these pings (and only these) as current-device usage, so the
// gating here defines the semantic. Two kinds of ping:
//
//  - CLAIM (/welkom/claim): sent while the user is actually providing input
//    (touch, scroll, hover, keys). Welkom lets claims take the person's
//    current-device slot from any other device. Nothing else counts as
//    interaction: page loads, foregrounding, and display wakes happen
//    without a human (app reloads, screen wake, window un-occlusion), and
//    treating them as interaction let idle machines steal the slot.
//  - SUSTAIN (/welkom/sustain): sent while the dashboard is merely on
//    screen. Welkom only lets sustains refresh a claim this device already
//    holds (or take a vacant slot) — never steal one. So an untouched HA
//    window on a desk, or a wall tablet, stays current without ever
//    out-competing the phone in the person's hand.
//
// Both are scheduled through requestAnimationFrame, which only fires while
// the page is actually being rendered: hidden tabs, minimized or occluded
// windows, and sleeping/locked displays stop painting — even though timers
// and background fetches (camera streams, auto-refreshing cards) keep
// running — so nothing pings from a screen nobody could be looking at.
//
// The endpoints are served by the integration itself; the ping traverses the
// reverse proxy's forward auth, whose identity headers let the integration
// update the current-device sensor in the same round trip.

const PING_INTERVAL = 60000; // sustain cadence; keeps welkom's ttl alive while on screen
const CLAIM_GAP = 20000; // min gap between claims while interacting (matches welkom's write throttle)
const INTERACTION_WINDOW = 70000; // input within this window makes the next ping a claim
const TICK_INTERVAL = 5000;
const SUSTAIN_FRAMES = 5; // frames a sustain needs within SUSTAIN_FRAMES * 100ms (>= 10fps)
// No "scroll": it also fires for programmatic scrolls (live cards adjusting
// layout), which would count as interaction on an untouched screen. Human
// scrolling is covered by wheel, touchstart, and pointer events.
const INTERACTION_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"];

let lastInteraction = 0;
let lastPing = 0;
let lastClaim = 0;
let lastEligible = 0;
let pending = false;

function ping(claim) {
  const now = Date.now();
  lastPing = now;
  if (claim) {
    lastClaim = now;
  }
  fetch(`${claim ? "/welkom/claim" : "/welkom/sustain"}?welkom=${now}`, { cache: "no-store" }).catch(() => {});
}

function tick() {
  if (pending) return;
  pending = true;
  // The callback stays queued while nothing is painted and runs when
  // rendering resumes — so pings fire exactly while the page is on screen.
  requestAnimationFrame(() => {
    const now = Date.now();
    const interactive = now - lastInteraction < INTERACTION_WINDOW;
    if (interactive && now - lastClaim >= CLAIM_GAP) {
      pending = false;
      ping(true);
    } else if (!interactive && now - lastPing >= PING_INTERVAL) {
      // Sustains additionally require display-rate rendering: occluded and
      // screen-off webviews may keep a *throttled* rAF alive, but only a
      // genuinely displayed page delivers a quick burst of frames.
      const start = performance.now();
      let frames = 0;
      const step = () => {
        frames += 1;
        if (frames >= SUSTAIN_FRAMES) {
          pending = false;
          if (performance.now() - start < SUSTAIN_FRAMES * 100) {
            // Require CONTINUOUS display-rate rendering — the previous
            // minute-cadence check must also have passed. A screen-off
            // webview can render in brief isolated bursts, and a single
            // fast window shouldn't let it take a vacant slot.
            if (now - lastEligible <= PING_INTERVAL * 2.5) {
              ping(false);
            }
            lastEligible = now;
          } else {
            lastEligible = 0;
          }
          return;
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    } else {
      pending = false;
    }
  });
}

function onInteraction(event) {
  // Runs at input-event rate (up to ~120Hz while scrolling) on the UI
  // thread, so it must stay trivially cheap: only escalate to tick() when
  // a claim could actually be due. Synthetic events (card libraries
  // dispatching pointer/wheel events programmatically) don't count.
  if (!event.isTrusted) return;
  const now = Date.now();
  lastInteraction = now;
  if (now - lastClaim >= CLAIM_GAP) {
    tick();
  }
}

for (const event of INTERACTION_EVENTS) {
  window.addEventListener(event, onInteraction, { passive: true, capture: true });
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    tick(); // resume pinging, but becoming visible is not interaction
  }
});

setInterval(tick, TICK_INTERVAL);
tick();
