// Registers "actively using" pings for the device viewing this dashboard.
//
// Welkom counts these pings (and only these) as current-device usage, so the
// gating here defines the semantic. Pings are scheduled through
// requestAnimationFrame, which only fires while the page is actually being
// rendered: hidden tabs, minimized or occluded windows, and sleeping/locked
// displays stop painting — even though timers and background fetches (camera
// streams, auto-refreshing cards) keep running. A painted frame is the most
// truthful "this dashboard is on screen" signal, and needs no interaction,
// so passively watched displays (wall tablets) stay current.
//
// Each ping fetches a cheap same-origin URL so the request traverses the
// reverse proxy's forward auth and registers activity for this device, then
// calls the welkom.refresh service so the integration picks the fresh
// activity up immediately instead of on its next poll.

const PING_INTERVAL = 60000; // min gap between pings; keeps welkom's ttl alive while on screen
const TICK_INTERVAL = 5000;

let lastPing = 0;
let pending = false;

function refresh() {
  const hass = document.querySelector("home-assistant")?.hass;
  hass?.callService?.("welkom", "refresh");
}

function ping() {
  lastPing = Date.now();
  fetch(`/manifest.json?welkom=${lastPing}`, { cache: "no-store" })
    .then(() => refresh())
    .catch(() => {});
}

function tick() {
  if (pending) return;
  pending = true;
  // The callback stays queued while nothing is painted and runs when
  // rendering resumes — so the ping fires exactly when the page returns
  // to screen, and never while it's off screen.
  requestAnimationFrame(() => {
    pending = false;
    if (Date.now() - lastPing >= PING_INTERVAL) {
      ping();
    }
  });
}

setInterval(tick, TICK_INTERVAL);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    tick();
  }
});

tick();
