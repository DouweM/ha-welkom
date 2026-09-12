# Welkom for Home Assistant

Home Assistant integration for [welkom](https://github.com/DouweM/welcome), the identity-aware forward-auth and presence layer for a Traefik-fronted homelab. Because every device on the network (and every request into it) passes through welkom, Home Assistant gets room-level presence and per-device connection state **without any app installed on the tracked devices**.

## Entities

**Per person** (added automatically as people appear in welkom's config):

- `device_tracker.<person>` — where they are: a room of the main home, `home`, another home (`"Cabin: Kitchen"`), or `not_home`. Maps onto HA zones matching those names (`in_zones`, coordinates), so people show up on the map and count in zones. With a phone tracker configured for the person (see [Phone GPS](#phone-gps)) it also carries the phone's position while they're out — the one tracker to link to `person.<name>`.
- `binary_sensor.<person>` — presence in the main home.
- `sensor.<person>_trip` — what they are doing while they are out: `home`, `travelling`, or `settled` once they have stopped somewhere. Only for people with a phone tracker configured (see [Trips](#trips)). Attributes: `left_at`, `settled_at`, `place` (the zone they are in, if any), `latitude`/`longitude`, `distance` and `furthest` (metres from home), `turn` (the zone at the furthest point), `places` and `been_to` (everywhere they stopped, in order), `seen_at` and `stale`.
- `sensor.<person>_current_device` — the device they are *actively using* right now (e.g. `Douwe's phone`), driven by welkom's activity tracking of forward-auth requests to configured services (Home Assistant itself, typically). Expires to `unknown` after welkom's `ttl` (default 2 minutes) of inactivity. Attributes: `device_type`, `network_id`, `role_id`, `host`, `room`, `last_seen_at`, `connection_summary` (welkom's concise connection description), plus the connection metadata (ip, wifi ssid, user agent summary, ...).

**Per device** (each known tracker/personal device, added as it connects):

- `sensor.<device>_connection` — the network the device is connected through (`residents`, `tailscale`, ...), or `unknown` when offline. When a device is connected via multiple networks at once, the primary is chosen online-first, then highest role, then most recently seen. Attributes: ip, mac, wifi ssid, online, last seen, home/room, person, and all active networks.

**Per home and room**: people count, known/unknown people counts, and comma-joined name lists — six sensors each, attached to devices suggested into matching HA areas.

Ten fixed `Unknown Person N` tracker slots cover unrecognized personal devices.

### Phone GPS

Welkom only knows the network, so on its own a person is either in a room or `not_home`. Give the integration the person's phone device tracker — per person under the integration's **Configure** options, or as `attrs.homeassistant.gps_tracker: <device_tracker object id>` on the person in welkom.yml (the option wins) — and `device_tracker.<person>` becomes the merge of both:

- **Home:** welkom's room, with the room zone's coordinates and `in_zones` (room, home, and whatever encloses the home).
- **Out:** the phone's position, accuracy and zones, like any GPS tracker.
- **At the edges, the phone wins.** Welkom keeps seeing a phone that's still associated to the garden access point from the street, and a controller takes a couple of minutes to age out a client after the person drove off; both read as "home" for a while. A *fresh* fix (under 15 minutes old) clearly outside the home's zone overrules that placement. A stale fix doesn't: the phone may be dead or left behind while its owner is home with another device.
- **The handover is hysteretic.** Once the phone has taken over, welkom gets the person back only on a fix that is *entirely* inside the home zone, accuracy disc and all. A blurry fix from down the street overlaps a house-sized zone easily, and letting that hand the person back plants them in a room they are nowhere near, for as long as it takes the next fix to arrive.
- **Brief dropouts hold the room.** An idle phone goes quiet on WiFi for minutes at a time and the controller ages it out, so welkom loses a person who hasn't moved. The tracker keeps welkom's last room for up to 5 minutes as long as the phone's fix stays within the home (`held: true` while it does), instead of dropping to a bare `home`. Only for a person who never left in the meantime: once the phone has taken charge, the room they were last seen in is history, not a place to snap back to.
- **Welkom outage:** the tracker stays available, holding welkom's last placement while the phone's fix remains within the home and following the phone otherwise. (Without a phone it goes `unavailable`, so `person.*` holds its last state.)

Link **only** this tracker to the Home Assistant person. Listing the phone tracker alongside it makes HA's person entity pick whichever wrote last, which is the race this merge exists to end. `binary_sensor.<person>` stays welkom's pure network view; the `source` attribute on the tracker says whether `welkom` or `gps` is speaking, and `held` whether welkom's placement is being kept through a dropout.

### Trips

Once a person's phone is carrying their position, `sensor.<person>_trip` follows what they do with it. A trip starts when the phone takes over from welkom and ends when welkom has them back; in between the sensor says whether they are still moving or have stopped, and collects everywhere they stopped along the way.

A destination is not a position — somebody in a taxi and somebody at dinner report the same fix — so stops are found by **anchoring**: a fix that lands near the last one extends a stay, one that lands away starts a new anchor, and an anchor that holds for 5 minutes is somewhere they went. Time counts even while the phone is silent, because a phone that has arrived stops reporting; waiting for a confirming fix would mean never noticing an arrival until the person left again.

A few rules fall out of that, each of which exists because the naive version gets a real journey wrong:

- **Silence is checked afterwards.** A car on the motorway goes just as quiet as a parked one. When the phone speaks again, the distance it covered over the silence says which it was: a stay somebody left at more than walking pace was the road, and is discarded.
- **Zones name places; they don't decide stillness.** A stop inside a hand-drawn zone is reported by name, wandering within the zone is one visit, and the zone is exempt from the distance floor below. But a zone never lowers the bar for holding still, or minutes of driving through a wide one would read as an evening in it. Zones are matched by *name*, so several overlapping zones called the same thing are one place, and the smallest match wins. Zones bigger than 5 km are ignored — one drawn around a city contains every trip there is.
- **The turn counts as a destination.** Some journeys have no stay at all: a school run pulls up, the kid gets out, and it drives away. So the named zone holding the furthest fix of the trip is reported too, however briefly they were in it.
- **Near the house doesn't count** unless it has a name. The return leg of every trip passes the same ground as the start, so an unnamed stop within 250 m of home is the doorstep — but a *named* one that near is somewhere they chose to be.
- **A trip under 2 minutes never happened.** One bad fix can hand the phone control for a second or two.

Nothing here is named beyond the zone: an unnamed stop carries only coordinates, and turning that into "La Roma, CDMX" needs a geocoder this integration has no business owning. Compose the sentence from `been_to` in a template or an automation, where the words belong.

Room zones in HA are best made **passive** (a few metres around each room's spot, inside the home zone): the tracker names rooms from welkom's placement, and passive zones keep a phone's jittery GPS from ever claiming one.

### Freshness

The integration bundles a small frontend script (registered automatically) that keeps the current-device sensor fresh — including in the companion app's web view. While a dashboard is on screen it pings `/welkom/claim` or `/welkom/sustain` (served by the integration itself) once a minute. The ping traverses the reverse proxy's forward auth, so it arrives carrying welkom's `X-Welcome-*` identity headers — and the integration applies those to the sensor **in the same round trip**, no poll or extra request needed; welkom records the same ping authoritatively and the regular 30-second poll reconciles.

Pings are scheduled through `requestAnimationFrame`, so they only fire while the page is *actually being rendered*: hidden tabs, minimized or occluded windows, and sleeping or locked displays stop painting — and therefore stop pinging — even though background traffic (camera streams, auto-refreshing cards, companion-app polling) keeps flowing.

There are two kinds of ping, matching welkom's `services`/`sustain` config: real input (touch, scroll, hover, keys) sends **claims**, which take the person's current-device slot from any other device; on-screen dashboards without recent input send **sustains**, which keep a claim this device already holds alive (or take a vacant slot) but never steal one. On desktops and tablets, page loads, foregrounding, and display wakes deliberately do *not* count as interaction — they happen without a human (app reloads, screen wake, window un-occlusion), and would let idle machines claim the slot. Phones are the exception: an app doesn't come to the foreground without a hand on the device, so opening or switching to the app claims immediately, no scroll required. So the phone in your hand always wins, while an untouched HA window on a desk or a wall tablet stays current only when nothing else is actively used.

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

## Pictures

Welkom can host the household's pictures itself — a person's avatar, the home's, and a home's `images` (a drone shot of the house, say) as files next to its config — and the integration serves them from Home Assistant's own origin: `/welkom/people/<id>/avatar`, `/welkom/homes/<id>/avatar`, `/welkom/homes/<id>/images/<name>`. Entity pictures point there, so they load on whatever host the dashboard was opened on (the LAN name, a public one, the companion app) without the browser ever needing to reach welkom or be identified by it. Pictures welkom merely links to (Gravatar, an image host) are passed through as-is.

A home image with map `bounds` can be laid over any [ha-map-card](https://github.com/nathan-gs/ha-map-card) with the bundled plugin — the file and its corners stay in welkom, the dashboard only names which image it wants:

```yaml
type: custom:map-card
plugins:
- name: aerial
  url: /welkom/map-image.js
  options:
    home: home          # welkom home id
    image: aerial       # key under the home's `images`; defaults to "aerial"
    opacity: 1          # optional
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

## Auth routing

Because every request into the homelab already carries welkom's identity as
`X-Welcome-*` headers, Home Assistant can log people in as themselves without a
password prompt. Enable **auth routing** in the integration's options and map
welkom people and roles to Home Assistant users:

1. Settings → Devices & services → **Welkom** → **Configure**.
2. Turn on *Enable auth routing*, pick a *Default user*, then map each welkom
   **person** and **role** to a Home Assistant user.

Each request resolves in order: the person's mapped user → the role's mapped
user → the default user. So a recognized person logs into their own account, an
unrecognized person on a known network logs into the shared account for their
role, and anyone else lands on the default — nobody gets bounced to a login
loop. The Home Assistant users must already exist; auth routing never creates
them. (Role targets are best pointed at *shared* accounts — a downgraded person,
see below, falls back to one.)

### Not identifying people on low-trust networks

welkom identifies people partly from device MAC addresses, which can be spoofed
on a network you can already join. To stop that from escalating privilege,
*Only identify people at their full role* (on by default) honors the person map
only when the request's role equals the person's **assigned** role in welkom —
i.e. only on a network whose `max` role grants them their full role. On a
lower-trust network welkom caps the role below the person's assigned role, the
person is treated as un-trusted there, and resolution falls back to the role
account.

So someone assigned `admin` is auto-logged into their own (owner) account only
from a network trusted to `admin` (e.g. Tailscale, where identity is a
cryptographic node, not a MAC) — never from a residents/guest VLAN capped at a
lower role, where a spoofed MAC would otherwise impersonate them. The assigned
role is read from welkom's `/api/people` (via the coordinator), not from a
request header, so no forge-able input decides trust; if it can't be determined
the person is treated as un-trusted (fail closed).

How it works: enabling the option injects a Home Assistant auth provider that
reads the welkom headers and resolves the mapped user. Home Assistant only
trusts those headers from a configured `http.trusted_proxies` address, so the
reverse proxy (and only it) can assert identity — a request that reaches Home
Assistant directly falls through to normal login. Mapping changes take effect
immediately; toggling the feature *off* fully takes effect on the next restart.
