"""
wrapper.py - schedules the FlightTracker display.

Phases (Sydney local time):
  off          hour < 7 or hour >= 20         -> subprocess stopped, matrix blanked
  day          07:00 - sunset                 -> 80% brightness
  evening      sunset - 20:00                 -> 60% brightness

Brightness changes by editing config.py BRIGHTNESS line and restarting the
subprocess (since brightness is read once at matrix init).
"""

import os
import re
import time
import subprocess
import zoneinfo
from datetime import datetime, date

from astral import LocationInfo
from astral.sun import sun

from rgbmatrix import RGBMatrix, RGBMatrixOptions

PROJECT_DIR = "/home/pi/FlightTracker-adsb"
PYTHON_BIN  = os.path.join(PROJECT_DIR, "env/bin/python3")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.py")

OPERATING_START_HOUR = 7
OPERATING_END_HOUR   = 20

DAY_BRIGHTNESS     = 80
EVENING_BRIGHTNESS = 60

# Sydney Eastern Suburbs - good enough for sunset to the minute
SYDNEY = LocationInfo("Sydney", "Australia", "Australia/Sydney", -33.92, 151.27)
TZ = zoneinfo.ZoneInfo("Australia/Sydney")


def sunset_today():
    s = sun(SYDNEY.observer, date=datetime.now(TZ).date(), tzinfo=TZ)
    return s["sunset"]


def current_phase(now=None):
    now = now or datetime.now(TZ)
    h = now.hour
    if h < OPERATING_START_HOUR or h >= OPERATING_END_HOUR:
        return "off"
    if now >= sunset_today():
        return "evening"
    return "day"


def desired_brightness(phase):
    return EVENING_BRIGHTNESS if phase == "evening" else DAY_BRIGHTNESS


def write_brightness(value):
    """Rewrite the BRIGHTNESS = N line in config.py. No-op if already correct."""
    with open(CONFIG_PATH) as f:
        src = f.read()
    new = re.sub(r"^BRIGHTNESS\s*=\s*\d+", f"BRIGHTNESS = {value}", src, count=1, flags=re.M)
    if new != src:
        with open(CONFIG_PATH, "w") as f:
            f.write(new)


def blank_matrix():
    try:
        opts = RGBMatrixOptions()
        opts.rows = 32
        opts.cols = 64
        opts.hardware_mapping = "adafruit-hat"
        opts.gpio_slowdown = 5
        RGBMatrix(options=opts).Clear()
    except Exception as e:
        print(f"blank_matrix failed: {e}")


def start_subprocess():
    # Wipe LEDs before launching the new process so any leftover pixels
    # from the previous phase don't appear as ghost text.
    blank_matrix()
    return subprocess.Popen(
        [PYTHON_BIN, "flight-tracker.py"],
        cwd=PROJECT_DIR,
    )


def stop_subprocess(proc):
    if not proc:
        return None
    if proc.poll() is None:
        proc.terminate()
        time.sleep(2)
        if proc.poll() is None:
            proc.kill()
    return None


def main():
    print(f"FlightTracker wrapper - {OPERATING_START_HOUR}:00 to {OPERATING_END_HOUR}:00 (Sydney)")
    print(f"Day brightness {DAY_BRIGHTNESS}%, evening (after sunset) {EVENING_BRIGHTNESS}%")

    proc = None
    active_phase = None

    while True:
        now = datetime.now(TZ)
        phase = current_phase(now)

        if phase != active_phase:
            ts = now.strftime("%Y-%m-%d %H:%M")
            if phase == "off":
                print(f"[{ts}] phase -> off (sunset today {sunset_today().strftime('%H:%M')})")
                proc = stop_subprocess(proc)
                blank_matrix()
            else:
                b = desired_brightness(phase)
                print(f"[{ts}] phase -> {phase} (brightness {b}%, sunset {sunset_today().strftime('%H:%M')})")
                proc = stop_subprocess(proc)
                write_brightness(b)
                proc = start_subprocess()
            active_phase = phase
        else:
            if phase != "off" and (proc is None or proc.poll() is not None):
                ts = now.strftime("%Y-%m-%d %H:%M")
                print(f"[{ts}] subprocess died, restarting")
                proc = start_subprocess()

        time.sleep(30)


if __name__ == "__main__":
    main()
