# Full-name journey display (municipality)

Date: 2026-08-03  
Repo: `paulmcclafferty/FlightTracker` (overlay on ColinWaddell FlightTracker v1 scaffolding)

## Goal

Match ColinWaddell FlightTracker v2 **full-name journey** layout on the 64×32 matrix:

```
DUB>Dublin
GLA<Glasgow
```

Use municipality (city) names, not full airport names. Keep existing bounding box, brightness, GPIO, wrapper schedule, idle clock/weather, callsign bar, and plane-type scroll.

## Approach

**Port layout rules from Colin’s `scenes/flight/journey/full_label.py` into the overlay** — do not vendor the v2 Scroller/panel/theme stack.

Reference: [ColinWaddell/FlightTracker `full_label.py` (main)](https://github.com/ColinWaddell/FlightTracker/blob/main/scenes/flight/journey/full_label.py)

## Display changes (`display/__init__.py`)

Replace `draw_journey` / `draw_journey_arrow` (single-line IATA + pixel arrow) with a two-line journey drawer:

| Element | Position / behaviour | Colour (Default theme) |
|--------|----------------------|-------------------------|
| Origin IATA | Static, baseline y=6, x=1 | Gold `(255, 215, 0)` |
| Origin arrow | Static `>` immediately after code | Lime green `(50, 205, 50)` |
| Origin city | After prefix; bounce/scroll if wider than remaining width | Moss `(100, 130, 0)` |
| Dest IATA | Static, baseline y=14, x=1 | Yellow `(255, 255, 0)` |
| Dest arrow | Static `<` immediately after code | Orange-red `(255, 69, 0)` |
| Dest city | After prefix; bounce/scroll if wider than remaining width | Peach `(255, 173, 114)` |

- Font: existing `fonts.small` (5×8 BDF), standing in for Colin’s `small_symbols`.
- Blank IATA still uses `JOURNEY_BLANK_FILLER` (`" ? "`).
- Missing municipality → show `Unknown` (same fallback as Colin).
- Callsign bar, green divider, N/M index, pink plane-type scroll, loading pulse: unchanged.
- Idle scenes (clock / day / date / temperature): unchanged.

Scrolling: bounce-scroll the city text within the remaining width after the static prefix (same behaviour as Colin’s name scroller; implemented with a small local helper — no v2 `Scroller` dependency).

## Data changes (`utilities/overhead.py`)

adsbdb already returns Airport objects with `municipality`. Today only `iata_code` is kept.

Extend route lookup / cache / emitted flight dicts with:

- `origin_city` — `origin.municipality`
- `destination_city` — `destination.municipality`

Keep existing `origin` / `destination` IATA fields. Prefer adsbdb for names (richer). If only adsb.lol route IATA is available, cities may be empty → display `Unknown`.

Do **not** change bounding box, altitude filters, or poll intervals.

## Unchanged

- `config.py` zone / location / altitude
- `wrapper.py` day/evening brightness schedule
- Hardware matrix options already in `display/__init__.py`
- No airline logos, themes UI, or airport_display_style config

## Success criteria

1. With a resolved route, top of panel shows two lines: `III>City` and `JJJ<City`.
2. Long city names scroll/bounce instead of clipping off the panel.
3. Callsign + plane type still render below as before.
4. Empty sky still shows clock / day / date / temp.
5. Bounding box behaviour unchanged.
