---
title: Performance tab — pump and station analytics
---

# Performance tab

The Performance tab sits beside Health on a pump's or a pump station's page.
Health answers *is the machine all right*; Performance answers *how is it
running, what has it been doing over the last hour or shift, and which of its
parameters are moving together*. It is built from the same two facts as the
rest of the health surfaces - the machine's current signals and a bounded read
of the source's own history - and it invents nothing in between.

## What the page shows, in order

The page is arranged by the questions an operator asks, top to bottom:

1. **What is happening now.** A strip of up to eight KPI tiles: active power,
   shaft speed, discharge pressure, vibration, motor current, line voltage,
   power factor and the hottest winding sensor, then status and flow if there
   is room. Each tile shows the current value and unit, how old the reading is,
   its range over the selected window, a sparkline, a trend arrow, and the
   configured-limit verdict where a limit exists. A parameter the pump has not
   got is left out - except vibration, which keeps its tile and says why it is
   empty, because a pump with no usable vibration channel is something to see.
2. **Is anything unusual.** One line: how many signals are over a warning or
   critical limit, how many are stale or of poor quality, and how many have
   limits configured at all. "Nothing outside configured limits" is only said
   when it is true, and it is always followed by how much of the machine that
   statement covers.
3. **What changed.** Time-series sections, one per engineering group:
   electrical; speed, operation and hydraulics; motor winding temperature;
   vibration and mechanical; cooling and thermal; bearing and lubrication. A
   section only appears if the machine has parameters in it.
4. **Where.** Within a section, a family of like sensors - twelve winding RTDs,
   ten thrust pads - is one card: a header naming the highest sensor, the mean,
   the spread between sensors, the window peak and the limit state; every sensor
   on one axis with per-line show/hide; a sensors × time heatmap when there are
   four or more; and a bar comparison of current readings.
5. **How they relate.** Scatter views of pairs that are read together - power
   against speed, speed against pressure, valve position against pressure. Each
   point is one snapshot in which both were read, coloured from light to dark
   across the window. They are titled *relationship* and captioned as operating
   patterns; nothing on the page claims one parameter drives another.

Signals the catalogue does not recognise are listed in a compact table at the
end rather than hidden.

## Time range, live and zoom

One control drives every chart: presets of 15 minutes, 1, 6, 12 and 24 hours,
and a custom range bounded by the history the source actually holds. **Live**
keeps the window anchored to the clock and re-reads on the cadence at which new
data can appear. The indicator beside it is green only when the page is
following the clock *and* the newest reading is inside the source's freshness
budget; otherwise it says *Stale* or *Paused*. Beneath it are the facts needed
to judge either: the source writes every 5 s, this deployment reads it once a
minute, when the last poll ran, and whether the window on screen was read whole
or sampled (and at what resolution).

Dragging across any chart highlights a range and offers **Zoom to …**. Zoom is
a refetch, not a rescale: the narrower window is read from the source at finer
resolution, down to every five-second reading once the window is short enough.
Charts that share the page share a crosshair, so hovering one reads them all at
the same instant.

## How the history is read

AIMMS stores current values only; every chart is a bounded read of the
source's history through `/api/machine-health/machines/<id>/health/series/`.
The source writes a whole-station snapshot every five seconds, about 50 KB
each, so a full read of a day would be 17,000 documents and is never made.
Instead:

- A window short enough to read whole (about 20 minutes) is read whole, and the
  response says `mode: complete` with the cadence it measured.
- A longer window is cut into a fixed number of slots and each slot is
  answered by the *first* real snapshot in it - one small query per slot, run
  concurrently. The response says `mode: sampled` and the resolution. Nothing
  is averaged or interpolated; a slot the source has nothing for stays empty
  and the line breaks there.

The number of points is sized to the window (120 for an hour, 240 for a day)
because the cost of the read is its point count, not its length. The scope of
a series is the machine and its station: a station reads its bays, a pump reads
its station's forebay level and running count, and neither reaches a sibling.

## What the page will not do

- **Assume a unit.** Units come from the reviewed dictionary point behind each
  binding. A tag whose unit has not been confirmed is not bound, so it does not
  reach this page; the vibration section says so in words when that is the case.
- **Invent a threshold.** Limits are the ones configured on the binding and
  arrive with the signal. A parameter without one is drawn without a verdict,
  and the page says how many of the machine's signals that applies to.
- **Turn a gap into a value.** A missing reading is `null`, never zero. Zero is
  a reading - a stopped pump draws 0 MW - and the two are never conflated.
- **Present a derived number as a measurement.** Cooling ΔT, air rise, the
  spread across a family and the plant totals on a station page are computed,
  only where every input exists at the same instant, and are labelled
  *calculated* wherever they appear.

## Adding a parameter

Stations name the same measurement differently, so the tab does not know tag
names; it knows *parameters*. The catalogue in
`src/frontend/src/pages/assets/performance/parameters.ts` maps a tag pattern
to a canonical parameter with its category, its family role, its display
precision and its preferred chart. Adding a station with new naming, or a new
instrument, is a pattern in that file - not a component. A signal that matches
nothing lands in the *Other signals* table, which is the cue to add one.
