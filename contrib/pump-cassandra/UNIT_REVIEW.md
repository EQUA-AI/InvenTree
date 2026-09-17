# Unit review for PH_3, from the observed data

I could not consult manufacturer documentation or standards texts directly while
doing this; there is no network access from the working environment. What follows
is therefore derived from two things: the actual values in
`PH_3.full-snapshot.json`, and the engineering conventions that govern this class
of machine. Where those two agree, the unit is settled. Where the data cannot
discriminate, the unit is **withheld with the reason recorded on the point**,
because a plausible label is not a measurement.

Reproduce the evidence with:

```zsh
python3 contrib/pump-cassandra/value_ranges.py
python3 contrib/pump-cassandra/decide_units.py
```

## The finding that governs everything else

**The reference snapshot was taken with the station shut down.** Every bay reports
`st=I`; `MOTOR_ON_STATUS` is 0 and `MOTOR_OFF_STATUS` is 1 for all fourteen;
active power, average current and line-to-line voltage are all exactly zero.

That single fact decides which units can be confirmed and which cannot. A unit is
a claim about magnitude - volts predicts ~11000 on this class of motor, kilovolts
predicts ~11 - and a stopped machine reads zero either way. Roughly a third of the
dictionary is in that position. Those tags were not approved. Calibrating a scale
against transmitter noise is guessing with extra steps, and the resulting unit
would look identical to a real one in the UI.

**A snapshot taken while pumping settles most of them in a single reading.** That
is the highest-value thing to obtain next.

## Confirmed, and why

| Family | Unit | Observed | Why the data settles it |
|---|---|---|---|
| All motor core / winding / bearing-pad RTD, cooling water, hot and cold air temperatures | `degC` | 35 to 45 typical | A machine soaking at ambient reads ~40 degC. The same body reads ~104 in degF and ~313 in K, so the scale is not ambiguous. |
| `PUMP_FREQUENCY` | `Hz` | 50.005 to 50.046 on six bays | Nominal grid frequency, sensed at the breaker while stopped. This fixes the scale outright. The bays reading 0 are isolated, not running at zero speed. |
| `EOPD_VALVE_POS_PROCESS_VALUE`, `HOPD_VALVE_POS_PROCESS_VALUE` | `percent` | clusters at ~100 and ~0 | Percentage of travel, pinned at the end stops. EOPD open, HOPD closed, which is coherent with an idle station. |
| `MOTOR_ON_STATUS`, `MOTOR_OFF_STATUS` | unitless | only 0 and 1 | Dimensionless by nature, and consistent with `st=I` on every bay. |
| `PUMP_POWERFATCOR` | unitless | 0 and 1.0 | A ratio of real to apparent power. Dimensionless by definition. |
| `COMMAN_FORBAY_LEVEL`, and `/sl` which equals it bit for bit | `m` | 132.0436 | An elevation above datum, not a depth. Already approved. |

479 points carry `degC`, 28 `percent`, 14 `Hz`. 581 of 905 points are now approved,
each with a note recording the observed range it was judged against.

## Withheld, and why

264 points. The reason is written onto the point itself, so it travels with the
thing it is about rather than living in this file.

**Blocked by the plant being idle.** `ACTIVE_POWER` (kW vs MW differ by 1000x),
`PUMP_CURRENT_AVG`, `PUMP_LINE_TO_LINE_VOLTAGE` (V vs kV), `PUMP_REACTIVE_POWER`,
`SPEED`, `DISCHARGE_PRESSURE` (bar, kg/cm2 and metres of head are all plausible and
all predict ~0 at rest). Every one of these reads zero or noise.

**Vibration**, seven families. All read between -0.18 and 0.48, which is noise
about zero. The **negative readings are themselves informative**: neither velocity
RMS nor displacement can be negative, so these are uncalibrated raw channels with a
zero offset. mm/s for the motor drive-end and non-drive-end channels matches both
the reference images and the ISO 20816 convention for this machine class, but it
should be confirmed against a running sample rather than assumed.

**`EXCITATION_FLD_CURR_PROCESS_VALUE`** is withheld for a different reason. Thirteen
bays read about 0 and P6 reads **1048.29** with its motor off. That anomaly needs
explaining before a unit is fixed to the tag.

## A correction to the earlier handover

I previously recorded that 70 points were "observed only as null". **That was
wrong.** The snapshot contains no nulls at all - all 845 `dex` values are JSON
strings. Those 70 points are `match_method: exact`, so their `data_type` came from
the **catalogue**, which deliberately declares these unknown and asks for the
quantity to be confirmed:

> `"tag": "PUMP_GUIDED_RADIAL_PAD_{channel}D6"`, `"data_type": "unknown"`,
> note: *"D6 is preserved as supplied; do not assume temperature or displacement units."*

So the open question was never the unit. It was **what the instrument measures.**

### What the data now says about the pad channels

The guide radial and thrust axial pad channels read **20 to 37 while the machine is
stopped**. Every genuine vibration channel on the same machine reads about zero. A
pad temperature sitting at ambient reads exactly this. Further, `PUMP5_PUMP_THRUST_AXIAL_PAD_1D5`
reads **-41.9**, matching the open-circuit signature of the RTDs elsewhere in this
payload (`MOTOR_CORE_RTD2` at -242.1, cooling water at -47.9 and -50.1).

That is evidence for temperature, and it is worth recording. It is **not**
confirmation: a proximity probe at rest can also read tens of micrometres. The
points stay withheld with that argument attached.

`SPIRAL_CASE1` separates from the pads cleanly. It reads ~0.01, near zero at rest,
which is pressure behaviour rather than temperature - if it were a temperature it
would read about 30 like its neighbours. The quantity is now likely settled; the
unit is not, because a pressure at rest cannot fix its own scale.

## Sensor faults found on the way

These are **value-quality** findings, not unit findings, and they do not change any
unit. They must not be clamped, discarded or rendered as normal readings.

- **`3276.699951171875` on four temperature channels.** This is exactly
  `32767 x 0.1` in float32 - the signed 16-bit maximum at 0.1 resolution. It is a
  saturated or open register, not a temperature. Affects
  `PUMP2_PUMP_COOLING_WATER_INLET_TEMP2`, `PUMP2_PUMP_PUMP_INLET_COOLING_WATER_TEMPERATURED3`,
  `PUMP3_PUMP_MOTOR_COLD_AIR TEMP1`, `PUMP5_PUMP_COOLING_WATER_INLET_TEMP5`.
- **Under-range readings** at -242.1, -118.5, -50.1, -47.9, -41.9: open circuit or
  uncalibrated span.
- **`PUMP9_EOPD_VALVE_POS` at -118.5 and `PUMP9_HOPD_VALVE_POS` at 141.8**, well
  outside 0-100 travel.

Alarm limits still require approved plant data. None of the above is a confirmed
fault; it is a list of readings that should not be believed at face value.

## What to obtain next, in order

1. **A snapshot taken while at least one bay is pumping.** Settles power, current,
   voltage, speed, discharge pressure and vibration in one reading.
2. **The instrument schedule for the `D5`/`D6` pad channels.** Settles temperature
   versus displacement, which the data can only argue for.
3. **`var` added to the unit registry.** Blocks reactive power and `/pmvar`
   regardless of what any sample shows.
4. **An explanation for P6's 1048 A field current with the motor off.**
