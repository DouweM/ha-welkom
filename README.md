# Welkom for Home Assistant

Home Assistant integration for [welkom](https://github.com/DouweM/welcome), the identity-aware forward-auth and presence layer for a Traefik-fronted homelab. Because every device on the network (and every request into it) passes through welkom, Home Assistant gets room-level presence and per-device connection state **without any app installed on the tracked devices**.

## Entities

**Per person** (added automatically as people appear in welkom's config):

- `device_tracker.<person>` — where they are: a room of the main home, `home`, another home (`"Cabin: Kitchen"`), or `not_home`. Maps onto HA zones matching those names, so people show up on the map.
- `binary_sensor.<person>` — presence in the main home.
- `sensor.<person>_current_device` — the device they are *actively using* right now (e.g. `Douwe's phone`), driven by welkom's activity tracking of forward-auth requests to configured services (Home Assistant itself, typically). Expires to `unknown` after welkom's `ttl` (default 2 minutes) of inactivity. Attributes: `device_type`, `network_id`, `role_id`, `host`, `room`, `last_seen_at`, `connection_summary` (welkom's concise connection description), plus the connection metadata (ip, wifi ssid, user agent summary, ...).

**Per device** (each known tracker/personal device, added as it connects):

- `sensor.<device>_connection` — the network the device is connected through (`residents`, `tailscale`, ...), or `unknown` when offline. When a device is connected via multiple networks at once, the primary is chosen online-first, then highest role, then most recently seen. Attributes: ip, mac, wifi ssid, online, last seen, home/room, person, and all active networks.

**Per home and room**: people count, known/unknown people counts, and comma-joined name lists — six sensors each, attached to devices suggested into matching HA areas.

Ten fixed `Unknown Person N` tracker slots cover unrecognized personal devices.

### Freshness

The integration bundles a small frontend script (registered automatically) that keeps the current-device sensor fresh — including in the companion app's web view. While a dashboard is on screen it pings `/welkom/claim` or `/welkom/sustain` (served by the integration itself) once a minute. The ping traverses the reverse proxy's forward auth, so it arrives carrying welkom's `X-Welcome-*` identity headers — and the integration applies those to the sensor **in the same round trip**, no poll or extra request needed; welkom records the same ping authoritatively and the regular 30-second poll reconciles.

Pings are scheduled through `requestAnimationFrame`, so they only fire while the page is *actually being rendered*: hidden tabs, minimized or occluded windows, and sleeping or locked displays stop painting — and therefore stop pinging — even though background traffic (camera streams, auto-refreshing cards, companion-app polling) keeps flowing.

There are two kinds of ping, matching welkom's `services`/`sustain` config: real input (touch, scroll, hover, keys) sends **claims**, which take the person's current-device slot from any other device; on-screen dashboards without recent input send **sustains**, which keep a claim this device already holds alive (or take a vacant slot) but never steal one. Page loads, foregrounding, and display wakes deliberately do *not* count as interaction — they happen without a human (app reloads, screen wake, window un-occlusion), and would let idle machines claim the slot. So the phone in your hand always wins, while an untouched HA window on a desk or a wall tablet stays current only when nothing else is actively used.

### Device suspension

Frontend gating can't catch everything: a sleeping Mac's web view may keep rendering (and even see input events) with the screen off. When Home Assistant *knows* a device isn't in use — the companion app's `Active` binary sensor is `off` — align welkom with reality via the `welkom.set_device_suspended` service. While suspended, welkom ignores the device's claims and sustains and releases any slot it holds, whatever its traffic looks like:

```yaml
automation:
  - alias: "Welkom: suspend sleeping MacBook"
    triggers:
      - trigger: state
        entity_id: binary_sensor.douwe_s_macbook_pro_active
    actions:
      - action: welkom.set_device_suspended
        data:
          device: "Douwe's MBP"
          suspended: "{{ trigger.to_state.state != 'on' }}"
```

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
- For the current-device sensor, welkom needs `activity.current_device` configured to count only the bundled frontend script's beacons — broad host patterns also count background traffic (camera streams, auto-refreshing cards, companion-app polling), which lets idle devices claim the current device:

  ```yaml
  current_device:
    services: ['GET https://<your-ha-host>/welkom/claim']    # interaction claims
    sustain: ['GET https://<your-ha-host>/welkom/sustain']   # on-screen keeps alive
  ```

  The `/welkom/claim` and `/welkom/sustain` endpoints are served by this integration; only the bundled script ever fetches them. (Natural frontend URLs like `/manifest.json` are also fetched by companion apps in the background, which would make idle devices look interactive.)

The integration polls welkom every 30 seconds.
