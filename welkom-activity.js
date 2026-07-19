// Registers "actively using" pings for the device viewing this dashboard.
//
// Welkom counts these pings (and only these) as current-device usage, so the
// gating here defines the semantic. Two kinds of ping:
//
//  - CLAIM (/welkom/claim): sent on load, on foregrounding, and while the
//    user is interacting (touch, scroll, hover, keys). Welkom lets claims
//    take the person's current-device slot from any other device.
//  - SUSTAIN (/welkom/sustain): sent while the dashboard is merely on screen
//    without recent interaction. Welkom only lets sustains refresh a claim
//    this device already holds (or take a vacant slot) — never steal one.
//    So an untouched HA window on a desk, or a wall tablet, stays current
//    without ever out-competing the phone in the person's hand.
//
// The endpoints are registered by the integration itself: natural frontend
// URLs (like /manifest.json) are also fetched by clients in the background,
// which made background companion apps look like interactive use.
//
// Both are scheduled through requestAnimationFrame, which only fires while
// the page is actually being rendered: hidden tabs, minimized or occluded
// windows, and sleeping/locked displays stop painting — even though timers
// and background fetches (camera streams, auto-refreshing cards) keep
// running — so nothing pings from a screen nobody could be looking at.
//
// Each ping traverses the reverse proxy's forward auth (registering activity
// for this device), then calls the welkom.refresh service so the integration
// picks the change up within seconds instead of on its next poll.

const PING_INTERVAL = 60000; // sustain cadence; keeps welkom's ttl alive while on screen
const CLAIM_GAP = 20000; // min gap between claims while interacting (matches welkom's write throttle)
const INTERACTION_WINDOW = 70000; // input within this window makes the next ping a claim
const TICK_INTERVAL = 5000;
const INTERACTION_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"];

let lastInteraction = Date.now(); // loading the page counts as an interaction
let lastPing = 0;
let lastClaim = 0;
let pending = false;

function refresh() {
  const hass = document.querySelector("home-assistant")?.hass;
  hass?.callService?.("welkom", "refresh");
}

function ping(claim) {
  const now = Date.now();
  lastPing = now;
  if (claim) {
    lastClaim = now;
  }
  fetch(`${claim ? "/welkom/claim" : "/welkom/sustain"}?welkom=${now}`, { cache: "no-store" })
    .then(() => refresh())
    .catch(() => {});
}

function tick() {
  if (pending) return;
  pending = true;
  // The callback stays queued while nothing is painted and runs when
  // rendering resumes — so pings fire exactly while the page is on screen.
  requestAnimationFrame(() => {
    pending = false;
    const now = Date.now();
    const interactive = now - lastInteraction < INTERACTION_WINDOW;
    if (interactive && now - lastClaim >= CLAIM_GAP) {
      ping(true);
    } else if (!interactive && now - lastPing >= PING_INTERVAL) {
      ping(false);
    }
  });
}

function onInteraction() {
  lastInteraction = Date.now();
  tick();
}

for (const event of INTERACTION_EVENTS) {
  window.addEventListener(event, onInteraction, { passive: true, capture: true });
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    onInteraction(); // foregrounding implies the user is looking
  }
});

setInterval(tick, TICK_INTERVAL);
tick();
