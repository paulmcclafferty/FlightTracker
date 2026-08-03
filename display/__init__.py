"""
display/__init__.py - render loop for the 64x32 RGB matrix.

Single explicit loop:

    while True:
        canvas.Clear()
        draw_home_or_plane_scenes()
        draw_overlays()
        canvas = matrix.SwapOnVSync(canvas)
        sleep(PERIOD)

Capture-and-reassign the swapped canvas every frame so both back buffers
always hold a freshly drawn frame.
"""

import sys
import time
import urllib.request
import json
from datetime import datetime, date
from functools import lru_cache
from threading import Lock
from zoneinfo import ZoneInfo

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

from setup import colours, fonts, screen
from utilities.overhead import Overhead

try:
    from astral import LocationInfo
    from astral.sun import sun as astral_sun
except ImportError:
    LocationInfo = None
    astral_sun = None

try:
    from config import BRIGHTNESS, GPIO_SLOWDOWN, HAT_PWM_ENABLED
except (ModuleNotFoundError, NameError, ImportError):
    BRIGHTNESS = 100
    GPIO_SLOWDOWN = 4
    HAT_PWM_ENABLED = False

try:
    from config import LOADING_LED_ENABLED, LOADING_LED_GPIO_PIN
except (ModuleNotFoundError, NameError, ImportError):
    LOADING_LED_ENABLED = False
    LOADING_LED_GPIO_PIN = 25

try:
    from config import LOCATION_HOME
    LAT, LON = LOCATION_HOME[0], LOCATION_HOME[1]
except (ModuleNotFoundError, NameError, ImportError):
    LAT, LON = -33.92, 151.27

try:
    from config import JOURNEY_BLANK_FILLER
except (ModuleNotFoundError, NameError, ImportError):
    JOURNEY_BLANK_FILLER = " ? "


# ---------------- timing ----------------
FRAME_PERIOD = 0.05                    # 20 fps
DATA_POLL_INTERVAL = 30                # seconds between overhead.grab_data()
SCENE_CHECK_INTERVAL = 5               # seconds between data-state checks
PLANE_LOOP_HOLD_FRAMES = 20            # pause briefly after a scroll loop
TEMPERATURE_REFRESH_SECONDS = 60

# ---------------- positions ----------------
CLOCK_POS = (1, 8)
CLOCK_FONT = fonts.regular
CLOCK_COLOUR = colours.BLUE_DARK

DAY_POS = (2, 23)
DAY_FONT = fonts.small
DAY_COLOUR = colours.PINK_DARK

DATE_POS = (2, 31)
DATE_FONT = fonts.small
DATE_COLOUR = colours.PINK_DARKER

TEMP_POS = (48, 6)
TEMP_FONT = fonts.extrasmall

# Idle-screen sunrise / sunset stack under temperature (option A)
SUN_ICON_X = 36
SUNRISE_ICON_Y = 10          # top of 5px-tall glyph
SUNSET_ICON_Y = 17
SUN_TIME_X = 44
SUNRISE_TIME_POS = (SUN_TIME_X, 14)
SUNSET_TIME_POS = (SUN_TIME_X, 21)
SUN_TIME_FONT = fonts.extrasmall
SUNRISE_COLOUR = graphics.Color(255, 179, 71)   # warm gold
SUNSET_COLOUR = graphics.Color(255, 106, 61)    # orange-red
LOCAL_TZ = ZoneInfo("Australia/Sydney")

# Full-name journey layout (ColinWaddell FlightTracker full_label.py)
#   line 1: ORIGIN > municipality
#   line 2: DEST   < municipality
# Journey uses the larger regular font; callsign / plane type stay compact.
JOURNEY_FONT = fonts.regular
JOURNEY_LINE_Y = (8, 18)
JOURNEY_TEXT_X = 1
# 6x12 glyphs sit mostly above the baseline. Bands must not overlap between
# the two journey lines (y=8 and y=18) or letters get clipped (Sydney → svdnev).
JOURNEY_CLEAR_ABOVE = 8
JOURNEY_CLEAR_BELOW = 1
JOURNEY_ORIGIN_COLOUR = graphics.Color(255, 215, 0)       # GOLD
JOURNEY_DEST_COLOUR = graphics.Color(255, 255, 0)         # YELLOW
JOURNEY_ORIGIN_ARROW_COLOUR = graphics.Color(50, 205, 50) # LIME_GREEN
JOURNEY_DEST_ARROW_COLOUR = graphics.Color(255, 69, 0)    # ORANGE_RED
JOURNEY_ORIGIN_CITY_COLOUR = graphics.Color(100, 130, 0)  # MOSS
JOURNEY_DEST_CITY_COLOUR = graphics.Color(255, 173, 114)  # PEACH
JOURNEY_UNKNOWN_CITY = "Unknown"
JOURNEY_BOUNCE_PAUSE_FRAMES = 15

FLIGHT_NO_POS = (1, 26)
FLIGHT_NO_FONT = fonts.extrasmall
FLIGHT_NO_COLOUR_ALPHA = colours.BLUE
FLIGHT_NO_COLOUR_NUM = colours.BLUE_LIGHT
FLIGHT_BAR_Y = 23
FLIGHT_BAR_COLOUR = colours.GREEN
FLIGHT_INDEX_POS = (52, 26)
FLIGHT_INDEX_FONT = fonts.extrasmall
FLIGHT_INDEX_COLOUR = colours.GREY

PLANE_TYPE_FONT = fonts.extrasmall
PLANE_TYPE_Y = 31
PLANE_TYPE_COLOUR = colours.PINK

LOADING_PULSE_POS = (63, 0)
LOADING_PULSE_COLOUR = colours.WHITE
LOADING_PULSE_STEPS = 10

TEMP_COLOURS = (
    (5,  colours.BLUE),
    (12, colours.CYAN),
    (18, colours.GREEN),
    (24, colours.YELLOW),
    (30, colours.ORANGE),
    (38, colours.RED),
)


# ---------------- weather ----------------
@lru_cache(maxsize=1)
def _weather_cached(ttl_bucket):
    del ttl_bucket
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
        f"&current=temperature_2m&timezone=auto"
    )
    req = urllib.request.Request(url)
    raw = urllib.request.urlopen(req, timeout=5).read()
    data = json.loads(raw.decode("utf-8"))
    return float(data["current"]["temperature_2m"])


def get_temperature():
    bucket = round(time.time() / TEMPERATURE_REFRESH_SECONDS)
    try:
        return _weather_cached(bucket)
    except Exception as e:
        print(f"weather fetch failed: {e}", file=sys.stderr)
        return None


@lru_cache(maxsize=2)
def _sun_times_for_day(day_iso):
    """Return (sunrise_HHMM, sunset_HHMM) for the given ISO date, or (None, None)."""
    if LocationInfo is None or astral_sun is None:
        return (None, None)
    try:
        day = date.fromisoformat(day_iso)
        loc = LocationInfo("Home", "Australia", "Australia/Sydney", LAT, LON)
        s = astral_sun(loc.observer, date=day, tzinfo=LOCAL_TZ)
        return (
            s["sunrise"].strftime("%H:%M"),
            s["sunset"].strftime("%H:%M"),
        )
    except Exception as e:
        print(f"sun times failed: {e}", file=sys.stderr)
        return (None, None)


def get_sun_times():
    return _sun_times_for_day(datetime.now(LOCAL_TZ).date().isoformat())


def colour_gradient(c1, c2, ratio):
    return graphics.Color(
        int(c1.red + (c2.red - c1.red) * ratio),
        int(c1.green + (c2.green - c1.green) * ratio),
        int(c1.blue + (c2.blue - c1.blue) * ratio),
    )


def temp_to_colour(t):
    pairs = TEMP_COLOURS
    if t <= pairs[0][0]:
        return pairs[0][1]
    if t >= pairs[-1][0]:
        return pairs[-1][1]
    for i in range(len(pairs) - 1):
        if pairs[i][0] <= t <= pairs[i + 1][0]:
            ratio = (t - pairs[i][0]) / (pairs[i + 1][0] - pairs[i][0])
            return colour_gradient(pairs[i][1], pairs[i + 1][1], ratio)
    return pairs[0][1]


class BounceScroller:
    """Bounce-scroll text within a fixed width (Colin's FullNameLabel behaviour)."""

    def __init__(self):
        self._key = None
        self._offset = 0
        self._direction = 1
        self._pause = JOURNEY_BOUNCE_PAUSE_FRAMES

    def reset_if_changed(self, key):
        if key != self._key:
            self._key = key
            self._offset = 0
            self._direction = 1
            self._pause = JOURNEY_BOUNCE_PAUSE_FRAMES

    def step(self, text_width, available_width):
        overflow = text_width - available_width
        if overflow <= 0:
            self._offset = 0
            return 0

        if self._pause > 0:
            self._pause -= 1
            return self._offset

        self._offset += self._direction
        if self._offset >= overflow:
            self._offset = overflow
            self._direction = -1
            self._pause = JOURNEY_BOUNCE_PAUSE_FRAMES
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
            self._pause = JOURNEY_BOUNCE_PAUSE_FRAMES
        return self._offset


# ---------------- optional GPIO loading LED ----------------
_gpio = None
if LOADING_LED_ENABLED:
    try:
        import RPi.GPIO as GPIO
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LOADING_LED_GPIO_PIN, GPIO.OUT)
        GPIO.output(LOADING_LED_GPIO_PIN, GPIO.HIGH)
        _gpio = GPIO
    except Exception as e:
        print(f"GPIO setup failed: {e}", file=sys.stderr)


# ---------------- main display ----------------
class Display:
    def __init__(self):
        opts = RGBMatrixOptions()
        opts.hardware_mapping = "adafruit-hat-pwm" if HAT_PWM_ENABLED else "adafruit-hat"
        opts.rows = 32
        opts.cols = 64
        opts.chain_length = 1
        opts.parallel = 1
        opts.row_address_type = 0
        opts.multiplexing = 0
        opts.pwm_bits = 11
        opts.brightness = BRIGHTNESS
        opts.pwm_lsb_nanoseconds = 130
        opts.led_rgb_sequence = "RGB"
        opts.show_refresh_rate = 0
        opts.gpio_slowdown = GPIO_SLOWDOWN
        opts.disable_hardware_pulsing = True
        opts.drop_privileges = True

        self.matrix = RGBMatrix(options=opts)
        self.matrix.Clear()
        self.canvas = self.matrix.CreateFrameCanvas()

        self.overhead = Overhead()
        self._data = []
        self._data_index = 0
        self._plane_position = screen.WIDTH
        self._plane_hold_remaining = 0
        self._origin_city_scroll = BounceScroller()
        self._dest_city_scroll = BounceScroller()

        self._last_data_check = 0.0
        self._last_data_grab = 0.0
        self._loading_count = 0

        self.overhead.grab_data()
        self._last_data_grab = time.monotonic()

    # -------- helpers --------
    def fill_rect(self, x0, y0, x1, y1, colour):
        if x0 > x1: x0, x1 = x1, x0
        if y0 > y1: y0, y1 = y1, y0
        for x in range(x0, x1 + 1):
            graphics.DrawLine(self.canvas, x, y0, x, y1, colour)

    def _text_width(self, font, text):
        """Pixel advance width without touching the canvas."""
        return sum(font.CharacterWidth(ord(ch)) for ch in text)

    def _draw_city_bounce(self, scroller, key, city, x, y, available_width, colour):
        """Draw municipality in the city slot; bounce only if it doesn't fit.

        May briefly paint into the prefix columns while scrolling; caller must
        redraw the IATA/arrow afterwards so codes stay fixed.
        """
        city = city or ""
        width = self._text_width(JOURNEY_FONT, city)
        scroller.reset_if_changed(key)

        top = y - JOURNEY_CLEAR_ABOVE
        bottom = y + JOURNEY_CLEAR_BELOW
        # Clear only the city slot (to the right of > / <).
        self.fill_rect(x, top, screen.WIDTH - 1, bottom, colours.BLACK)

        if width <= available_width:
            scroller.step(0, 1)
            graphics.DrawText(self.canvas, JOURNEY_FONT, x, y, colour, city)
            return

        scroll_text = city + " "
        scroll_width = self._text_width(JOURNEY_FONT, scroll_text)
        offset = scroller.step(scroll_width, available_width)
        graphics.DrawText(
            self.canvas, JOURNEY_FONT, x - offset, y, colour, scroll_text,
        )

    def _draw_journey_prefix(self, code, arrow, arrow_colour, code_colour, y, city_x):
        """Clear prefix columns and redraw static IATA + direction glyph."""
        top = y - JOURNEY_CLEAR_ABOVE
        bottom = y + JOURNEY_CLEAR_BELOW
        if city_x > 0:
            self.fill_rect(0, top, city_x - 1, bottom, colours.BLACK)
        graphics.DrawText(
            self.canvas, JOURNEY_FONT, JOURNEY_TEXT_X, y, code_colour, code,
        )
        code_w = self._text_width(JOURNEY_FONT, code)
        graphics.DrawText(
            self.canvas, JOURNEY_FONT, JOURNEY_TEXT_X + code_w, y,
            arrow_colour, arrow,
        )

    # -------- home scenes (no plane data) --------
    def draw_clock(self):
        now = datetime.now()
        graphics.DrawText(
            self.canvas, CLOCK_FONT, CLOCK_POS[0], CLOCK_POS[1],
            CLOCK_COLOUR, now.strftime("%H:%M"),
        )

    def draw_day(self):
        graphics.DrawText(
            self.canvas, DAY_FONT, DAY_POS[0], DAY_POS[1],
            DAY_COLOUR, datetime.now().strftime("%a"),  # Mon Tue Wed...
        )

    def draw_date(self):
        graphics.DrawText(
            self.canvas, DATE_FONT, DATE_POS[0], DATE_POS[1],
            DATE_COLOUR, datetime.now().strftime("%-d-%-m-%Y"),
        )

    def draw_temperature(self, temp_c):
        if temp_c is None:
            return
        text = f"{round(temp_c)}°".rjust(4, " ")
        graphics.DrawText(
            self.canvas, TEMP_FONT, TEMP_POS[0], TEMP_POS[1],
            temp_to_colour(temp_c), text,
        )

    def _set_px(self, x, y, colour):
        if 0 <= x < screen.WIDTH and 0 <= y < screen.HEIGHT:
            self.canvas.SetPixel(x, y, colour.red, colour.green, colour.blue)

    def draw_sunrise_icon(self, x, y, colour):
        """Half-circle above a horizon line (sunrise). 7×5 px."""
        # horizon
        for dx in range(7):
            self._set_px(x + dx, y + 2, colour)
        # upper semicircle
        for dx, dy in ((1, 1), (2, 1), (3, 1), (4, 1), (5, 1),
                       (2, 0), (3, 0), (4, 0)):
            self._set_px(x + dx, y + dy, colour)

    def draw_sunset_icon(self, x, y, colour):
        """Half-circle below a horizon line (sunset). 7×5 px."""
        # horizon
        for dx in range(7):
            self._set_px(x + dx, y + 2, colour)
        # lower semicircle
        for dx, dy in ((1, 3), (2, 3), (3, 3), (4, 3), (5, 3),
                       (2, 4), (3, 4), (4, 4)):
            self._set_px(x + dx, y + dy, colour)

    def draw_sun_times(self):
        """Stack sunrise / sunset under the temperature (idle option A)."""
        sunrise, sunset = get_sun_times()
        if sunrise:
            self.draw_sunrise_icon(SUN_ICON_X, SUNRISE_ICON_Y, SUNRISE_COLOUR)
            graphics.DrawText(
                self.canvas, SUN_TIME_FONT,
                SUNRISE_TIME_POS[0], SUNRISE_TIME_POS[1],
                SUNRISE_COLOUR, sunrise,
            )
        if sunset:
            self.draw_sunset_icon(SUN_ICON_X, SUNSET_ICON_Y, SUNSET_COLOUR)
            graphics.DrawText(
                self.canvas, SUN_TIME_FONT,
                SUNSET_TIME_POS[0], SUNSET_TIME_POS[1],
                SUNSET_COLOUR, sunset,
            )

    # -------- plane scenes (data present) --------
    def draw_journey(self):
        """Two-line full-name layout from Colin's full_label.py.

        ORIGIN>City
        DEST<City
        """
        rec = self._data[self._data_index]
        origin = rec.get("origin") or JOURNEY_BLANK_FILLER
        destination = rec.get("destination") or JOURNEY_BLANK_FILLER
        origin_city = rec.get("origin_city") or JOURNEY_UNKNOWN_CITY
        destination_city = rec.get("destination_city") or JOURNEY_UNKNOWN_CITY

        # Measure where city slots start (IATA + arrow stay fixed).
        origin_x = (
            JOURNEY_TEXT_X
            + self._text_width(JOURNEY_FONT, origin)
            + self._text_width(JOURNEY_FONT, ">")
        )
        dest_x = (
            JOURNEY_TEXT_X
            + self._text_width(JOURNEY_FONT, destination)
            + self._text_width(JOURNEY_FONT, "<")
        )

        # City names scroll only to the right of > / <, and only if too wide.
        route_key = f"{origin}-{destination}-{self._data_index}"
        self._draw_city_bounce(
            self._origin_city_scroll,
            f"o:{route_key}:{origin_city}",
            origin_city,
            origin_x,
            JOURNEY_LINE_Y[0],
            max(1, screen.WIDTH - origin_x),
            JOURNEY_ORIGIN_CITY_COLOUR,
        )
        self._draw_city_bounce(
            self._dest_city_scroll,
            f"d:{route_key}:{destination_city}",
            destination_city,
            dest_x,
            JOURNEY_LINE_Y[1],
            max(1, screen.WIDTH - dest_x),
            JOURNEY_DEST_CITY_COLOUR,
        )

        # Draw prefixes last so a scrolling city never covers the airport code.
        self._draw_journey_prefix(
            origin, ">", JOURNEY_ORIGIN_ARROW_COLOUR, JOURNEY_ORIGIN_COLOUR,
            JOURNEY_LINE_Y[0], origin_x,
        )
        self._draw_journey_prefix(
            destination, "<", JOURNEY_DEST_ARROW_COLOUR, JOURNEY_DEST_COLOUR,
            JOURNEY_LINE_Y[1], dest_x,
        )

    def draw_flight_details(self):
        rec = self._data[self._data_index]
        callsign = rec.get("callsign") or ""
        callsign = "" if callsign.upper() in ("", "N/A", "NONE") else callsign

        # Callsign with alpha/numeric colouring
        x = FLIGHT_NO_POS[0]
        for ch in callsign:
            colour = FLIGHT_NO_COLOUR_NUM if ch.isnumeric() else FLIGHT_NO_COLOUR_ALPHA
            x += graphics.DrawText(
                self.canvas, FLIGHT_NO_FONT, x, FLIGHT_NO_POS[1], colour, ch,
            )
        callsign_end_x = x

        # Index N/M when more than one plane
        if len(self._data) > 1:
            graphics.DrawText(
                self.canvas, FLIGHT_INDEX_FONT,
                FLIGHT_INDEX_POS[0], FLIGHT_INDEX_POS[1],
                FLIGHT_INDEX_COLOUR,
                f"{self._data_index + 1}/{len(self._data)}",
            )
            bar_end_x = FLIGHT_INDEX_POS[0] - 3
        else:
            bar_end_x = screen.WIDTH

        # Dividing bar
        bar_start_x = (callsign_end_x + 2) if callsign_end_x > 0 else 0
        graphics.DrawLine(
            self.canvas, bar_start_x, FLIGHT_BAR_Y, bar_end_x, FLIGHT_BAR_Y,
            FLIGHT_BAR_COLOUR,
        )

    def draw_plane_type_scrolling(self):
        rec = self._data[self._data_index]
        plane = rec.get("plane") or ""
        if not plane:
            self._advance_plane()
            return

        text_length = graphics.DrawText(
            self.canvas, PLANE_TYPE_FONT,
            self._plane_position, PLANE_TYPE_Y,
            PLANE_TYPE_COLOUR, plane,
        )

        if self._plane_hold_remaining > 0:
            self._plane_hold_remaining -= 1
            return

        self._plane_position -= 1
        if self._plane_position + text_length < 0:
            self._advance_plane()

    def _advance_plane(self):
        self._plane_position = screen.WIDTH
        self._plane_hold_remaining = PLANE_LOOP_HOLD_FRAMES
        if len(self._data) > 1:
            self._data_index = (self._data_index + 1) % len(self._data)

    # -------- overlays --------
    def draw_loading_pulse(self):
        if self.overhead.processing:
            ratio = (1 - (self._loading_count / LOADING_PULSE_STEPS)) / 2
            ratio = max(0.0, min(1.0, ratio))
            self.canvas.SetPixel(
                LOADING_PULSE_POS[0], LOADING_PULSE_POS[1],
                int(LOADING_PULSE_COLOUR.red * ratio),
                int(LOADING_PULSE_COLOUR.green * ratio),
                int(LOADING_PULSE_COLOUR.blue * ratio),
            )
            self._loading_count = (self._loading_count + 1) % LOADING_PULSE_STEPS
        else:
            self._loading_count = 0

    def update_loading_led(self):
        if not _gpio or not LOADING_LED_ENABLED:
            return
        try:
            if self.overhead.processing:
                _gpio.output(
                    LOADING_LED_GPIO_PIN,
                    _gpio.HIGH if (self._loading_count % 2) else _gpio.LOW,
                )
            else:
                _gpio.output(LOADING_LED_GPIO_PIN, _gpio.HIGH)
        except Exception:
            pass

    # -------- data lifecycle --------
    def maybe_check_data(self, now):
        if now - self._last_data_check < SCENE_CHECK_INTERVAL:
            return
        self._last_data_check = now

        if not self.overhead.new_data:
            return

        new = self.overhead.data
        new_callsigns = {f["callsign"] for f in new}
        old_callsigns = {f["callsign"] for f in self._data}
        if new_callsigns != old_callsigns:
            self._data = new
            self._data_index = 0
            self._plane_position = screen.WIDTH
            self._plane_hold_remaining = 0
            self._origin_city_scroll = BounceScroller()
            self._dest_city_scroll = BounceScroller()
        else:
            self._data = new

    def maybe_grab_data(self, now):
        if now - self._last_data_grab < DATA_POLL_INTERVAL:
            return
        if self.overhead.processing:
            return
        self.overhead.grab_data()
        self._last_data_grab = now

    # -------- main loop --------
    def run(self):
        print("Display.run() starting")
        try:
            while True:
                now = time.monotonic()
                self.maybe_check_data(now)
                self.maybe_grab_data(now)

                self.canvas.Clear()
                if self._data:
                    self.draw_journey()
                    self.draw_flight_details()
                    self.draw_plane_type_scrolling()
                else:
                    self.draw_clock()
                    self.draw_day()
                    self.draw_date()
                    self.draw_temperature(get_temperature())
                    self.draw_sun_times()

                self.draw_loading_pulse()
                self.update_loading_led()

                self.canvas = self.matrix.SwapOnVSync(self.canvas)

                time.sleep(FRAME_PERIOD)

        except KeyboardInterrupt:
            print("Exiting")
            sys.exit(0)
