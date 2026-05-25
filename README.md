# FlightTracker

A Raspberry Pi powered 64x32 RGB LED matrix that shows the planes currently
flying over a bounding box you define. When the sky is empty it falls back to
a clock, the day of the week, the date and the local temperature.

![what it shows]: a small panel on the shelf. When a plane is overhead it
displays origin → destination, callsign and aircraft type. When nothing is
overhead it shows the time, day, date and outdoor temperature.

## What it shows

**When one or more aircraft are inside the bounding box**

- Top half: origin IATA, an arrow, destination IATA (e.g. `MEL → SYD`).
  If either airport matches `JOURNEY_CODE_SELECTED` in `config.py` it is
  rendered in bold.
- Mid line: callsign, colour-coded alpha vs. numeric. If more than one
  plane is overhead the right edge shows `N/M` and pages between them.
- Bottom line: aircraft ICAO type code (e.g. `A339`, `B789`), scrolling
  right-to-left when it doesn't fit.
- Top-right pixel pulses while data is being fetched.

**When the box is empty**

- `HH:MM` clock (top-left).
- Day of week and date (left column).
- Current temperature in °C (top-right), colour-graded blue → red across
  5 °C – 38 °C.

## Hardware

- Raspberry Pi (any model from a Pi 3 upward; tested on Pi 4 and Pi Zero 2 W).
- [Adafruit RGB matrix bonnet for Raspberry Pi](https://www.adafruit.com/product/3211).
- One 64x32 HUB75 LED matrix panel (P3, P4 or P5 pitch all work).
- 5V power supply rated for the panel (a 64x32 panel draws up to 4 A at full
  white; 5V/4A is comfortable, 5V/2A is fine at the brightness levels below).

## Software

Built on top of [`colinwaddell/flighttracker`](https://github.com/colinwaddell/flighttracker)
for the scene/setup/font scaffolding. The files in this repo replace four
modules in that project:

| File in this repo            | Replaces / installs at                |
| ---------------------------- | ------------------------------------- |
| `config.py`                  | `~/FlightTracker/config.py`           |
| `utilities/overhead.py`      | `~/FlightTracker/utilities/overhead.py` |
| `display/__init__.py`        | `~/FlightTracker/display/__init__.py` |
| `wrapper.py`                 | `~/FlightTracker/wrapper.py`          |

Data sources (all free, no API keys):

- **[adsb.lol](https://api.adsb.lol)** — live ADS-B positions, aircraft type,
  flight number.
- **[adsbdb.com](https://api.adsbdb.com)** — callsign → origin / destination
  route lookup.
- **[open-meteo.com](https://open-meteo.com)** — temperature for the home
  location.

## Install on a fresh Pi

```bash
# 1. System deps + rpi-rgb-led-matrix
sudo apt update
sudo apt install -y python3-dev python3-pip python3-pillow git
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix
make build-python PYTHON=$(which python3)
sudo make install-python PYTHON=$(which python3)
cd ..

# 2. Upstream FlightTracker
git clone https://github.com/colinwaddell/flighttracker.git ~/FlightTracker
cd ~/FlightTracker
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
pip install requests astral

# 3. Overlay the files from this repo
git clone https://github.com/paulmcclafferty/FlightTracker.git /tmp/ft-overlay
cp /tmp/ft-overlay/config.py            ~/FlightTracker/config.py
cp /tmp/ft-overlay/utilities/overhead.py ~/FlightTracker/utilities/overhead.py
cp /tmp/ft-overlay/display/__init__.py   ~/FlightTracker/display/__init__.py
cp /tmp/ft-overlay/wrapper.py            ~/FlightTracker/wrapper.py
```

## Configure your bounding box

Edit `config.py` to cover your sky. The defaults are Sydney Eastern Suburbs:

```python
ZONE_HOME = {
    "tl_y": -33.884773,   # north latitude
    "tl_x":  151.249112,  # west longitude
    "br_y": -33.960117,   # south latitude
    "br_x":  151.289081,  # east longitude
}
LOCATION_HOME = [-33.922445, 151.269097, 6371]  # centre + earth radius (km)
```

`MIN_ALTITUDE` (feet) optionally hides low traffic. `JOURNEY_HOME` (3-letter
IATA) optionally bolds your home airport in the origin/destination scene.

## Smoke test (no display needed)

```bash
cd ~/FlightTracker
source env/bin/activate
python3 -m utilities.overhead
```

You should see `processing...` followed by a list of dicts with `plane`,
`origin`, `destination`, `callsign`, `altitude`, `vertical_speed`.

## Run on the matrix

`wrapper.py` schedules the display so the panel isn't glaring at night. By
default:

- `07:00` → sunset: 80% brightness
- Sunset → `20:00`: 60% brightness
- Outside those hours: the subprocess is stopped and the matrix is blanked

Sunset is computed locally via `astral` using the lat/lon at the top of
`wrapper.py` (edit `SYDNEY = LocationInfo(...)` for your location).

Run it under systemd so it survives reboots:

```ini
# /etc/systemd/system/flighttracker.service
[Unit]
Description=FlightTracker matrix
After=network-online.target

[Service]
User=root
WorkingDirectory=/home/pi/FlightTracker
ExecStart=/home/pi/FlightTracker/env/bin/python3 wrapper.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flighttracker
journalctl -u flighttracker -f
```

`root` is needed because `rgbmatrix` opens `/dev/mem` to drive GPIO; the
library drops privileges back to the `pi` user once the matrix is initialised
(`drop_privileges = True` in `display/__init__.py`).

## Tuning

- **Bonnet quality flicker**: if you see flicker, set
  `HAT_PWM_ENABLED = True` in `config.py` after soldering the
  [Adafruit "quality" mod](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi/improving-flicker)
  (GPIO4 ↔ GPIO18). Otherwise leave it `False`.
- **Ghosting / faint pixels**: increase `GPIO_SLOWDOWN` (3, 4, 5) in
  `config.py`. Pi 4 typically needs `4` or `5`.
- **Brightness**: edit `DAY_BRIGHTNESS` / `EVENING_BRIGHTNESS` in
  `wrapper.py`. Values are 0–100. `wrapper.py` rewrites the `BRIGHTNESS`
  line in `config.py` and restarts the display subprocess on each phase
  change because brightness is read once at matrix init.

## Layout

```
.
├── config.py                # bounding box, brightness, GPIO options
├── display/
│   └── __init__.py          # render loop, scenes, weather fetch
├── utilities/
│   └── overhead.py          # adsb.lol + adsbdb.com client
├── wrapper.py               # day/evening/off scheduler + brightness writer
└── README.md
```
