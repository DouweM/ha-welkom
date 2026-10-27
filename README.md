# Welkom for Home Assistant

Home Assistant integration for [welkom](https://github.com/DouweM/welcome), the identity-aware forward-auth and presence layer for a Traefik-fronted homelab. Because every device on the network (and every request into it) passes through welkom, Home Assistant gets room-level presence and per-device connection state **without any app installed on the tracked devices**.

## Entities

**Per person** (added automatically as people appear in welkom's config):

- `device_tracker.<person>` — where they are: a room of the main home, `home`, another home (`"Cabin: Kitchen"`), or `not_home`. Maps onto HA zones matching those names (`in_zones`, coordinates), so people show up on the map and count in zones. With a phone tracker configured for the person (see [Phone GPS](#phone-gps)) it also carries the phone's position while they're out — the one tracker to link to `person.<name>`.
- `binary_sensor.<person>` — presence in the main home. Also who the person is, for anything that has to turn another system's id back into them: `person_id`, `person` (the display name), `handles` and `duties` from `attrs.homeassistant` on the person in welkom.yml (see [Handles](#handles)), and `trip`, the entity id of their trip sensor if they have one. `presence` says what being home rests on (`network` or `door`) and `last_presence` keeps it once they're gone. `last_heard` is when any of their tracker devices (phones, wearables, handhelds: what presence rests on, never a laptop) was last heard at the home, kept the same way once it is off, and left out of the recorder since it moves every poll or two. The sensor turns off five to nine minutes after that (welkom's two-minute online window plus its presence hold), so date a departure from `last_heard` and decide it from the off.
- `sensor.<person>_trip` — what they are doing while they are out: `home`, `travelling`, or `settled` once they have stopped somewhere. Only for people with a phone tracker configured (see [Trips](#trips)). Attributes: `left_at`, `settled_at`, `place` (the zone they are in, if any), `latitude`/`longitude`, `distance` and `furthest` (metres from home), `turn` (the zone at the furthest point), `places` and `been_to` (everywhere they stopped, in order), `seen_at` and `stale`. While `home` it still carries the *last* trip's `left_at`, `returned_at`, `furthest`, `places` and `been_to` (until the next restart), because the moment anybody wants to say where somebody has been is the moment they walk back in.
- `sensor.<person>_current_device` — the device they are *actively using* right now (e.g. `Douwe's phone`), driven by welkom's activity tracking of forward-auth requests to configured services (Home Assistant itself, typically). Expires to `unknown` after welkom's `ttl` (default 2 minutes) of inactivity. Attributes: `device_type`, `network_id`, `role_id`, `host`, `room`, `last_seen_at`, `connection_summary` (welkom's concise connection description), plus the connection metadata (ip, wifi ssid, user agent summary, ...).

**Per device** (each known tracker/personal device, added as it connects):

- `sensor.<device>_connection` — the network the device is connected through (`residents`, `tailscale`, ...), or `unknown` when offline. When a device is connected via multiple networks at once, the primary is chosen online-first, then highest role, then most recently seen. Attributes: ip, mac, wifi ssid, online, last seen, home/room, person, and all active networks.

**Per roaming home** (see [Roaming homes](#roaming-homes)):

- `device_tracker.<home>` — where a travel router is, borrowed from the phones on it; `unknown` while nobody has placed it. Attributes: `uplink` and `ssid` (what it is plugged into), `uplink_since`, `dark_since`, `located_at`, `located_by`, `people`.

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

While the phone is in charge, the tracker's state is the tightest place that holds — a zone, or one of the household's own [places](#places) — so `person.*` can read "Roma" without a zone being drawn. Nothing recognised is `not_home`. Except at home, meaning `zone.home`: for the minute or two between the phone walking in the door and welkom seeing it on the WiFi (or after a dropout longer than the room hold), the state is `home`, without a room, rather than the neighbourhood the house is in. That is hysteretic too: the phone brings them home on a fix *entirely* inside the zone, and keeps them there while a fix may still be inside, since a phone indoors often reports an accuracy wider than the zone. `source` stays `gps` until welkom has them, so "out" is `source: gps` and a state other than `home`. A roaming home (a travel router) has no zone and is not covered: there, only welkom places them.

Link **only** this tracker to the Home Assistant person. Listing the phone tracker alongside it makes HA's person entity pick whichever wrote last, which is the race this merge exists to end. `binary_sensor.<person>` stays welkom's pure network view; the `source` attribute on the tracker says whether `welkom` or `gps` is speaking, and `held` whether welkom's placement is being kept through a dropout.

### Trips

Once a person's phone is carrying their position, `sensor.<person>_trip` follows what they do with it. A trip starts when the phone takes over from welkom and ends when welkom has them back; in between the sensor says whether they are still moving or have stopped, and collects everywhere they stopped along the way.

A destination is not a position — somebody in a taxi and somebody at dinner report the same fix — so stops are found by **anchoring**: a fix that lands near the last one extends a stay, one that lands away starts a new anchor, and an anchor that holds for 5 minutes is somewhere they went. Time counts even while the phone is silent, because a phone that has arrived stops reporting; waiting for a confirming fix would mean never noticing an arrival until the person left again.

A few rules fall out of that, each of which exists because the naive version gets a real journey wrong:

- **Silence is checked afterwards.** A car on the motorway goes just as quiet as a parked one. When the phone speaks again, the distance it covered over the silence says which it was: a stay somebody left at more than walking pace was the road, and is discarded.
- **Zones name places; they don't decide stillness.** A stop inside a hand-drawn zone is reported by name, wandering within the zone is one visit, and the zone is exempt from the distance floor below. But a zone never lowers the bar for holding still, or minutes of driving through a wide one would read as an evening in it. Zones are matched by *name*, so several overlapping zones called the same thing are one place, and the smallest match wins. Zones bigger than 5 km are ignored — one drawn around a city contains every trip there is.
- **A zone crossed at speed is the road.** Sitting inside a named zone for five minutes is being there; crossing one in five minutes is driving through it, and the same neighbourhood can be either on the same afternoon. A *visit* — time inside a zone the anchor never settled in — counts only at walking pace or slower, measured as net displacement over its length. Without it a kilometre-wide neighbourhood is reported by every car that passes through.
- **The turn counts as a destination.** Some journeys have no stay at all: a school run pulls up, the kid gets out, and it drives away. So the named zone holding the furthest fix of the trip is reported too, however briefly they were in it.
- **Near the house doesn't count** unless it has a name. The return leg of every trip passes the same ground as the start, so an unnamed stop within 250 m of home is the doorstep — but a *named* one that near is somewhere they chose to be.
- **The neighbourhood the house is in is never a stop.** Every trip from home starts and ends in it, so a drive back through it, a wait at the gate or an errand that never leaves it says nothing. A place counts as the house's when the house itself passes its test: a zone whose circle holds the home zone's centre, a polygon around it, or a geocode matcher that holds for what a phone's geocoder said inside the home zone (from the gazetteer). Tighter places inside it, like a zone down the road, still count.
- **A trip under 2 minutes never happened.** One bad fix can hand the phone control for a second or two.

Nothing here is named beyond the zone: an unnamed stop carries only coordinates, and turning that into "La Roma, CDMX" needs a geocoder this integration has no business owning. Compose the sentence from `been_to` in a template or an automation, where the words belong.

Room zones in HA are best made **passive** (a few metres around each room's spot, inside the home zone): the tracker names rooms from welkom's placement, and passive zones keep a phone's jittery GPS from ever claiming one.

### Places

Zones are circles, and most places people mean are not: a park with three named sections, a neighbourhood, a town. The phone's reverse geocoder already knows those boundaries, so a household can name places by what the geocode says there, or by a hand-drawn polygon, in `attrs.homeassistant.places` on the home in welkom.yml:

```yaml
homes:
- id: oasis
  attrs:
    homeassistant:
      places:
      - name: Chapu II
        geocodes_as: {sub_locality: Bosque de Chapultepec II}
      - name: Roma
        geocodes_as: {sub_locality: [Roma Norte, Roma Sur]}
      - name: Satélite            # all keys must hold
        geocodes_as: {sub_locality: Ciudad Satélite, locality: Naucalpan de Juárez}
      - name: Polanco             # any of several matchers
        geocodes_as: [{sub_locality: Polanco}, {area_of_interest: Parque Lincoln}]
      - name: Molino
        polygon: [[19.4238, -99.2052], [19.4224, -99.2038], [19.4222, -99.2058]]
```

The geocode comes from the companion app's *Geocoded Location* sensor, found on the same device as the person's phone tracker; attributes are `area_of_interest`, `thoroughfare`, `sub_locality`, `postal_code`, `locality`, `sub_administrative_area`, `administrative_area`, `country`, matched case-insensitively. A geocode is only trusted when its own position agrees with the fix, so a sensor a poll behind can't name the street the phone just left.

Configured places take part in the same "tightest zone wins" rule as real zones. A polygon is worth the radius of the circle with its area; a geocode match is worth a nominal radius for the attribute (an area of interest is tighter than a sub-locality is tighter than a locality). So a 48 m school circle still beats the neighbourhood around it, and a real zone drawn smaller always wins. The name reaches three things at once: the tracker's state (and so `person.*` and any badge reading it), the trip sensor's stops and turns, and whatever composes sentences from either. Draw a small **passive** zone with the same name if you want an icon for it on the map; passive means the circle never claims anybody by geometry. (Its person count still follows the circle, not the name — HA counts by coordinates — so the marker is a picture, not a sensor.)

The state is only ever a household place, a zone or `not_home`, so dashboards can key on those names. The geocode's own words go in attributes instead, while `source` is `gps`: `colonia` is its sub-locality ("Lomas de Vista Hermosa"), and `alcaldia` the municipality, in Mexico only: the locality in Mexico City ("Cuajimalpa"), the sub-administrative area in a state ("Huixquilucan", Edo. Méx.), null elsewhere. Both come from the fix's own geocode, else the nearest remembered one (below), and are null when neither has them.

A phone doesn't geocode every fix, and in a car hardly any. Every geocode a tracker accepts is remembered against its position in a gazetteer shared by every phone (stored as `.storage/welkom.gazetteer`, one entry per ~25 m, at most 20,000, oldest out first, written at most a minute after the first unsaved one and never before the stored file has been read). It is seeded from the recorder's history of every `sensor.*_geocoded_location` until one seed has landed, which the file records. A fix without a geocode of its own may be named by the nearest remembered one, within its accuracy, between 150 m and 300 m, when that geocode matches a household place tighter than the zones; failing that, by the household place the phone last geocoded within 300 m.

### Roaming homes

A travel router is a home that moves. Mark it in welkom.yml and the integration places it by the phones connected to it:

```yaml
homes:
- id: awaysys
  resolver: glinet
  attrs:
    homeassistant:
      roaming: true
```

A fresh fix (under 15 minutes old, within 500 m) from the **phone** of somebody whose phone welkom sees on the router, taken since that phone joined it, is a fix on the router. Not a laptop's owner (a laptop left in the room keeps its owner "on" the router through dinner), and not a fix from before the phone (re)joined (the taxi, the café on the way back). Everybody on the router then stands at its position on the map (their tracker's state stays the home's name, with a `roaming: true` attribute), and `device_tracker.<home>` carries it on its own.

The position is held for exactly as long as the router stays plugged into the same thing — welkom's `GET /api/homes/<id>/uplink`, which for a GL.iNet is the WiFi it repeats, the cable, the tethered phone or the SIM:

- **Silence is not movement.** Phones on a router in a hotel room go quiet for hours overnight; while the router keeps repeating the same SSID, it is where it was.
- **A new uplink is.** A router cannot change hotels or board a plane without changing what it is plugged into, so the position is dropped then, until a phone on the new uplink reports. A fix taken before the router got there — the taxi from the airport, still the phone's latest as it joins — does not count.
- **Dark for 15 minutes is packed up.** A blip keeps the position; a router that stops answering for longer loses it, and the people last seen on it are released to their phones.
- **Walking out is not moving.** A phone stays on the router's client list for a minute or two after its owner leaves the room. So a fix more than WiFi range (100 m, plus both fixes' accuracy) from the router asks a question instead of moving it: if that same phone is still on the router 10 minutes later, the router went with it (a car on a SIM, a plane) and takes its latest fix; if it drops off first, it walked out. Fixes in range never move the router, only sharpen it, so short steps can't drag it along.

It survives a Home Assistant restart if the router is still on the uplink it was placed on. It needs welkom's uplink endpoint: against an older welkom the router is placed but never let go of, since neither a new uplink nor going dark can be seen.

To place it by hand — the hotel's address before anybody's phone has reported — call `welkom.locate` on `device_tracker.<home>` with `latitude`, `longitude` and optionally `accuracy`. It is held by the same rule, so it lasts until the router moves.

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

## Handles

Other systems know people by ids that mean nothing to anybody: a lock sees an access code, WhatsApp a long number. Put them on the person in welkom.yml and every `binary_sensor.<person>` carries them, so the household resolves a handle in one place instead of keeping a table in each automation:

```yaml
- id: alex
  attrs:
    homeassistant:
      handles:
        schlage: [Homekit-cd060a01]
        whatsapp: ["123456789012345"]
      duties: [driver, trash]
```

```jinja
{{ states.binary_sensor | selectattr('attributes.handles', 'defined')
   | selectattr('attributes.handles.schlage', 'defined')
   | selectattr('attributes.handles.schlage', 'contains', 'Homekit-cd060a01')
   | map(attribute='attributes.person') | first }}
```

The systems and duties are free-form strings; the integration passes them through untouched. Handles are always strings, so an unquoted all-digit id in YAML still matches. People are read when the integration starts, so after changing them in welkom.yml, restart welkom and then reload the integration.

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
user → the default user. So a recognized person logs into their own account, a
person welkom knows who has no account of their own logs into the shared account
for their role, and anyone else lands on the default — nobody gets bounced to a
login loop. The Home Assistant users must already exist; auth routing never creates
them. (Role targets are best pointed at *shared* accounts — a downgraded person,
see below, falls back to one.)

### Role accounts are for people welkom knows

Every device on a network gets at least that network's default role, including
one welkom has never seen: an unknown phone that joined the residents WiFi
arrives with the same role as a registered guest. *Only use role accounts for
known people* (on by default) honors the role map only when the request's person
is one welkom knows, read from `/api/people` like the assigned role below, or the
request comes from a known device that belongs to nobody (a shared TV, a
server). An unknown device's person id is its IP, so it skips the role map and
gets the default user, if any, or the login page. Turn it off to let the role
map serve anyone on the network.

New in v0.31.0 and on by default, so on an existing install unknown devices stop
getting role accounts on upgrade. Map a default user, or turn the option off, to
keep letting them in.

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
