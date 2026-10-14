# Standards baseline for the pump-station data (2026-10-14)

What published standards settle for Millbrook (60), Cedar Creek (93), Maple Grove (94) and
Pine Hollow (230), and what only the plant can settle. Read with `THRESHOLDS.md` (what is
armed and why) and `PLANT_INPUT_REQUEST.md` (what we are asking for).

Each answer is tagged:

- **Standard-based** - a published standard or basic engineering relationship supports it.
- **Provisional** - a working hypothesis. It is used in the system and labelled as such.
- **Site-specific** - needs the nameplate, OEM documents, PLC configuration, plant drawings or
  a measured reference. No standard can answer it.

**Main conclusion:** standards give validation rules (thermal classes, vibration zones, OPC UA
data quality, intake design). They do not give tag meanings, rated voltages, sensor locations,
heat-run results or site operating limits. Nothing below adds a number to the system.

---

## 1. Standards that apply

| Standard | Used for | Note |
|---|---|---|
| IEC 60034-1 (IS/IEC 60034-1) | Motor rating, temperature rise | The 2026 edition is published, but the **edition in the purchase contract** governs existing machines. Do not recalculate to the newest edition. |
| NEMA MG 1 | Motor design, temperature rise | Only if the motors were specified to it. Use the applicable part and table. |
| API Std 541 | Form-wound squirrel-cage **induction** motors ≥ 375 kW | Not a general pump-motor standard. Does not govern a synchronous motor. |
| ISO 10816-7:2009 | Vibration of rotodynamic pumps, on non-rotating parts | Current. ISO/AWI 20816-7 is replacing it. |
| ISO 20816-5:2018 | Hydraulic generating and pump-storage machine sets | Excludes pumps in ordinary industrial installations. Which of the two applies depends on the machine category. |
| OPC UA Part 8 | `EngineeringUnits`, `InstrumentRange`, `EURange` | Some properties are optional. Where missing, take them from PLC documents. |
| OPC UA Part 4 | Status codes, source/server timestamps | Check status before using a value. |
| ANSI/ISA-5.1-2024 | Instrument identification, P&IDs | Basis for tag-to-instrument mapping. |
| ISA-101 | HMI design and lifecycle | Does not certify that a mimic matches the plant. |
| ANSI/HI 9.6.1-2024, ANSI/HI 9.8 | NPSH margin, intake submergence | Safe level depends on NPSH, submergence and intake design. There is no generic level. |

---

## 2. Winding temperature limits

**Status of the armed 125 / 145 C: provisional, not OEM-approved.** Warning 125 C, critical
145 C, critical needs two sensors to agree (`vote_minimum: 2`). These are the configured values,
not established safe limits for the Maple Grove or Pine Hollow motors. Both rows in
`limits/pumphouse.limits.json` carry `"status": "provisional"` and `"approved_basis": null`, and
`apply_signal_limits` prints a `PROVISIONAL` line for them on every run.

| Design basis | Figures in the notes | Validation result |
|---|---|---|
| Class F insulation, rise limited to class B | 125 / 145 C (armed) | Not confirmed for these motors |
| Full class F rise | about 150 / 155 C | Not approved by that assumption alone |
| Class B insulation | about 110 / 125 C | Not approved by that assumption alone |

These are not interchangeable OEM alarm/trip settings. Insulation thermal class, permitted
temperature rise, measured winding temperature and embedded-RTD alarm/trip limits are four
different things. The 134 MW rating of the Maple Grove motors does not set their thresholds, and
the same holds for Pine Hollow.

**Do not raise, lower or disable the armed settings on these assumptions.** They stay armed until
the responsible electrical/protection engineer reviews them. Being armed does not make them
OEM-approved.

**Evidence needed to approve each limit (125 C and 145 C separately):**

1. OEM temperature alarm/trip schedule for the specific motor, with sensor locations and
   thresholds.
2. Motor nameplate and data sheet: insulation class, cooling arrangement, rated conditions.
3. BHEL heat-run or type-test report, where applicable, for the temperature-rise basis.
4. Approved contract specification and any project protection settings.
5. Confirmation that the limits apply to the actual motor, winding sensors and measurement
   method.

**How approval is recorded.** Set `"status": "approved"` only when that evidence is in hand, and
fill `approved_basis` with the exact document, revision, section/table and what it applies to:

```json
"status": "approved",
"approved_basis": {
  "document": "<OEM document identifier>",
  "revision": "<revision>",
  "section": "<section or table>",
  "applies_to": "<station, motors and sensors confirmed>"
}
```

`apply_signal_limits` refuses `"approved"` if any of the four fields is missing or empty, and
refuses a free-text or `null` basis. Where stations or motors have different schedules, split the
row per station rather than approving one row for all of them.

**Standard-based:**

- Thermal classes: B = 130 °C, F = 155 °C, H = 180 °C. These are **classification temperatures**,
  not RTD alarm or trip settings.
- Example only, air-cooled, 40 °C reference, resistance method: about 80 K rise (B) and
  105 K (F), so average winding about 120 °C and 145 °C. This is not a protection-setting
  calculation. Embedded-detector limits, rated voltage, coolant and standard-specific
  adjustments change it.

**Not standard-based (corrected in THRESHOLDS.md and PLANT_INPUT_REQUEST.md):**

- "Full class F rise → 150 / 155 °C" and "class B → 110 / 125 °C" are illustrations,
  not derived settings. A Class F marking alone does not authorise a 155 °C trip.
- "NEMA / API → 120 / 145 °C" does not apply. Use whichever standard the contract names, not
  whichever gives the most convenient alarm.

**Site-specific:** the motor-specific RTD alarm/trip schedule from the OEM or the approved design
basis, for each station. If no schedule exists, get a formal recommendation from the OEM or the
responsible electrical engineer, and record the uncertainty. This applies most to the 134 MW
Maple Grove machines, where voltage, cooling and the measurement method can change the
permitted temperature rise.

---

## 3. Pine Hollow instrumentation register

| Item | Answer | Tag |
|---|---|---|
| Current / L-L voltage units (8 withheld) | Units must be documented (A/kA, V/kV). Scaling must be applied before a value is called an engineering unit. Reference: nameplate, CT/VT ratios, PLC scaling and a loaded comparison with the switchgear meter. Cross-checks: `P_in = √3·V_LL·I_L·PF` and `P_out = P_in·η`. These are plausibility checks, not calibration. | Site-specific |
| `PUMP_GUIDE_BEARING_PAD_TEMP1-8` (32) | No standard sets the sensor count or channel order. Reconcile against the bearing assembly drawing and the I/O list. Being a separate software series does not make it a third bearing. | Site-specific |
| Channels reading only −1 or 0 (6) | Neither value is a universal OPC UA fault code. Treat bad-quality status as unusable. A good-quality 0 or −1 is a reported number until the plant confirms a sentinel, and then only for those tags. Wiring status needs the plant. The software does **not** treat −1 as a sentinel. | Standard-based rule; site-specific answer |
| Winding RTD 13-18 (24) | No standard requires 12 or 18. Reconcile installed sensors ↔ terminal box ↔ PLC I/O ↔ OPC UA ↔ catalogue. Classify each as wired, spare, uncommissioned, duplicate, mislabelled or legacy. Do not drop the points because the catalogue ends at 12. | Site-specific |
| Barrel air (16) | Find where each sensor sits in the cooling circuit (cold-air inlet or hot-air outlet), using the OEM cooling schematic. A single universal limit is wrong. | Site-specific |
| Per-phase I/V (24) | One row per phase: phase, quantity, unit, range, CT/VT ratio, PLC scale, source, commissioning status. Averaging must not hide a zero on one phase. | Standard-based structure |
| `/pmw`, `/pmvar`, `/sl` | `/pmw` and `/pmvar` look like active and reactive power. **Provisional** until the tag dictionary confirms the unit and sign convention. `/sl` is unidentified at Pine Hollow. | Site-specific |
| 295 OPC UA node ids | Needs a point schedule or address-space export: NodeId, equipment, `EngineeringUnits`, `InstrumentRange`, `EURange`, scaling. Ask for CSV or Excel. | Site-specific |
| Forebay level, active power, discharge pressure | "No verified published tag in the available source." That does **not** mean the instrument is missing on site. The mimic label "Not published by this station" means only this. | Standard-based wording |

---

## 4. Mimic sign-off

The generic layout stays `provisional` until a named plant engineer confirms it (ISA-5.1 for
identification, ISA-101 for display). Neither standard replaces a physical check.

The approval package needs, for each station:

- The overview drawing or SCADA screen.
- The pump-unit drawing or screen.
- P&ID and I/O references.
- Engineer sign-off on reading locations and the HOPD/EOPD order.
- A missing-point register classifying each point as not installed, not wired, not configured,
  not published or unavailable.
- A revision record.

To approve, change `pumphouse.layout.json` as a controlled change: backup, dry run, then a
post-deployment check.

---

## 5. Stator core

**Status: provisional backstop.** The winding figure is a temporary monitoring fallback, not a
validated core alarm. The core and winding heat differently. A core can be abnormal compared
with its baseline while still below the winding threshold.

**67.7 °C is a reading on a stopped plant, not a limit.**

The heat-run report needs to give:

- Core sensor location.
- Temperatures before and during the test.
- The reference coolant or ambient temperature.
- Steady state at rated load.
- Test method and report revision.
- The OEM permissible limit and the action to take.

Another motor's report cannot be used for these machines.

---

## 6. Rated voltage, motor type, governing standard

| Question | Answer |
|---|---|
| Maple Grove rated stator voltage | Cannot be worked out from 134 MW. Get it from the nameplate or data sheet, the single-line diagram, or the switchgear documents. |
| Induction or synchronous | An OEM design fact. Do not infer it from the rating or from tag names. It decides how speed and slip are read. |
| IEC, NEMA or API | Whatever the purchase contract and motor documents name. API 541 is for induction motors only. |

---

## 7. Vibration

**Reference only, not armed.** ISO 10816-7:2009 zone boundaries, velocity in mm/s RMS on
**non-rotating pump parts**, for pumps above 200 kW:

| Boundary | Category I (critical) | Category II (general) |
|---|---:|---:|
| A/B | 3.5 | 4.2 |
| B/C | 5.0 | 6.1 |
| C/D | 7.6 | 9.5 |

These boundaries cannot be copied onto the 158 channels yet. The channels carry unscaled
converter counts (THRESHOLDS.md finding 2), and the quantity, location and category are unknown.

Each channel needs:

- The quantity: velocity, displacement or acceleration.
- Unit and convention (mm/s RMS, µm p-p).
- Transducer model.
- Mounting position and orientation.
- Frequency response and converter range.
- PLC scaling, filtering and quality.
- The applicable standard and the OEM criteria.

**Millbrook Pump 05, every channel at full scale:** the data cannot yet tell high vibration
from a measurement fault. Trace the signal in this order:

1. Raw PLC or converter input.
2. PLC engineering value.
3. OPC UA value.
4. Stored and displayed value.
5. Clipping, range, scaling, diagnostics and mounting.
6. An independent measurement.

When every channel stops at the same bound, check the shared scaling first. Do not suppress
the alarms or widen the display ranges to hide the flat line.

---

## 8. Millbrook, Cedar Creek, Maple Grove

| Item | Answer |
|---|---|
| `MV_PMP_DRF_TB_PT`, `OL_SMP_BRG_LT`, `TOP_CVR_DRNG`, `VS_NDE_BG2_M`, `WT_CDIN_WT1_M`, `WT_CDIN_WT2_M` | Do not guess from the abbreviation. Needed from `PS1_PROG_19_4_2019` or MEIL: symbol and comment, data type, unit, I/O channel, scaling, location and dependents. Also confirm the "blocks 72 points" claim from the dependency map. |
| Maple Grove Pump 02, 63 RTDs in July 2025 | Answer from that period's records: module diagnostics, commissioning, raw values with quality, loop tests. Keep three things apart: wired, configured and valid. |
| Zero current, kvar and PF on a loaded pump | Check the meter, CT/VT, scaling, quality and mapping first. Do not compute a PF from invalid inputs. |
| `PUMP5_SPEED` 474 rpm | **Unverified.** See section 8a. |
| Guide pads 5 °C below thrust pads | No universal rule. Use the bearing design, OEM limits and the verified sensor positions. |
| Cedar Creek thrust families 11 °C apart at standstill | First establish whether both measure the same thing at the same place. Standstill data alone does not show which is right. |
| Cooling-water inlet 38-48 °C on stopped pumps | Heat soak is possible but not shown. Compare inlet and outlet against pump status, time since shutdown, flow and sensor position, then check scale and mapping. |
| Maple Grove cooling-water tags, winding counts | Find out whether the tags are missing from the PLC, not published on OPC UA, or missing only from our dictionary. No standard requires six or more winding sensors. Check the motor's own schedule. |
| Hot air 73-74 °C | Unresolved. Compare with OEM hot-air limits, cold air, cooling water, load and the raw channel. If it is flat through start, load and stop, suspect a stuck or mis-mapped channel. |

---

## 8a. RPM validation - Maple Grove, Pine Hollow and Cedar Creek

**Status: unverified. Validation pending.**

The 474 rpm reading is not corroborated. The reported raw value equals +32767 counts, the maximum
positive value of a signed 16-bit integer. Converter saturation, clipping and scaling errors have
not been ruled out. Validation remains pending review of the source data, converter
configuration, PLC scaling and an independent speed reference. The reading must not be treated as
a verified RPM value until these checks are complete.

Where the reading comes from: `PUMP5_SPEED` at Millbrook, Pump 05 (474.05963 rpm, x8 on the rail
lattice in THRESHOLDS.md). It is the only speed reading on the estate's only running bay; every
other bay's `SPEED` reads sub-rpm noise.

What this changes:

- **Not corroborated.** The review notes that say "Catalogue unit rpm, corroborated under load at
  [Millbrook] (474 rpm)" are wrong on that point: 4 in `review/maple-grove.review.json` and 14 in
  `review/cedar-creek.review.json`. `review/pine-hollow.review.json` has none; an earlier handover
  line said it did. The packs are already applied, so they stay unedited for audit traceability.
  This section and HANDOVER.md carry the correction.
- **Not a reference.** 474 rpm must not be used to calibrate or interpret any other reading. The
  `2*pi*f = 49.6` vibration factor in BLOCKERS.md and `devtools/record_vibration_sign.py` assumes
  it and is conditional on it.
- **Not coerced either.** `PUMP5_SPEED` is deliberately left out of the rail coercion (x8 is
  excluded), so it keeps quality `good`. That means "not proven to be a rail", not "validated".
- **Synchronous speed is not shaft speed.** `N_s = 120f / P` gives 500 rpm for 12 poles at 50 Hz,
  so 474 rpm is physically possible. The pole count is unconfirmed, and possible is not
  measured.

Related, same source: the "Catalogue unit V, corroborated under load" notes (4 Maple Grove, 13
Cedar Creek) rest on the same Millbrook bay, whose `PUMP5_EXCITATION_FLD_VLTG` reads +592.57 V,
the x10 positive rail, already coerced to `bad`. Treat those as uncorroborated as well.

**Validation steps (follow the signal path):**

1. **Raw source.** Is +32767 the converter's actual output, or introduced later by the PLC?
2. **Converter configuration.** Configured input and output ranges, and whether the maximum count
   means over-range, saturation or a valid end point. For a plain signed 16-bit integer +32767 is
   the positive maximum, but a converter may use another encoding or reserve that value as a
   fault code, so its manual and configuration are needed.
3. **PLC scaling.** The expression that turns raw counts into rpm.
4. **Drive feedback.** Compare actual-speed feedback with the SCADA reading.
5. **Independent check.** A tachometer or another validated speed measurement.
6. **Trend.** Across several operating speeds, does the raw value move, or stay pinned at the
   maximum?

| Check | Evidence needed | What it establishes |
|---|---|---|
| Raw count | PLC trace or raw historian export | Whether +32767 is the source value |
| Converter | Model, configuration, output range | Whether 32767 means full scale, clipping or a special value |
| PLC scaling | Scaling expression or configuration | Whether the conversion to 474 rpm is correct |
| Motor speed | Nameplate, poles/frequency or drive data | Expected speed range |
| Independent reference | Validated drive feedback or tachometer | Whether the displayed rpm agrees with a separate measurement |

**Resolution rule:** mark RPM validated only after the scaling is verified and the displayed
reading agrees with an independent reference within an agreed tolerance. If the raw count is
pinned at its maximum, investigate the input path before accepting any displayed value.

---

## 9. Forebay level

There is no universal safe level.

| Parameter | Basis |
|---|---|
| Unit and datum | Survey reference, transmitter configuration. Do **not** assume metres above MSL; it may be a local gauge zero. |
| Minimum drawdown | Approved hydraulic design and intake limits |
| Minimum level for safe pumping | NPSH margin (HI 9.6.1), minimum submergence and vortex risk (HI 9.8), OEM |
| High stop level | Control philosophy, operating envelope, interlocks |
| Tag availability | Transmitter → PLC I/O → OPC UA → application |

---

## 10. Data freshness

The newest Cosmos record is 2025-08-01. On 2026-10-14 that is **439 days** old. The app shows the
recorded window as current (`services/display_time.py`, HANDOVER item 8). That is a deliberate
demo arrangement, **not live data**.

To find the failure point, trace one known pump tag through each layer:

1. PLC / OPC UA source timestamp.
2. Ingestion event timestamp.
3. Stored record.
4. Cosmos query result.
5. Application value.
6. Status code and acquisition logs.

A weeks-long operating dataset shows operating ranges. On its own it cannot fix unknown units.
Pair it with the nameplate, CT/VT ratios, PLC scaling and a trusted meter reading.

The dataset should include, for each station with at least one running pump:

- State, speed and start/stop events.
- I, V, kW, kvar and PF.
- Winding, bearing, core, air and water temperatures.
- Vibration, with its unit and quality.
- Level and pressure.
- Source timestamps, ingest timestamps and OPC UA status.

Keep raw values and quality. Never interpolate.

---

## 11. Priorities

1. **Data quality.** Already in place: the over-range marker and converter rails are marked bad,
   quality is checked before classifying, values are not shown as current once stale, and the time
   a value last changed is recorded. Still open: a run-state input to classification, and a
   whole-block sentinel for Millbrook P05.
2. **Electrical units and vibration clipping.** Get the CT/VT ratios and PLC scaling, and run
   the six-step signal trace from section 7.
3. **Motor and bearing protection documents.** Get the per-station OEM schedules and the heat-run
   report. Only then set the limits to `"status": "approved"`, with `approved_basis`.
4. **Station mapping and mimic sign-off.** Use the point register and the section 4 package, then
   approve the layout.
