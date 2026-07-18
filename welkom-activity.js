// Keeps welkom's "current device" fresh for the device viewing this dashboard.
//
// Loading a dashboard doesn't guarantee HTTP traffic (the frontend rides a
// long-lived websocket), so welkom's forward-auth-based activity tracking can
// miss that this device is actively viewing Home Assistant. On load and while
// visible, this module:
//  1. pings a cheap same-origin URL so the request traverses the reverse
//     proxy's forward auth and registers activity for this device, and
//  2. calls the welkom.refresh service so the integration picks the fresh
//     activity up immediately instead of on its next poll.

const PING_INTERVAL = 60000; // keep activity alive while visible; must stay under welkom's ttl

function refresh() {
  const hass = document.querySelector("home-assistant")?.hass;
  hass?.callService?.("welkom", "refresh");
}

function ping() {
  fetch(`/manifest.json?welkom=${Date.now()}`, { cache: "no-store" })
    .then(() => refresh())
    .catch(() => {});
}

let timer = null;

function start() {
  if (timer) return;
  ping();
  timer = setInterval(ping, PING_INTERVAL);
}

function stop() {
  clearInterval(timer);
  timer = null;
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    start();
  } else {
    stop();
  }
});

if (document.visibilityState === "visible") {
  start();
}
