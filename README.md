# Welkom for Home Assistant

Home Assistant integration for [welkom](https://github.com/DouweM/welcome), the identity-aware forward-auth and presence layer for a Traefik-fronted homelab. Because every device on the network (and every request into it) passes through welkom, Home Assistant gets room-level presence and per-device connection state **without any app installed on the tracked devices**.

## Entities

**Per person** (added automatically as people appear in welkom's config):

- `device_tracker.<person>` — where they are: a room of the main home, `home`, another home (`"Cabin: Kitchen"`), or `not_home`. Maps onto HA zones matching those names, so people show up on the map.
- `binary_sensor.<person>` — presence in the main home.
- `sensor.<person>_current_device` — the device they are *actively using* right now (e.g. `Douwe's phone`), driven by welkom's activity tracking of forward-auth requests to configured services (Home Assistant itself, typically). Expires to `unknown` after welkom's `ttl` (default 2 minutes) of inactivity. Attributes: `device_type`, `network_id`, `role_id`, `host`, `room`, `last_seen_at`, plus the connection metadata (ip, wifi ssid, user agent summary, ...).

**Per device** (each known tracker/personal device, added as it connects):

- `sensor.<device>_connection` — the network the device is connected through (`residents`, `tailscale`, ...), or `unknown` when offline. When a device is connected via multiple networks at once, the primary is chosen online-first, then highest role, then most recently seen. Attributes: ip, mac, wifi ssid, online, last seen, home/room, person, and all active networks.

**Per home and room**: people count, known/unknown people counts, and comma-joined name lists — six sensors each, attached to devices suggested into matching HA areas.

Ten fixed `Unknown Person N` tracker slots cover unrecognized personal devices.

### Freshness

The integration bundles a small frontend script (registered automatically) that keeps the current-device sensor fresh: on dashboard load, on foregrounding, and while in active use — including in the companion app's web view — it pings a same-origin URL so the viewing device registers forward-auth activity with welkom, then calls the `welkom.refresh` service so the sensor updates within seconds instead of on the next 30-second poll.

Pings continue (once a minute) only while the page is visible **and** the user has interacted with it in the last ~2.5 minutes — visibility alone isn't trustworthy, since an HA window left open on an idle computer reports as visible and would otherwise claim the current device forever. The flip side: a passively watched display (wall tablet) expires to `unknown` unless it's touched now and then.

### In automations and templates

```yaml
# Is Douwe looking at HA on their phone right now?
{{ states('sensor.douwe_current_device') == "Douwe's phone" }}

# Is the phone on Tailscale (i.e. away but connected)?
{{ states('sensor.douwe_s_phone_connection') == 'tailscale' }}

# "Currently using HA from any device" (state is unknown when expired):
{{ states('sensor.douwe_current_device') not in ['unknown', 'unavailable'] }}
```

## Installation

1. Add this repository to HACS as a custom repository (type: integration) and install **Welkom**, or copy the files into `custom_components/welkom/`.
2. Restart Home Assistant.
3. Add the **Welkom** integration: give it an id, the base URL of your welkom server, and optionally a `home_id` if welkom knows several homes.

## Requirements

- A running [welkom](https://github.com/DouweM/welcome) server.
- The role welkom assigns to your Home Assistant server must have the `api`, `home_people`, and `home_devices` features.
- For the current-device sensor, welkom needs `activity.current_device.services` configured. Recommended: count only the bundled frontend script's beacon — `GET https://<your-ha-host>/manifest.json` — since it only fires while a dashboard is actually visible. Broad host patterns also count background traffic (camera streams, auto-refreshing cards, companion-app polling), which lets idle devices claim the current device.

The integration polls welkom every 30 seconds.
