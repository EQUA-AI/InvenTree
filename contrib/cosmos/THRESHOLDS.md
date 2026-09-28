# Alarm thresholds: one row applied, and why the rest are not

**Status: the stator-winding row and the core backstop are applied; everything
else is still a draft.** **411 of 1,452** active bindings carry limits. The rest remain unbounded, so
`classify()` still returns `unknown` for them. (The earlier figure here, 1,294,
predated the last activation; re-counted 2026-09-27.)

A second research pass over the remaining four families closed on 2026-09-27 and
**added no rows** - see "The second pass" below for the four verdicts, the six
findings that came out of it, and the six new questions for the plant.

This file records what was applied and why, what is still only drafted, and the
questions that would let the rest follow.

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

**This paragraph used to be wrong, and the correction is instructive.** It said
the deep negatives (-118.5, -59.3, -592.6, -853.3, -242.1) were each "the exact
negative of a value the plant also reports positive, so they are a sign fault on
real channels rather than a marker", and left them at `GOOD` on that reasoning.

For `-59.3` it is false. Its positive counterpart is **32767** counts and it is
itself **32768** - one raw count apart, which is a two's-complement rail pair,
not a sign flip. It is the same class of object as `3276.7` above, one range
scalar away. Finding 2 below has the lattice arithmetic. `-853.3` still fits
nothing and remains with Ask 1. `-242.1` is settled - see "What applying the
core backstop took": one dead channel, constant across the span.

---

## The draft, and what survived review

Two adversarial passes were run over this table. **Both returned "disagree".**
The table is recorded with their objections against it rather than cleaned up,
because the objections are the useful part.

| family | pts | warn | crit | standing |
|---|---:|---:|---:|---|
| Stator winding ETDs | 318 | 125 | 145 | **APPLIED to 307 of them, 2026-09-26.** IS/IEC 60034-1 Table 7 item 1a: 85 K rise by embedded detector for thermal class 130(B) on the 40 degC reference coolant, so 125 is the highest reading the machine is designed to produce. Trip from IEEE Std 3004.8-2016 cl. 8.5.2.1, "5 degC to 10 degC below the insulation class maximum" (155 - 10). |
| Motor / stator core RTDs | 106 | 125 | 145 | **APPLIED to 105 of them, 2026-09-28.** Backstop only. No standard gives a core figure - cl. 8.10.4 is qualitative and there is no core row in Table 7 - so these carry the winding numbers unchanged. A plausible lower number was deliberately *not* invented: it would be the first thing to fire on a hot day. |
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

### What applying the winding row actually took

Setting it on all 318 would have raised alarms immediately, and the sentinel fix
did not prevent that. After 3276.7 was marked bad, **41 of 7,573 sampled winding
readings still breached** - 12 above 145 and 29 between 125 and 145, including
191 and 192.8 degC on a plant whose every bay is stopped in a 29 degC hall.

They are not spread across the fleet. **Six channels, all at Saraswati, produce
every one of them**, and four of those are out of range in every sample taken:

| channel | bad samples |
|---|---|
| `PUMP2_..._TEMPERATURED1` | 73 of 73 |
| `PUMP2_..._TEMPERATURED2` | 73 of 73 |
| `PUMP7_..._TEMPERATURED10` | 71 of 71 |
| `PUMP2_..._TEMPERATURED10` | 12 of 12 |
| `PUMP6_..._TEMPERATURED3` | 43 of 73 |
| `PUMP6_..._TEMPERATURED2` | 31 of 74 |

So the channels were classified from the data before any limit was written, and
`contrib/cosmos/devtools/apply_winding_thresholds.py` sets limits only where a
channel has at least 20 usable samples and none of them impossible. That is 307
points. The six faulty channels are an instrumentation question - Ask 1's kind,
not a limit question.

Five points are left, and they are a third kind of thing again: at Millbrook
(Saraswati), `PUMP1_..._TEMPERATURED1`, `D10` and `D4`, `PUMP6_..._TEMPERATURED1`
and `PUMP9_..._TEMPERATURED6` returned **zero** samples across all 288 hours of
that station's span. The tags are approved in the dictionary and bound, but the
payload never carries them, so those five bindings will read `unknown` forever
however the limits are set. That is a dictionary question - either the tag names
drifted or those detectors are not wired - and it belongs with Ask 1.

Result: 307 points classify, **0 anomalies raised**, 0 open.

An earlier run set 274 and reported the other 38 as "too few samples", blaming
Ranganayaka's sparse span. The span was not the problem; the sampler was. It
probed `hours // 70` apart across 288 hours, which is a fine stride when ~80% of
the hours hold data (Millbrook and Cedar Creek) and a bad one at Ranganayaka,
where 50 do - it landed on about a dozen. The tool now re-probes at half the
stride while any channel is still short, so a dense station is read exactly as
before, with no extra requests, and a sparse one is walked as densely as it
takes. It halves rather than stopping at the first 20 samples on purpose:
stopping early would judge every channel on the opening hours of the span and
miss the intermittent faults this classification exists to catch. The six faulty
channels came back identical under the denser walk, which is the evidence that
the change did not move the classification, only its coverage.

The 38 were also not all Ranganayaka's: 32 were, and the other 6 were at
Millbrook - 1 that the denser walk resolved and the 5 absent tags above.

A counting note, because the draft got it wrong and so did I when reporting it:
318 is the number of *points*, but only 197 distinct tag paths - the same tag
exists at more than one station. Any per-channel analysis has to key on
(station, path) or it will clear a point at one station because its namesake
elsewhere is clean.

### What applying the core backstop took

Applied 2026-09-28 to **105 of 106** points, screened per `(station, path)` over
the recorded span with the same sampler and the same rules as the winding row -
`PLAUSIBLE = (0, 125)`, `MIN_SAMPLES = 20`, densifying while any channel is
short. 75 samples per channel, 105 clean, **0** too sparse to judge.

One exclusion, and it closes an open question rather than opening one.
`PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE` at Cedar Creek reads **exactly -242.1 in
75 of 75 samples**, while its five sibling detectors on the same bay read
36.6-43.4 degC throughout. A constant impossible value is a dead channel, so it
is excluded with that reason rather than armed. It also **identifies the -242.1**
that this file and BLOCKERS.md carried as an unexplained deep negative: it is one
channel, it is not on the converter rail lattice, and it is not a sign fault.

Nothing fires. Hottest core reading anywhere on the estate is 67.7 degC against a
125 degC warning, and `apply_signal_limits` reports `evaluated : 29 machines, 0
breaching` on application. The row will only ever fire on a genuine excursion or
an instrument fault - which is what a backstop is for, and also why it is not a
substitute for question 6.

### Undeterminable, honestly

No defensible limit was found for: **hot air** (26 points - no standard fixes
one), **`PUMP_BEARING_TEMP`** (12 - the tag does not say which bearing, or metal
versus oil), **brush gear** (11 - cl. 8.10.5 declines to give a figure),
**motor shell** (9 - structural, qualitative limit only), and
**`COOLING_WATER_RTD`** (4 - upstream or downstream is unknown, and the two
warrant different bands).

---

---

## The second pass: the other four families, and why the table did not grow

A second research pass, 2026-09-27, covered every remaining bound family -
**852 points** in four groups - looking for the same thing the winding row
found: a published figure that is the *same physical quantity*, at the *same
measurement location*, serving the *same purpose* as an alarm.

**It did not grow the table. Not one new row.** Each family was then challenged
by independent reviewers working a different angle - one on the citations, one
on the estate data. Every challenger agreed the verdict. Three of the four
proposals nevertheless failed their challenge on the *reasoning*, and those
corrections are recorded here, because on this table the reasoning is what gets
reused.

| family | pts | verdict | why, in one line |
|---|---:|---|---|
| Vibration | 158 | needs-plant-input | ISO 20816-3 cl. 1 excludes these machines twice over, and the channel carries raw converter counts rather than any engineering quantity. |
| Electrical | 88 bound | needs-plant-input | The one defensible figure is blocked by the run-state gap below, not by the standards. 132 further points are not bound at all, so there is no field to write into. |
| Bearing temperatures | 203 | needs-plant-input | Five primary standards were read in full and **all five route the number to the machinery vendor**. The path that produced 125/145 for windings does not exist for bearings. |
| Cooling + hydraulics | 403 | no-defensible-figure | The standard's cooling figures are rating-validity boundaries, not protection limits - and the winding row already prices coolant temperature in. |

Six findings from the pass change what happens next.

### 1. The winding row already covers the cooling families

IS/IEC 60034-1 Table 9 item 2 does not annunciate a hot coolant; it *reduces the
permitted winding rise*. Work the arithmetic through and the permitted rise
above water inlet is `85 + 15 - (θw - 25)`, so the absolute ceiling is
`θw + (125 - θw)` = **125 °C for every θw**, and the 5-25 °C branch gives the
same answer.

Two consequences. A separate cooling-water alarm would not add protection this
estate lacks - it would duplicate, less reliably and with far more false
positives, a limit that is already live and already accounts for coolant
temperature by construction. And **question 2 below can be dropped**: whether
the rated rise is referred to the primary or the secondary coolant does not move
the applied 125/145 band. That is one ask retired by arithmetic rather than by a
document request.

### 2. The vibration channel is unscaled converter counts

All 158 vibration readings sit exactly on a raw-count lattice of
`0.003616898087784648 / 20` = `1.8084490438923239e-4` per count. **Reproduced
independently against the cache**: 158 of 158 on-lattice, no exceptions.

The extremes are the two's-complement rails, not measurements:

| reading | counts | meaning | points | where |
|---|---:|---|---:|---|
| `-59.25925827026367` | `-32768 x 10` | negative rail | 10 | Millbrook, 7 stopped bays |
| `+59.25745391845703` | `+32767 x 10` | positive rail | 5 | Millbrook **Pump 05 only** |

They differ by **exactly one raw count**, which is the defining signature of a
rail pair. All 15 currently carry quality `good`.

Pump 05 is the one bay in the whole estate that is running. **Every one of its
vibration channels is at positive full scale.**

It is also frozen, and the two facts are separate. Measured against the local
emulator over 539 consecutive snapshots spanning an hour, **all 66 of Pump 05's
tags are `distinct=1`** - not only the rails but `ACTIVE_POWER` 24.5,
`DISCHARGE_PRESSURE` 18.962385, every winding RTD. The control settles that this
is not how the data normally behaves: stopped Pump 04, same station, same 539
snapshots, varies on **45 of its 66**. So the "frozen P5 block" BLOCKERS.md
records is real *and* its monitoring channels are additionally railed. An
earlier draft of this section said the bay's other readings were "plausible and
varied"; they are varied across channels and frozen in time, which is not the
same thing, and the mistake was not checking the time axis.

Either way the estate's only loaded vibration sample is not weak evidence, it is
no evidence.

This **refutes the "not fixed, deliberately" paragraph above** as it stood, and
the same claim in the coercion source. `-59.3` is not "the exact negative of a
value the plant also reports positive": its positive counterpart is 32767
counts, not 32768. It is the converter's negative rail - the same class of
object as `3276.7`, which is `32767/10`, the same rail at a different range
scalar.

**And it is not only vibration.** Swept properly - `abs(value)/LSB = counts x 10
x scalar`, `counts` in {32767, 32768}, every integer scalar to 100,000 - the
cache holds **21 railed readings at exactly four scalars and no others**:

| scalar | value | counts | unit | rows |
|---:|---|---:|---|---:|
| x1 | `-59.25925827026367` / `+59.25745391845703` | 32768 / 32767 | - | 10 / 5 |
| x2 | `-118.51851654052734` | 32768 | percent | 1 |
| x8 | `+474.05963134765625` | 32767 | rpm | 1 |
| x10 | `-592.5925903320312` / `+592.5745239257812` | 32768 / 32767 | V | 3 / 1 |

Two are self-evidently not measurements: a valve position of **-118.5%** and a
field voltage of **-592.6 V**.

`PUMP5_SPEED` is the one to be careful about, and an earlier draft of this
section overstated it. 474.06 rpm is exactly 32767 counts at scalar 80, with a
relative error identical to the confirmed positive vibration rail - but 474.06
rpm is also a plausible instrument range for this machine, so "the converter
saturated" and "the machine is at the top of its range" cannot be told apart
from the value. Provable: the reading sits exactly on the 32767 boundary. Not
provable: which of the two that means. See BLOCKERS.md.

`18.96` and `7.111` are *not* on this lattice. Their implied scalars land within
6e-5 of `0.32` and `0.12`, suggestive of the same mechanism on a different
conversion constant, but 6e-5 is 150x the float32 tolerance, so it is not proof.
`853.3` fits nothing. `242.1` is not a rail either, but it is no longer a mystery: it is one dead core detector, identified below.

It also dissolves the family's stated clincher. The pass argued vibration is
unboundable *by construction*, because a one-sided ceiling would flip the ten
`-59.2593` points from an honest UNKNOWN to a false NORMAL. True of `classify()`
- but those readings must never reach it. Coerce the rails and the objection
becomes a sequencing problem, not a structural one: unboundable **until the
rails are coerced and the channel scaling is answered**.

What remains genuinely blocking: ISO 20816-3:2022 cl. 1 excludes these machines
by two separate items - (m) "machine sets in hydraulic power generating and
pumping plants" and (o) rotordynamic pumps with directly-mounted impellers - and
cl. 6.2.3 and 6.5.2 require an alarm to be set relative to a **per-machine
baseline**, not to a zone boundary. With the one loaded bay railed on every
channel, the estate holds **zero usable loaded readings** to baseline against.
Not "one frozen bay to discount" - none.

### 3. `classify()` has no run-state input, and it gates almost everything

Every electrical and hydraulic quantity here is meaningful only while the machine
is energised. 29 of 30 bays are stopped. `classify()` is an unconditional
per-binding scalar comparison, and none of its three call sites passes machine
state.

So a max-only band - the only shape rule 5 above permits - converts a stopped
bay's `0.0` from an honest UNKNOWN into a false NORMAL. This is the mirror image
of the `normal_min` floors this table already removed: **a threshold field cannot
express "this reading is meaningless because the machine is off", in either
direction.**

The research offered one row as held back purely by this - IEC 60034-1 cl. 7.4
Zone A, `warn_max 51.0 / critical_max 51.5 Hz` on the 14 `PUMP_FREQUENCY` points,
clearing the highest live reading of 50.0737 Hz by 0.93 Hz. **That example does
not survive checking, and the correction is worth more than the row was.** All 14
bound points are at Cedar Creek, where `MOTOR_ON_STATUS` reads 0 on all fourteen
bays; the two stations that *do* have a running bay have their frequency points
**withheld** in the dictionary review ("Reads 0 on a loaded pump; a running
machine cannot be at 0 Hz"). So a run-state gate would arm this row on **0 of 14
points**. The reviewer's own approved note says what the tag is: six bays sense
50.0 Hz at the breaker while stopped. It is a *supply-present* measurement, not a
machine frequency - a dictionary question, not a threshold one.

The gap is still real; the frequency row was simply the wrong illustration of it.

The same gate blocks discharge pressure and flow outright: the operationally
interesting alarm - *running but not making head or flow* - cannot be expressed
at any value until classification can be conditioned on state. A `warn_min` there
would be the rule-5 error in a new costume, firing on all 29 stopped bays.

IEGC Reg 30(1)'s 49.900-50.050 Hz band was examined and **rejected**: it is a
system-operation band owned by the Load Despatch Centres, a drawal consumer
neither causes nor can act on a frequency excursion, and Grid-India's own
published data has frequency above 50.05 Hz for 26-38% of the day - the alarm
would stand about a third of the time on a healthy machine.

### 4. Bearings: five standards, five deferrals

Read directly, not via summaries: IEEE Std 3004.8-2016 cl. 8.5.4.2 (wholly
qualitative - sensor type, placement, trip-vs-alarm, voting, no number
anywhere); API Std 670 §8.2.2 item 8 (setpoints "as recommended by the machinery
vendor"); IS 5120:1977 cl. 13.2(b) ("shall not exceed the limits specified by the
manufacturer", and note it is a *pump-test observation*, the same trap the API
610 row fell into); IS/IEC 60034-1 cl. 8.9 and Table 6 (specifies only *how* to
measure - there is no bearing row in the Table 7 the winding row depends on);
and USBR FIST 2-7, which prescribes a documented heat run instead of a figure.

The asymmetry is the finding: **the same IEEE standard that ties a winding
setpoint to a nameplate quantity ties the bearing setpoint to nothing.** That is
why this is needs-plant-input rather than no-defensible-figure - the BHEL O&M
manual and the pad OEM data almost certainly carry one.

Two data findings sharpen the bearing ask:

- At Parvathi, **two independent tag families claim the same bay's thrust
  bearing and disagree by a mean of +11.2 K at standstill** (spread +4.4 to
  +21.2 K across all 14 bays). It cannot be the Table 6 measuring-location
  effect, because that difference is driven by heat flowing through the bearing
  and at standstill there is none. The `THRST_PD_RTD` group tracks its bay's
  cooling-water band rather than the hall. One band cannot legitimately span both
  families.
- Those same 42 `THRST_PD_RTD` points are **withheld at Saraswati** because 100%
  of 2,501 running samples read the over-range sentinel while the cooling-water
  control read a steady 29.3. The instrument fails exactly when the pump runs.
  Parvathi's 42 are approved on unit only, at rest. A limit written on them would
  be guaranteed inert under load: it would present as protection and deliver
  none.

### 5. Voting is now required, not merely recommended

API Std 670 cl. 5.4.6.4: "Dual voting logic shall be standard when two sensors
are installed in the load zone of the bearing." IEEE 3004.8 cl. 8.5.4.2 e)
recommends more than one sensor per bearing on critical machines to avoid
nuisance tripping and to provide sensor-failure backup. The detectors exist
everywhere - 3 `THRST_PD_RTD` + 2 axial + 2 radial per bay at Parvathi, up to 10
thrust pads per bay at Ranganayaka. This is the same mitigation already noted for
the winding row, and for bearings a standard requires it.

### 6. Oil is corroboration, never the primary alarm

IEEE 3004.8 cl. 8.5.4.2 f)1) NOTE is explicit that oil temperature responds more
slowly than embedded metal measurement and "should only be used as a secondary
measurement to corroborate machinery problems when timeliness is not a concern".
The 10 oil points sit at Ranganayaka beside 62 pad detectors on the same 4 bays.
So the first draft's 70/80 was the tightest number in the whole table attached to
its least timely measurement.

### What was done about the six findings

Each of the three code changes the pass implied was designed and then attacked by
two independent reviewers. **All three designs were refuted**, which is the useful
part: one was corrected and shipped, two were stopped.

**Shipped: the limits now actually alarm.** Findings 1-6 are all about *which*
number to write. None of them mattered, because nothing on this estate could
raise an alarm at all. `evaluate_thresholds` had exactly one caller - the webhook
ingest view - and that view is unreachable for a Cosmos source, because
`ingest_readings` refuses a Cosmos batch without an explicit station. So the 307
armed winding points had been inert since the day they were set: a reading could
have landed above 145 degC every minute for a year and `MachineAnomaly` would
still have held nothing.

The live poll path now evaluates the machines whose state it wrote, after the
ingest verdict is taken and inside a helper that cannot raise - the sweep's own
handler feeds `_classify`, which has no branch for a detector error and would
have filed one as `NETWORK` while suppressing `last_success_at`, telling an
operator a poll that succeeded could not reach Cosmos. Three further paths came
with it:

- `apply_signal_limits` evaluates every binding a family matched, not only the
  ones whose bounds moved, and reports the count. Without that the command is
  inert *here specifically*: every station is parked at the end of a fully
  consumed recorded window, so no poll will ever apply another document, and a
  limit armed after the readings landed would never be judged. `--dry-run` now
  answers the question actually worth asking before arming anything - **how many
  alarms does this file raise on the estate as it stands**. For the committed
  file: `evaluated : 28 machines, 0 breaching`.
- `import_pumphouse_dump` does the same. It is the path that really loads a
  station from a recorded window, so wiring only the poller would have closed the
  gap on the path that reads nothing and left it open on the one an operator uses.
- Auto-resolution stopped lying. `_auto_resolve_threshold_anomalies` is the only
  code in the backend that writes `RESOLVED`, and it closed *everything* it
  stopped matching with the note "Signal returned inside its configured limits" -
  including the case where activation had wiped all six bounds because a point's
  meaning changed. It asserted a recovery on a channel still reading 191 degC.
  There are now two notes, and the honest one says the condition was not observed
  to end. A bad-quality reading still holds its condition open, unchanged: a
  sensor going bad is not evidence either way.

Anomaly observation times are now presented in the same clock as the rest of the
blade. Nothing had ever needed this, because no anomaly had ever existed; the
first one the estate raised would have been dated 442 days before the signal row
that produced it, on the same screen.

**Stopped: the run-state gate.** Recorded, not built, and the research's own
example for it was wrong - see the correction in finding 3. Two further reasons
came out of review. The quality-side variant would **disarm 295 of the 307 armed
winding points**, because a stopped machine's winding temperature would be marked
unusable; that is a bigger hole than the one it closes. And a `/pd/Pn/st` run-state
reference does exist, agrees on all 30 bays and arrives in the same snapshot, so
if this is ever built the mechanism is available - it is not blocked on the plant.

**Stopped pending a decision: coercing the converter rails.** The finding is
solid and the fix is not obviously safe, which is a combination worth stating
rather than resolving quietly. Both reviewers would ship it; both attached
conditions that are somebody's call, not a reviewer's:

- It needs a companion data migration. `assets/migrations/0017_pegged_state_quality.py`
  exists for precisely this reason and says so: the window is exhausted, a
  checkpoint cannot rewind, and `_is_replay` drops a re-read, so a coercion rule
  alone changes nothing already cached - which is all of it.
- The "no real reading can reach this value" argument that justifies `3276.7` is
  **not available** here. This repo's own recorded decision of 2026-09-26
  approved the vibration channels as "raw, unitless source values... may be
  signed", *because* 10-34% of readings are negative. So the rule rests on the
  lattice and the one-count rail pair, not on impossibility - a closed list a
  human agrees to, not a test.
- It blanks Millbrook Pump 05. All five of its vibration channels are railed, it
  is the estate's only running bay, and the Performance panel's family tile and
  sensor-group summary both drop non-good readings - so that group goes empty on
  the one machine anybody is watching. Coercing `PUMP5_SPEED` too (the x8 scalar)
  makes it worse before it makes it better: the bay would show
  `MOTOR_ON_STATUS = 1` beside a blank speed.

The honest reading is that the whole P5 block is one railed acquisition, and the
right fix is probably to say *that* - a whole-block sentinel, annunciated once -
rather than to mark seven channels bad and leave `MOTOR_ON_STATUS` asserting a
running machine.

---

## The one to send first

Question 1 below is the only item on this whole list that costs five minutes
instead of a document request, and it is the one that decides whether the 307
limits now live are right. Ready to forward as-is:

> **Subject: One photo needed - motor rating plate, Saraswati or Parvathi**
>
> Could someone photograph the rating plate on any one of the 40 MW pump motors?
> Any machine will do - they are identical, so whichever is easiest to reach.
>
> The whole plate please, straight on and in focus, rather than a typed extract.
> It carries several things we need and we would rather read them than retype
> them.
>
> Why: the condition-monitoring dashboard now raises a winding-temperature alarm
> at 125 degC and a critical at 145 degC. Those came from the standard using an
> *assumed* insulation class F with temperature rise limited to class B. If the
> plate says otherwise the alarm points move by up to 25 degC - in one direction
> we get nuisance alarms, in the other a hot machine stays quiet - so we would
> rather read the plate than keep the assumption.

**Ask for the whole plate, not the two lines.** A rating plate normally carries
insulation class, temperature rise, rated voltage, rated current, duty and
design ambient - and IS/IEC 60034-1:2004 cl. 10.2 item 8 *requires* it to state
the thermal class and, printed separately, the rise limit when that is lower.
The information is on the plate by law, not by luck.

**But ask each pump house separately: three plates, not one.** An earlier
version of this line said one photograph answers questions 1, 2 and 3. It does
not. CAG's audit places BHEL as the pump and motor supplier for Packages 6, 8,
10 and 11, while Medigadda, Annaram and Sundilla were let as separate contracts,
so the OEM for Parvathi and Saraswati is unestablished - and those two carry
**275 of the 307 armed winding points**. Treat each plate as covering only its
own station. See "Searched the public record" below.

**Photograph the measurement method too.** BHEL's own specifications state rise
*by resistance*; the 125 here rests on the *embedded detector* column of Table 7.
Those are different quantities, and a plate figure read without its method could
move the band in the wrong direction.

**Ranganayaka Sagar needs its own plate.** Those are a different machine - the
catalogue records 134 MW with a different detector layout - and all 32 of their
winding points now carry the same 125/145 band, which was derived from the
*Annaram* assumption. That makes the second photograph worth more than it was
when the points were simply unlimited: the band is now live on a machine nobody
has checked the class of. Still not urgent, because the assumption errs early
rather than late, but it is no longer free.

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
   slip-ring or a shaft-grounding brush. (Likely **synchronous**: CAG records SFC
   starting equipment at Annaram and Sundilla, and BHEL's lift-irrigation line
   above 25 MW is salient-pole vertical synchronous. Neither is conclusive - an
   SFC can start a large induction machine too - so the question stands, but it
   is now a confirmation rather than an open choice.)
8. Four motor data-sheet rows would replace most of the judgement in the cooling
   families: max permissible cooling water inlet and outlet, cold air entering,
   hot air leaving. (The *design ambient* half of this question is withdrawn:
   IS/IEC 60034-1 Table 9 item 1c reduces the permitted rise by any excess above
   40 degC, so the absolute ceiling is the same whether the machine was designed
   to 40 or to BHEL's usual 50.)
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

### Added by the second pass

17. **What does the vibration channel actually carry?** The 158 points are
    unscaled converter counts (finding 2 above), so the reviewed unit is
    `unitless` and nothing can be set on them. Needed: the measured quantity
    (velocity mm/s r.m.s.? displacement µm peak-to-peak?), the converter's
    engineering range and range scalar, whether the zero offset is trimmed -
    29 of 158 sit a few counts either side of zero - and the transducer type
    and mounting. Without the quantity, ISO 20816 cannot be entered at all;
    with it, the part still has to be settled (Part 5 for pumping plants, or
    Part 7 for rotordynamic pumps - cl. 1 routes these machines to both).
18. **Why is every vibration channel on the one running bay railed?** All five
    of Millbrook/Saraswati Pump 05's vibration points sit exactly on the
    positive rail. That is the only loaded data the estate has, so this single
    question decides whether a baseline is obtainable from the historian at all
    or whether question 15 needs a fresh capture.
19. **Which of the two Parvathi thrust families is on the pad?** They disagree
    by +11.2 K at standstill (finding 4). `THRST_BRG_..._PROCESS_VALUE` looks
    like a separate condition-monitoring rack; `PUMP_THRUST_AXIAL_PAD_*` looks
    DCS-side. One band cannot span both.
20. **Forebay level: datum and civil limits, per station.** (a) Is the value
    metres above MSL or a local gauge zero - Cedar Creek reads 132.55 m against
    a published FRL of 130 m for Sundilla/Parvathi, so either the barrage runs
    above FRL post-monsoon or the datum differs. (b) MDDL and the minimum
    forebay level at which the pumps retain submergence/NPSH, per bay if it
    differs. (c) The high level at which pumping must stop. (d) Maple Grove /
    Ranganayaka has no forebay tag bound at all - is one available? This is the
    **only** hydraulic quantity where an unconditional threshold is physically
    sound, because a barrage pond has a level whether or not a bay runs.
21. **Is Parvathi's cooling circuit hot, or scaled differently?** At the window
    end, 13 stopped Parvathi bays read cooling-water inlet 38-48 °C while the
    one loaded Saraswati bay (24.5 MW) read 29.3 °C, and Saraswati's stopped
    bays read ~31 °C on water but ~41 °C on cold air. Was Parvathi shut down
    within hours of this window - a genuine hot soak - or do its cooling
    channels carry a different scale, offset or datum? Until this is answered no
    cooling baseline is trustworthy enough to place a limit against, which is a
    stronger reason to hold than the first pass's "it would fire today".
22. **Is Ranganayaka's cooling circuit instrumented differently?** Its four
    `COOLING_WATER_RTD` points are the station's only cooling-water tags - it has
    no inlet or outlet tags bound at all. So question 13 widens: not just
    "upstream or downstream of the cooler" but "are this station's inlet/outlet
    tags missing from the dictionary?" Consistent with it being the different
    134 MW machine.

**Question 2 is withdrawn.** Table 9 item 2's arithmetic makes the applied
winding ceiling 125 °C for every coolant temperature, so the "P" or "S" marking
does not move the band. Question 1 still swings it by 25-30 K.

**Question 15 is now the highest-value item after question 1.** Three separate
families - vibration, bearings, and every `normal_max` in the table - are blocked
on the same thing: one clean loaded window. CIGRE 558 §A4 describes exactly what
it would buy, and warns why the vendor's own figures are not a substitute:
"These prescribed alarm levels are in most cases much higher than the maximum
that the motor temperature ever reaches when in service. Once alarm levels are
reached, significant damage was already caused to the motor."

---

## Every bound channel's observed range, 2026-09-28

The winding and core screens each read whole documents and discarded every tag
but one family. Each document carries all ~900 tags, so surveying **every** bound
channel costs the same reads. 1,386 of 1,452 bound channels, 78 / 77 / 17
documents across the three stations at a 3-hour stride over the 238-hour span.
(The missing 66 are `/pd/` tags that do not live in the `dex` extension.)

| verdict | channels | meaning |
|---|---:|---|
| varies | 1,116 | a moving measurement |
| constant | 174 | one distinct value across 238 hours |
| signed | 57 | negative on a unit that cannot be negative |
| railed | 21 | on a converter full-scale rail |
| pegged | 18 | the 3276.7 over-range marker dominates |

**Read the first two carefully; a naive reading of them is wrong.** 58 of the
174 "constant" channels are `MOTOR_ON_STATUS` and `MOTOR_OFF_STATUS`, and 28
more are `ACTIVE_POWER` at 0.0. Those are *correct*: the bays are stopped for the
whole window, so a status bit that never changes is reporting accurately.
Likewise 85 of the "signed" channels read between -5 and 0 - speed at -1.69 rpm,
discharge pressure at -0.03 mH2O - which is transducer offset near zero on a
stopped machine, not a fault. Counting either as broken would have overstated the
problem by a factor of three.

### What is actually wrong

**One armed channel reports the over-range marker five readings in six.**
`PH_2 /dex/PUMP2_PUMP_MOTOR_WINDING_TEMPERATURED9`: 63 of 75 samples at 3276.7,
and it carried a live 125/145. It is now excluded.

That is a **defect in the screening rule, not just one bad channel.**
`apply_winding_thresholds` skips pegged samples before counting
(`if _pegged(value): continue`) and then asks only whether at least
`MIN_SAMPLES` usable readings remain. It never asks what *fraction* of the total
those are. A channel that is pegged 84% of the time and plausible the rest
therefore passes as clean. Any future family screened the same way inherits this.

**Ten channels report impossible temperatures**, none of them currently armed,
all of them future exclusions for whichever family arms them:

| station | channel | observed |
|---|---|---|
| PH_2 | `PUMP2_..._TEMPERATURED2` | -188.90 .. -81.60 |
| PH_2 | `PUMP7_..._TEMPERATURED10` | -161.60 .. -64.90 |
| PH_3 | `PUMP6_PUMP_COOLING_WATER_INLET_TEMP2` | -140.20 .. 3276.70 |
| PH_3 | `PUMP4_MOTOR_CORE_RTD2_PROCESS_VALUE` | -242.10, constant |
| PH_3 | `PUMP10_PUMP_COOLING_WATER_OUTLET_TEMP4` | -43.00 .. -6.20 |
| PH_2 | `PUMP2_PUMP_COOLING_AIR_ND_END_RIGHT_COLDD14` | -30.30 .. 3276.70 |
| PH_2 | `PUMP6_PUMP_COOLING_WATER_OUTLET_TEMP1` | -13.90 .. -12.20 |

Seventeen further channels are pegged outright - 3276.7 on every sample taken -
across cooling water inlet, outlet, inlet-water and cold air. None is armed yet;
all of them would need excluding the day a cooling family is.

### Two bays that do not behave like their neighbours

Constant-channel fraction, same station, same 75 samples, same hall:

| bay | constant / total | |
|---|---|---|
| PH_2 Pump 05 | **35 / 37 = 95%** | the known frozen block, confirmed from a second angle |
| PH_3 Pump 02 | **29 / 56 = 52%** | **new** |
| PH_3 Pump 01 | 4 / 56 = 7% | a healthy sibling, for comparison |

Pump 02 at Cedar Creek is not a whole-bay freeze. It is a *subset*, and the
subset is informative: `MOTOR_CORE_RTD1` through `RTD4` move across 7-8 distinct
values while `RTD5` and `RTD6` sit dead still, on the same bay, in the same
family. Five winding detectors, nine cooling-water channels, four cold-air
channels and two thrust-pad RTDs are likewise frozen while their siblings drift.
That is individual stuck detectors, not one failed card.

**Nineteen armed channels are among the frozen**, twelve of them on Millbrook
Pump 05 and seven on Cedar Creek Pump 02. They are **deliberately left armed**,
and the reasoning is worth stating because it is not obvious. A frozen detector
under-reports, so the risk it carries is a *missed* alarm rather than a nuisance
one - and excluding it does not restore protection, it only records that there is
none. Since the hall drifts by less than a kelvin over ten days, "stuck" and
"genuinely stable" cannot be separated from a 3-hour stride with certainty. The
honest position is that these are flagged and not yet settled; a denser walk over
one bay would settle it cheaply, and the plant can settle it instantly.

### And a defect the survey exposed in the limits file itself

**An exclusion did not disarm.** Adding a channel to an `exclude` list skipped it,
leaving whatever bounds it already carried - so the file could say a channel was
disarmed while the database still held 125/145. The one artefact that is supposed
to be the authority on limits was authoritative in only one direction. Excluding
now clears all six bounds and reports `disarmed`, and four tests pin it,
including that a `--dry-run` reports the disarm without performing it.

---

## Searched the public record, 2026-09-28

Four independent source classes - the OEM, project and regulatory documents,
technical literature, and the standards themselves - each searched and then
re-checked by a second pass that re-downloaded every cited document and
confirmed every quote in place. **No angle produced a figure. All four
"constrain".**

**The decisive negative.** CAG's Kaleshwaram audit is public, detailed, and
quotes contract terms verbatim, so it was the best candidate. It contains
**zero** occurrences of *insulation*, *temperature rise*, *class F*, *class B*,
*CACW*, *CACA*, *IS 325*, *60034*, *IS 5120*, *winding*, *stator* - or even the
bare word *temperature*. Twelve greps, 0/0 across both chapters, re-run by the
checker. Nothing in the regulatory record establishes the thermal class of these
machines, and **125/145 remains derived rather than read.**

### What the search did establish

**The assumption is the standard Indian convention, and it now has a statutory
citation.** CEA (Technical Standards for Construction of Electrical Plants and
Electric Lines) Regulations - r.37(2) in the 2010 edition, r.40(2)(a)(ii) in
2022 - require class F insulation with temperature rise limited to class B for
large vertical machines in a water powerhouse. The Government of Maharashtra's
Water Resources Department specifies the same pairing for a vertical
lift-irrigation pump motor, and BHEL's own HT-motor specification states it as a
blanket house rule: *"HV/MV/LT motors shall have class F insulation. The
temperature rise of all motors shall be limited to the limits applicable to
Class 'B' insulation."*

Read that for what it is. Three *organisations*, not three independent
derivations - it is one long-standing Indian specification convention, quoted
three times. And the CEA regulations' own applicability clause covers generating
companies, transmission licensees and distribution licensees, so their reach to
a lift-irrigation pumping station is arguable rather than settled. What this
changes is the *status* of the assumption: it was a guess, and it is now the
documented norm for this duty in this country. It is still not this machine's
plate.

**The dangerous branch is weakened, not closed.** BHEL's product literature
gives *"Insulation System Class F/ H"* and never mentions class B. That is an
argument from silence in a four-page marketing flyer whose own published range -
synchronous to 25,000 kW, vertical lift-irrigation "upto 15 MW" - excludes both
the 40 MW and the 134 MW machines, and which post-dates the KLIS supply. It is
context, not proof. **Class B insulation, the case in which 125/145 warns late,
is made unlikely by convention and is not ruled out by evidence.**

### Two questions retired, by arithmetic rather than by a document

**The design ambient does not move the alarm point.** IS/IEC 60034-1 Table 9
item 1c: where a maximum ambient above 40 degC is specified, the permitted rise
is *reduced by the excess*. So a machine designed to BHEL's usual 50 degC
ambient has the same absolute ceiling as one designed to 40. This is the same
structure as the Table 9 item 2 result that retired question 2, and it removes
the design-ambient half of question 8.

**The coolant datum does not move it either**, for the same reason: Table 9 item
1d increases the limit by 15 K and then adds or subtracts the difference from
25 degC, so the ceiling is invariant in the water inlet temperature.

### One trap this search found, worth more than a citation

**BHEL states temperature rise *by resistance*; the 125 comes from an *embedded
detector* limit.** BHEL's general motor specification fixes "70 deg. C by
resistance method for both thermal class 130(B) & 155(F) insulation", and 80 K
by resistance over inlet cooling water for water-cooled machines. Those are mean
winding temperatures. The 85 K of IS/IEC 60034-1 Table 7 item 1a that this
table's 125 rests on is the **ETD** column - the hottest spot a detector sees.
The two are different quantities and must never be compared directly, and a
plate photograph must therefore be read for the *measurement method* alongside
the number. IS 12802:1989 cl. 5.3 makes the ETD method mandatory by default for
AC stator windings, which is what licenses Table 7's ETD column here.

Useful consequence: **the plate is legally required to carry the answer.**
IS/IEC 60034-1:2004 cl. 10.2 item 8 requires the rating plate to state the
thermal class and, printed separately, the limit of temperature rise when it is
lower than that class. So the photograph is not a hopeful request - the
information is required to be on the plate by the standard the machines were
built to.

### And one correction to the ask itself

**"One photograph answers four questions" was wrong, and the procurement record
is why.** CAG places BHEL as the pump and motor supplier for Packages 6, 8, 10
and 11. The Medigadda, Annaram and Sundilla lifts were let as separate contracts
- CAG: *"in the three LS contracts relating to..."* - so **the OEM for Parvathi
(Sundilla) and Saraswati (Annaram) is not established**, in either direction.
News reporting says BHEL designed those motors too; that is press-release
material and does not meet this table's evidence bar, and the argument that they
are *not* BHEL is equally an argument from silence. Either way, the BHEL house
practice cited above cannot be assumed to reach the **275 of 307 armed winding
points that sit at Parvathi and Saraswati**.

So the ask is **one plate per pump house, three in total**, each treated as
covering only its own station. Note also that CAG never names a "Ranganayaka
pump house": the Package-11 134.44 MW x 4 to Ranganayaka mapping comes from
elsewhere and is not corroborated by the audit.

### Dead ends, recorded so nobody repeats them

- `cwc.gov.in/en/tac-meeting-minutes` - HTTP 401, confirmed twice.
- `bpl.bhel.com` - the BHEL Bhopal hydro pages are gone; HTTPS returns 403,
  plain HTTP 503. Reachable only through the Wayback Machine.
- `tgpcb.cgg.gov.in` - TCP timeout at 40 s.
- CBIP ICOLD 2021 proceedings - HTTP 404 on every paper path tried.
- Kaleshwaram's own Engineer-in-Chief published an ICOLD symposium paper on the
  project; it contains no electrical machine data at all.
- Academic indexes return two records for the barrage pump houses, and they are
  duplicates of one water-policy report with no electromechanical content.
- Every apparently independent "43 machines / 40 MW" report - PRNewswire,
  Business Standard, Siasat, Hans India and others - traces to a single MEIL
  press release. One source, many mirrors.

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
