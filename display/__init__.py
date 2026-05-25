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
from datetime import datetime
from functools import lru_cache
from threading import Lock

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

from setup import colours, fonts, screen
from utilities.overhead import Overhead

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
    from config import JOURNEY_CODE_SELECTED
except (ModuleNotFoundError, NameError, ImportError):
    JOURNEY_CODE_SELECTED = "SYD"

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

JOURNEY_HEIGHT = 12
JOURNEY_FONT = fonts.large
JOURNEY_FONT_BOLD = fonts.large_bold
JOURNEY_COLOUR = colours.YELLOW
ARROW_POINT_POS = (34, 7)
ARROW_WIDTH = 4
ARROW_HEIGHT = 8
ARROW_COLOUR = colours.ORANGE

FLIGHT_NO_POS = (1, 21)
FLIGHT_NO_FONT = fonts.small
FLIGHT_NO_COLOUR_ALPHA = colours.BLUE
FLIGHT_NO_COLOUR_NUM = colours.BLUE_LIGHT
FLIGHT_BAR_Y = 18
FLIGHT_BAR_COLOUR = colours.GREEN
FLIGHT_INDEX_POS = (52, 21)
FLIGHT_INDEX_FONT = fonts.extrasmall
FLIGHT_INDEX_COLOUR = colours.GREY

PLANE_TYPE_FONT = fonts.regular
PLANE_TYPE_Y = 30
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
            DAY_COLOUR, datetime.now().strftime("%A"),
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

    # -------- plane scenes (data present) --------
    def draw_journey(self):
        rec = self._data[self._data_index]
        origin = rec.get("origin") or JOURNEY_BLANK_FILLER
        destination = rec.get("destination") or JOURNEY_BLANK_FILLER

        # Origin (left)
        font = JOURNEY_FONT_BOLD if origin == JOURNEY_CODE_SELECTED else JOURNEY_FONT
        text_len = graphics.DrawText(
            self.canvas, font, 1, JOURNEY_HEIGHT,
            JOURNEY_COLOUR, origin,
        )

        # Destination (right of arrow)
        font = JOURNEY_FONT_BOLD if destination == JOURNEY_CODE_SELECTED else JOURNEY_FONT
        graphics.DrawText(
            self.canvas, font,
            ARROW_POINT_POS[0] + 4, JOURNEY_HEIGHT,
            JOURNEY_COLOUR, destination,
        )

    def draw_journey_arrow(self):
        # Filled arrow pointing right, tip at ARROW_POINT_POS
        x = ARROW_POINT_POS[0] - ARROW_WIDTH
        y1 = ARROW_POINT_POS[1] - (ARROW_HEIGHT // 2)
        y2 = ARROW_POINT_POS[1] + (ARROW_HEIGHT // 2)
        for col in range(ARROW_WIDTH):
            graphics.DrawLine(self.canvas, x, y1, x, y2, ARROW_COLOUR)
            x += 1
            y1 += 1
            y2 -= 1
        # Tip pixel
        self.canvas.SetPixel(
            ARROW_POINT_POS[0], ARROW_POINT_POS[1],
            ARROW_COLOUR.red, ARROW_COLOUR.green, ARROW_COLOUR.blue,
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
                    self.draw_journey_arrow()
                    self.draw_flight_details()
                    self.draw_plane_type_scrolling()
                else:
                    self.draw_clock()
                    self.draw_day()
                    self.draw_date()
                    self.draw_temperature(get_temperature())

                self.draw_loading_pulse()
                self.update_loading_led()

                self.canvas = self.matrix.SwapOnVSync(self.canvas)

                time.sleep(FRAME_PERIOD)

        except KeyboardInterrupt:
            print("Exiting")
            sys.exit(0)
