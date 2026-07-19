"""Constants for the Welkom integration."""

DOMAIN = "welkom"

CONF_HOME_ID = "home_id"

FRONTEND_SCRIPT_URL = f"/{DOMAIN}/welkom-activity.js"
FRONTEND_SCRIPT_VERSION = 10  # bump to cache-bust browsers when the script changes

# Dedicated ping endpoints for the frontend script. Natural frontend URLs
# (like /manifest.json) are also fetched by clients in the background, so the
# beacons need paths that only the script ever requests.
PING_CLAIM_URL = f"/{DOMAIN}/claim"
PING_SUSTAIN_URL = f"/{DOMAIN}/sustain"
