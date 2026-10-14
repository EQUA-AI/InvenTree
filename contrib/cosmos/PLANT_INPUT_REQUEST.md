# Plant input request - pump-station monitoring (2026-10-14)

Four items need information only the plant can provide. Each is listed with the exact
question, what we already know, and what we will change when the answer arrives.
Nothing below has been guessed into the system: every item is held at a safe default
until answered.

Stations: Millbrook (60), Cedar Creek (93), Maple Grove (94), Pine Hollow (230).
Recorded data: 2025-07-01 03:00 to 2025-08-01 02:59 UTC. Every pump at every station
is stopped for the whole window.

---

## 1. Mimic layout approval

**Ask:** the installation drawings or reference screenshots of the existing station
HMI/SCADA mimic, one overview and one pump unit, for each station. Then a named
plant engineer confirms the placement.

**What we have:** `src/backend/InvenTree/machine_health/layouts/pumphouse.layout.json`
and the two SVGs in `src/frontend/src/assets/mimic/` are generic schematics drawn on
2026-10-02 (station: forebay, suction header, discharge header; pump: motor, stool and
bearing, shaft, casing, discharge valve). No reference image has ever been supplied.

**Please confirm on the drawing:**
- Position of forebay level, station status, pumps running.
- Per pump: status, input power, shaft speed, discharge pressure, flow.
- Placement of `PUMP_GUIDE_BEARING_PAD` and the HOPD/EOPD valve order.
- Readings a station does not publish are shown as "Not published by this station":
  Pine Hollow - forebay level, active power and discharge pressure on all 4 pumps;
  Maple Grove - forebay level; Cedar Creek - pump 7 discharge pressure. These are missing
  from the published data only. For each one, please tell us whether it is not installed,
  not wired, not configured in the PLC, or not published on OPC UA.
- For each forebay transmitter: the unit and datum (MSL or a local gauge zero), the minimum
  drawdown level, the minimum level for safe pumping (from NPSH and submergence), and the
  high level at which pumping stops.

**On approval:** set `"review_status": "approved"` in the layout file (code change and
redeploy). That is the only thing keeping all 4 stations from reading as ready.

---

## 2. Pine Hollow withheld points

### 2a. Current and voltage (8 points)
`PUMP1..4_PUMP_CURRENT_AVG` and `PUMP1..4_PUMP_LINE_TO_LINE_VOLTAGE`, mapped to
`PS-ELECTRICAL` but withheld: they read 0 for the whole window because no pump ran,
so the unit (A, V or kV) cannot be confirmed.

**Ask (either):**
- A few days of Cosmos data with at least one Pine Hollow pump running, or
- The motor data sheet or the PLC scaling: units and rated values for motor current
  and line-to-line voltage.

### 2b. `PUMP_GUIDE_BEARING_PAD_TEMP1-8` (32 points)
Pine Hollow publishes three guide-bearing pad series per pump:
`UPPER_GUIDE_BEARING_PAD_TEMP1-8`, `LOWER_GUIDE_BEARING_PAD_TEMP1-8` (both approved) and
an unprefixed `GUIDE_BEARING_PAD_TEMP1-8` (withheld). The catalogue only has Upper and
Lower.

**Ask:** the site P&ID or I/O list. Which bearing does the unprefixed series measure?
Is it a third bearing, or a duplicate of Upper or Lower? Note that its even-numbered
channels (and pump 3's channel 3) read only -1 or 0 for the whole window (look unwired).

**On answer:** add the mapping or withholding to
`contrib/cosmos/review/pine-hollow.review.json`, dry-run and apply with
`apply_dictionary_review` after a backup.

---

## 3. Motor nameplate insulation class (THRESHOLDS.md question 1)

**Ask:** a photo of the motor nameplate, or the data sheet, giving the thermal class
and the rated temperature rise, for each station's motors.

**What we assumed:** class 155(F) insulation with rise limited to class 130(B). The armed
stator-winding limit is warning 125 °C, critical 145 °C, with 2 sensors required to
agree. Together with the core backstop in item 4 it is armed on 451 bindings across the 4 stations.

| Nameplate says | Winding limits become | Effect of today's setting |
|---|---|---|
| Class F, rise limited to B (assumed) | 125 / 145 °C | correct |
| Class F, full F rise | about 150 / 155 °C | today warns about 25 K early |
| Class B | about 110 / 125 °C | today warns late, so needs changing |

Also useful (questions 4 and 5): whether the purchase spec invoked IS/IEC 60034-1 or
NEMA MG 1 / API 541, and any OEM alarm/trip figures in the O&M manual (those outrank
the standard).

**On answer:** update the winding row in `contrib/cosmos/limits/pumphouse.limits.json`
and re-run `apply_signal_limits` per station (dry run, backup, apply).

---

## 4. BHEL heat-run / type-test report (THRESHOLDS.md question 6)

**Ask:** BHEL's type-test or heat-run report for the motors, giving the measured winding
and stator-core temperature rise at rated load.

**What we have:** the core RTDs run on a backstop that reuses the winding figures
(125 / 145 °C). No standard sets a core limit; IS/IEC 60034-1 has no core row. The
backstop is safe but late: it will not catch a core slowly running warm. The hottest
core reading recorded is 67.7 °C.

**On answer:** replace the "Motor / stator core RTDs (backstop)" row in the limits file
with a band derived from the measured core rise, then re-run `apply_signal_limits`.

---

## 5. Pump speed reading, Millbrook Pump 05 (`PUMP5_SPEED`, 474 rpm)

**Status: unverified.** The value is exactly +32767 counts, the signed 16-bit maximum, so
saturation, clipping or a scaling error has not been ruled out. Earlier review notes for Maple
Grove and Cedar Creek called it "corroborated"; that is withdrawn. Please send:

1. A PLC trace or raw historian export of the raw count, to show whether +32767 is the
   converter's actual output or added by the PLC.
2. The converter model, configuration, and input and output ranges, and what its maximum count
   means (full scale, over-range or a fault code).
3. The PLC scaling expression from counts to rpm.
4. The motor nameplate (poles, frequency) or drive data for the expected speed range.
5. Drive actual-speed feedback or a tachometer reading taken at the same time.
6. If possible, raw counts at several operating speeds, to show whether the value moves or stays
   at the maximum.

We will mark the reading validated only when the scaling is confirmed and it agrees with the
independent reference within an agreed tolerance (STANDARDS_BASELINE.md section 8a).

## Also helps every item above

**Can one loaded bay be recorded cleanly for a few weeks?** (THRESHOLDS.md question 15.)
All recorded data is from a stopped plant. One running window would give normal operating
bands and make the bearing limits meaningful. On its own it cannot confirm the electrical
units: please send the nameplate, CT/VT ratios, PLC scaling and one switchgear meter reading
taken at the same time. Keep the raw values, OPC UA status and source timestamps.

**Data freshness.** The newest record in Cosmos is 2025-08-01, which is 439 days old on
2026-10-14. The app currently shows that recorded window as if it were current. For live
monitoring, please confirm which layer stopped: PLC/OPC UA source, ingestion, storage, or the
Cosmos feed. The quickest check is to follow one known pump tag through each layer.

Engineering basis for each ask: `contrib/cosmos/STANDARDS_BASELINE.md`.
