"""
Local config for flighttracker.
Drop this file in the project root next to flight-tracker.py.
"""

# --- Bounding box (your overhead area) ---------------------------------
# Top-left = NW corner, Bottom-right = SE corner.
ZONE_HOME = {
    "tl_y": -33.884773,   # north latitude
    "tl_x":  151.249112,  # west longitude
    "br_y": -33.960117,   # south latitude
    "br_x":  151.289081,  # east longitude
}

# Centre of the box, used for ranking flights by distance.
# [lat, lon, earth_radius_km]
LOCATION_HOME = [-33.922445, 151.269097, 6371]

# Hide anything below this altitude (feet). 0 = no filter.
MIN_ALTITUDE = 0

# Optional: weather (only used by the weather scene)
# OPENWEATHER_KEY = ""
# OPENWEATHER_LOCATION = "Sydney,AU"

# Optional: airport code to highlight as a "local" flight (3-letter IATA)
# JOURNEY_HOME = "SYD"
