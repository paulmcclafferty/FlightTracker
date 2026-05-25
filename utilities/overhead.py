"""
overhead.py - pull live ADS-B traffic inside the home bounding box.

Two free public APIs, no keys:
  1. adsb.lol   -> live positions, aircraft type, flight number
  2. adsbdb.com -> callsign -> origin / destination route lookup

Route lookups are cached per callsign in memory.
"""

from threading import Thread, Lock
from time import sleep
import math
import requests

try:
    from config import MIN_ALTITUDE
except (ModuleNotFoundError, NameError, ImportError):
    MIN_ALTITUDE = 0

try:
    from config import ZONE_HOME, LOCATION_HOME
    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
except (ModuleNotFoundError, NameError, ImportError):
    ZONE_DEFAULT = {"tl_y": 62.61, "tl_x": -13.07, "br_y": 49.71, "br_x": 3.46}
    LOCATION_DEFAULT = [51.509865, -0.118092, 6371]

RETRIES = 3
RATE_LIMIT_DELAY = 1
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 10000        # feet
EARTH_RADIUS_KM = 6371
BLANK_FIELDS = ["", "N/A", "NONE"]

ADSB_LOL_URL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}"
ADSB_LOL_ROUTE_URL = "https://api.adsb.lol/api/0/route/{callsign}"
ADSBDB_CALLSIGN_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"

REQUEST_TIMEOUT = 8         # seconds
USER_AGENT = "flighttracker-pi/1.0"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _clean(v):
    if v is None:
        return ""
    return str(v).strip()


def _zone_radius_nm(zone):
    """Radius (in nautical miles) from box centre that fully covers the box."""
    lat_c = (zone["tl_y"] + zone["br_y"]) / 2.0
    lon_c = (zone["tl_x"] + zone["br_x"]) / 2.0
    # corner distance in km via equirectangular approximation
    dlat_km = (zone["tl_y"] - zone["br_y"]) * 111.0 / 2.0
    dlon_km = (zone["br_x"] - zone["tl_x"]) * 111.0 * math.cos(math.radians(lat_c)) / 2.0
    diag_km = math.sqrt(dlat_km ** 2 + dlon_km ** 2)
    nm = diag_km / 1.852
    # add a small margin and a sane floor so adsb.lol returns useful results
    return max(5, int(math.ceil(nm * 1.2)))


def _in_zone(lat, lon, zone):
    return (
        zone["br_y"] <= lat <= zone["tl_y"]
        and zone["tl_x"] <= lon <= zone["br_x"]
    )


def distance_from_flight_to_home(flight, home=LOCATION_DEFAULT):
    def polar_to_cartesian(lat, lon, alt):
        d = math.pi / 180
        return [
            alt * math.cos(d * lat) * math.sin(d * lon),
            alt * math.sin(d * lat),
            alt * math.cos(d * lat) * math.cos(d * lon),
        ]

    def feet_to_meters_plus_earth(altitude_ft):
        return EARTH_RADIUS_KM + 0.0003048 * altitude_ft

    try:
        x0, y0, z0 = polar_to_cartesian(
            flight.latitude,
            flight.longitude,
            feet_to_meters_plus_earth(flight.altitude),
        )
        x1, y1, z1 = polar_to_cartesian(*home)
        return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
    except (AttributeError, TypeError):
        return 1e6


# ----------------------------------------------------------------------
# Flight wrapper - mimics the old FlightRadar24 Flight object
# ----------------------------------------------------------------------

class Flight:
    def __init__(self, raw):
        self.hex = _clean(raw.get("hex"))
        self.callsign = _clean(raw.get("flight"))
        self.latitude = raw.get("lat") or 0.0
        self.longitude = raw.get("lon") or 0.0
        # adsb.lol can return alt_baro as the string "ground"
        alt = raw.get("alt_baro")
        self.altitude = alt if isinstance(alt, (int, float)) else 0
        self.vertical_speed = raw.get("baro_rate") or 0
        self.aircraft_model = _clean(raw.get("t"))      # ICAO type code, e.g. A339
        self.origin_airport_iata = ""
        self.destination_airport_iata = ""


# ----------------------------------------------------------------------
# Overhead - public class consumed by the rest of the project
# ----------------------------------------------------------------------

class Overhead:
    def __init__(self):
        self._lock = Lock()
        self._data = []
        self._new_data = False
        self._processing = False
        self._route_cache = {}      # callsign -> (origin_iata, destination_iata)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    # public interface ---------------------------------------------------

    def grab_data(self):
        Thread(target=self._grab_data).start()

    @property
    def new_data(self):
        with self._lock:
            return self._new_data

    @property
    def processing(self):
        with self._lock:
            return self._processing

    @property
    def data(self):
        with self._lock:
            self._new_data = False
            return self._data

    @property
    def data_is_empty(self):
        return len(self._data) == 0

    # internals ----------------------------------------------------------

    def _fetch_positions(self):
        lat = (ZONE_DEFAULT["tl_y"] + ZONE_DEFAULT["br_y"]) / 2.0
        lon = (ZONE_DEFAULT["tl_x"] + ZONE_DEFAULT["br_x"]) / 2.0
        nm = _zone_radius_nm(ZONE_DEFAULT)
        url = ADSB_LOL_URL.format(lat=lat, lon=lon, nm=nm)
        r = self._session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        payload = r.json() or {}
        flights = [Flight(item) for item in payload.get("ac") or []]
        # adsb.lol gives a circular result; clamp to the user's exact box
        flights = [
            f for f in flights
            if _in_zone(f.latitude, f.longitude, ZONE_DEFAULT)
        ]
        return flights

    def _lookup_route_adsblol(self, cs):
        """adsb.lol: /api/0/route returns '_airport_codes_iata' like 'MCY-SYD'."""
        try:
            r = self._session.get(
                ADSB_LOL_ROUTE_URL.format(callsign=cs),
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            if r.status_code == 404:
                return ("", "")
            r.raise_for_status()
            payload = r.json() or {}
            iata = payload.get("_airport_codes_iata") or ""
            parts = [p.strip() for p in iata.split("-") if p.strip()]
            origin = parts[0] if parts else ""
            destination = parts[-1] if len(parts) > 1 else ""
            return (origin, destination)
        except (requests.RequestException, ValueError):
            return ("", "")

    def _lookup_route_adsbdb(self, cs):
        """adsbdb.com: /v0/callsign returns flightroute with origin/destination."""
        try:
            r = self._session.get(
                ADSBDB_CALLSIGN_URL.format(callsign=cs),
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 404:
                return ("", "")
            r.raise_for_status()
            payload = r.json() or {}
            route = (payload.get("response") or {}).get("flightroute") or {}
            origin = _clean((route.get("origin") or {}).get("iata_code"))
            destination = _clean((route.get("destination") or {}).get("iata_code"))
            return (origin, destination)
        except (requests.RequestException, ValueError):
            return ("", "")

    def _lookup_route(self, callsign):
        """callsign -> (origin_iata, destination_iata). Cached.
        Tries adsb.lol first, then falls back to adsbdb.com on miss.
        """
        cs = callsign.strip().upper()
        if not cs:
            return ("", "")
        if cs in self._route_cache:
            return self._route_cache[cs]

        result = self._lookup_route_adsblol(cs)
        if result == ("", ""):
            result = self._lookup_route_adsbdb(cs)

        self._route_cache[cs] = result
        return result

    def _grab_data(self):
        with self._lock:
            self._new_data = False
            self._processing = True

        data = []

        try:
            flights = self._fetch_positions()
            flights = [
                f for f in flights
                if MIN_ALTITUDE < (f.altitude or 0) < MAX_ALTITUDE
            ]
            flights.sort(key=lambda f: distance_from_flight_to_home(f))

            for flight in flights[:MAX_FLIGHT_LOOKUP]:
                sleep(RATE_LIMIT_DELAY)     # be polite to adsbdb

                origin, destination = self._lookup_route(flight.callsign)
                flight.origin_airport_iata = origin
                flight.destination_airport_iata = destination

                plane = flight.aircraft_model if flight.aircraft_model.upper() not in BLANK_FIELDS else ""
                origin = origin if origin.upper() not in BLANK_FIELDS else ""
                destination = destination if destination.upper() not in BLANK_FIELDS else ""
                callsign = flight.callsign if flight.callsign.upper() not in BLANK_FIELDS else ""

                data.append({
                    "plane": plane,
                    "origin": origin,
                    "destination": destination,
                    "vertical_speed": flight.vertical_speed,
                    "altitude": flight.altitude,
                    "callsign": callsign,
                })

            with self._lock:
                self._data = data
                self._new_data = True
                self._processing = False

        except requests.RequestException as e:
            print(f"[overhead] adsb.lol request failed: {e}")
            with self._lock:
                self._new_data = False
                self._processing = False


# ----------------------------------------------------------------------
# CLI smoke test:  python -m utilities.overhead
# ----------------------------------------------------------------------

if __name__ == "__main__":
    o = Overhead()
    o.grab_data()
    while o.processing:
        print("processing...")
        sleep(1)
    print(f"{len(o.data)} flights:")
    for f in o.data:
        print(f)
