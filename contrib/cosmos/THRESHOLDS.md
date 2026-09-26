# Alarm thresholds: a draft, and why none of it is switched on

**Status: drafted 2026-09-26, nothing applied.** 1,294 active bindings carry
zero thresholds, so `classify()` returns `unknown` for every point, the `alarms`
array is empty by construction, and the anomaly detector has nothing to fire on.
The dashboard is a display, not a monitor.

This file is the draft that would change that, the reasons it is not yet safe to
apply, and the questions that would make it safe.

---

## What was already fixed, because the draft could not be applied without it

Two defects stood between a threshold and a trustworthy alarm. Both are fixed;
neither was caused by this work, and both were invisible while no threshold
existed.

**The over-range marker was a first-class reading.** `3276.7` is the signed
16-bit maximum at 0.1 resolution - 32767/10 - and it reaches **3.3% of readings
on approved temperature points**, 8.2% on cooling water inlet. It parses cleanly
as a float, so `_coerce()` returned it as `GOOD`. With any threshold set, about
one reading in thirty would have opened a critical anomaly about a measurement
that never happened, and no limit could have helped: nothing plausible sits above
3276.7.

It is now marked `BAD` at coercion, matched with a tolerance rather than by
equality - the peg arrives in **two float32 encodings one ULP apart**
(`3276.699951171875` and `3276.7001953125`) carried by disjoint tag populations,
so an equality test catches one and misses the other. The value is kept rather
than dropped or zeroed, because "pegged" and "zero" are different facts about a
plant.

**Two of the three `classify()` call sites ignored quality.** The mimic refused
to classify a reading whose quality was not good; `summary.py` gated on
staleness alone and `anomalies.py` checked nothing at all, and the latter writes
a *persistent* anomaly. So a pegged channel could drive a machine's condition on
the Health blade while the same row displayed "Unusable or unknown" beside it.
Both now gate on quality.

The anomaly guard holds the fingerprint rather than skipping the state outright.
Dropping it would let auto-resolution close an open condition with the note
"Signal returned inside its configured limits" - a different and untrue claim
from "the sensor stopped reporting".

**Not fixed, deliberately:** the deep negatives (-118.5, -59.3, -592.6, -853.3,
-242.1). Each of the first four is the exact negative of a value the plant also
reports positive, so they are a sign fault on real channels rather than a
marker. Suppressing them at coercion would hide the fault instead of showing it.
They belong to Ask 1.

---

## The draft, and what survived review

Two adversarial passes were run over this table. **Both returned "disagree".**
The table is recorded with their objections against it rather than cleaned up,
because the objections are the useful part.

| family | pts | warn | crit | standing |
|---|---:|---:|---:|---|
| Stator winding ETDs | 318 | 125 | 145 | **Defensible, conditional.** IS/IEC 60034-1 Table 7 item 1a: 85 K rise by embedded detector for thermal class 130(B) on the 40 degC reference coolant, so 125 is the highest reading the machine is designed to produce. Trip from IEEE Std 3004.8-2016 cl. 8.5.2.1, "5 degC to 10 degC below the insulation class maximum" (155 - 10). |
| Motor / stator core RTDs | 106 | 125 | 145 | **Backstop only.** No standard gives a core figure - cl. 8.10.4 is qualitative and there is no core row in Table 7 - so these carry the winding numbers unchanged. A plausible lower number was deliberately *not* invented: it would be the first thing to fire on a hot day. |
| Thrust + guide bearing pads | 181 | 80 | 90 | **Challenged.** 80 is a comparable plant's *trip* value (its alarm is 77), from a single low-profile paper on a 200 rpm Kaplan machine. 90 traces to API 610 cl. 6.10.2.4, which is a shop-test acceptance criterion for bearing metal, not an alarm setpoint. |
| Bearing oil / reservoir | 10 | 70 | 80 | **Challenged hardest.** The only row the draft marked "no plant confirmation needed", and the one whose warning point is within reach of normal running: the reference band for a large vertical oil bath is 50-60 degC. The 70 is cited from API 610's *pressurized-system* oil outlet; these are ring-oiled sumps. |
| Cooling water inlet | 108 | 50 | - | **Would fire today.** Against a cold-plant baseline of p95 48.8 and max 70.2, a warning at 50 sits *inside* the top 5% of the soak distribution, across 108 points. |
| Cooling water outlet | 95 | 55 | - | Same shape as above. |
| Cooling air cold | 111 | 52 | - | **Would fire today**, 1.7 K above the cold p95 of 50.3 - and the cited 50 degC is a *site ambient* design figure, while the tag measures cooler discharge air entering the machine. Different physical quantity. |

**`normal_max` is unset on every row, deliberately.** Every machine in the
migrated window is stopped and the one running bay is frozen on sentinels, so
nothing in this estate establishes where a healthy loaded machine sits.
`warn_max` carries the design ceiling instead. Since `classify()` returns
WARNING for both, stating the level once avoids implying an operating band that
does not exist.

**The five `normal_min` floors in the first draft were removed.** Each was
justified as a dead-channel gate, which is precisely what the draft's own rule
forbids: a quality gate written into a threshold field fires on exactly the
readings it exists to suppress. That is what the sentinel handling above is for.

### Undeterminable, honestly

No defensible limit was found for: **hot air** (26 points - no standard fixes
one), **`PUMP_BEARING_TEMP`** (12 - the tag does not say which bearing, or metal
versus oil), **brush gear** (11 - cl. 8.10.5 declines to give a figure),
**motor shell** (9 - structural, qualitative limit only), and
**`COOLING_WATER_RTD`** (4 - upstream or downstream is unknown, and the two
warrant different bands).

---

## What the plant could answer

Ordered by how much each one moves. The first alone swings the winding band by
25-30 K.

1. Nameplate insulation class and rated rise of the 40 MW motors - class 155(F)
   with rise limited to 130(B) as assumed? Full class F rise moves the band to
   150/155; class B insulation moves it to about 110/125.
2. Is the rated rise referred to the primary (air) or secondary (water) coolant -
   the "P" or "S" marking per cl. 10.2?
3. Rated stator voltage of the 134 MW Ranganayaka machines - above 12 kV, Table 9
   removes 1 K per kV.
4. Did the purchase specification invoke IS/IEC 60034-1, or NEMA MG 1 / API 541?
   The latter are 5 K tighter, giving 120/145.
5. Do the O&M manuals state OEM alarm and trip figures? Those outrank this entire
   table - IEEE 3004.8 cl. 8.5.2.1 says manufacturers may provide them.
6. Does BHEL's type-test or heat-run report give measured winding and core rise at
   rated load? The only route to a real core band.
7. Cage induction or synchronous - which decides whether the brush gear is
   slip-ring or a shaft-grounding brush.
8. Four motor data-sheet rows would replace most of the judgement in the cooling
   families: max permissible cooling water inlet and outlet, cold air entering,
   hot air leaving.
9. Is cooling water drawn from the barrage pool or the discharge column, and what
   inlet temperature was contracted?
10. Bearing pad RTD location - 75/75 on the shoe face, or behind the bond line -
    and does the pad OEM publish alarm and trip figures?
11. Should guide pads sit about 5 degC below thrust pads, or share the band?
12. Do the 12 `PUMP_BEARING_TEMP` points read metal or oil, and on which bearing?
13. Is `COOLING_WATER_RTD` upstream or downstream of the cooler?
14. Is the 73-74 degC cluster on hot air a channel stuck at its last running
    value, a mis-mapped tag, or genuine residual heat?
15. **Can one loaded bay be captured clean for a few weeks?** That single dataset
    sets every `normal_max` here and converts the bearing rows from design
    ceilings into thresholds that can actually catch a machine drifting away from
    its sisters.
16. Do the winding points number at least six per machine everywhere (cl. 8.6.2),
    confirming they are embedded detectors and licensing Table 7's ETD column?

---

## Recommended order

1. ~~Sentinel handling and the two quality gates~~ - done.
2. Confirm the nameplate (question 1). Then the **winding row alone** can be
   applied: 318 points, standard-derived, and far enough above anything observed
   that it cannot fire spuriously.
3. Everything else waits on the data sheet or on one clean loaded window.

One further mitigation is free and worth taking when the winding row lands.
IEEE Std 3004.8-2016 cl. 8.5.2.2 recommends RTD voting so that damaged and
open-circuit inputs are ignored. With 11-12 winding detectors per machine at
Parvathi and Saraswati and 8 at Ranganayaka - comfortably above the six required
by cl. 8.6.2 - a machine should read CRITICAL only on **two or more** detectors
above the limit, a single one being annunciated as a sensor fault.
