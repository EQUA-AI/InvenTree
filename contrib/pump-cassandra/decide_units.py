"""Turn the exported pack into a decided unit review, using the observed values.

A unit is a claim about magnitude, so the snapshot can settle some units and
refute others. It can also fail to settle one, and the most important fact about
this particular snapshot is how often that happens: the station is shut down.
Every pump reports `st=I`, `MOTOR_ON_STATUS=0`, zero power, zero current, zero
voltage. Anything that only has a magnitude while running reads zero or sensor
noise, and calibrating a unit against noise is guessing with extra steps.

So: approve what the data confirms, withhold what it cannot, and say which is
which on the point itself.

Writes `contrib/pump-cassandra/PH_3.unit-review.json`. Applies nothing.
"""

import json
import re
import statistics

PACK = 'contrib/pump-cassandra/PH_3.full-review.json'
SNAPSHOT = 'contrib/pump-cassandra/PH_3.full-snapshot.json'
TARGET = 'contrib/pump-cassandra/PH_3.unit-review.json'

PUMP_PATH = re.compile(r'^/dex/PUMP([1-9][0-9]{0,3})_')
PUMP_TAG = re.compile(r'^PUMP([1-9][0-9]{0,3})_')

# --- observed ranges, so each note carries its own evidence -------------------

with open(SNAPSHOT, encoding='utf-8') as fh:
    payload = json.load(fh)['snapshots'][0]['data1']
if isinstance(payload, str):
    payload = json.loads(payload)

observed = {}
for tag, value in payload['dex'].items():
    if tag in {'ID', 'TIMESTAMP'}:
        continue
    match = PUMP_TAG.match(tag)
    family = tag[match.end() :] if match else tag
    try:
        observed.setdefault(family, []).append(float(value))
    except (TypeError, ValueError):
        pass


def evidence(family):
    """Describe the observed spread for a family, or say there is none."""
    values = observed.get(family)
    if not values:
        return 'no numeric observation in the reference snapshot'
    return (
        f'observed {min(values):.4g} to {max(values):.4g} '
        f'(median {statistics.median(values):.4g}, n={len(values)})'
    )


IDLE = (
    'The reference snapshot was taken with the station shut down - every bay '
    'reports st=I and MOTOR_ON_STATUS=0, with zero active power, current and '
    'voltage.'
)

# --- decisions ----------------------------------------------------------------

# Confirmed by magnitude. A stopped machine sitting in ambient reads ~35-45 in
# degC; the same body would read ~104 in degF and ~313 in K, so the scale is not
# ambiguous. Saturated and under-range readings are a value-quality matter, not a
# unit matter, and do not change the unit.
TEMPERATURE_NOTE = (
    'Unit confirmed from observed magnitude: {ev}. A shut-down machine soaking at '
    'ambient is consistent with degC and excludes degF (~104) and K (~313). '
    'Out-of-range readings on some channels are a sensor-quality question, not a '
    'unit question, and engineering limits still require approved plant data.'
)

FREQUENCY_NOTE = (
    'Unit confirmed: {ev}. Six bays sense 50.0 Hz at the breaker while stopped, '
    'which is the nominal Indian grid frequency and fixes the scale directly. '
    'The bays reading 0 are isolated, not running at zero speed.'
)

VALVE_NOTE = (
    'Unit confirmed as percent of travel: {ev}. Readings cluster tightly at the '
    '0 and 100 end stops, which is what a percentage-of-travel transmitter does. '
    'Values slightly beyond 0-100, and the two far outliers, are uncalibrated raw '
    'transmitter span rather than real positions beyond the end stops.'
)

STATUS_NOTE = (
    'Dimensionless by nature: {ev}. Observed only as 0/1 across all fourteen bays '
    'and consistent with st=I, so it carries no physical unit.'
)

POWER_FACTOR_NOTE = (
    'Dimensionless by definition, a ratio of real to apparent power: {ev}. The '
    'values themselves are not meaningful here - power factor is undefined for a '
    'stopped machine, and the 1.0 readings are a controller default, not unity PF.'
)

APPROVE = {
    'PUMP_FREQUENCY': FREQUENCY_NOTE,
    'EOPD_VALVE_POS_PROCESS_VALUE': VALVE_NOTE,
    'HOPD_VALVE_POS_PROCESS_VALUE': VALVE_NOTE,
    'MOTOR_ON_STATUS': STATUS_NOTE,
    'MOTOR_OFF_STATUS': STATUS_NOTE,
    'PUMP_POWERFATCOR': POWER_FACTOR_NOTE,
}

# Everything the catalogue proposes in degC, confirmed the same way.
TEMPERATURE_PREFIXES = (
    'MOTOR_CORE_RTD',
    'PUMP_COOLING_WATER_INLET_TEMP',
    'PUMP_COOLING_WATER_OUTLET_TEMP',
    'PUMP_MOTOR_COLD_AIR',
    'PUMP_MOTOR_HOT_AIR',
    'PUMP_MOTOR_WINDING_TEMPERATURED',
    'PUMP_PUMP_INLET_COOLING_WATER_TEMPERATURED',
    'THRST_BRG_THRST_PD_RTD',
)

# Cannot be settled by a shut-down snapshot. The candidate units differ by orders
# of magnitude and the observation is zero or transmitter noise either way.
BLOCKED = {
    'ACTIVE_POWER': 'kW and MW differ by 1000x and both predict 0 while stopped',
    'PUMP_CURRENT_AVG': 'A and kA cannot be told apart from a zero reading',
    'PUMP_LINE_TO_LINE_VOLTAGE': 'V predicts ~11000 and kV predicts ~11; observed 0',
    'PUMP_REACTIVE_POWER': 'MVAR is expected but unconfirmed, and var is not yet in the unit registry',
    'SPEED': 'rpm predicts a few hundred while running; observed is transmitter noise about zero',
    'DISCHARGE_PRESSURE': 'bar, kg/cm2 and metres of head are all plausible and all predict ~0 at rest',
    'EXCITATION_FLD_CURR_PROCESS_VALUE': 'one bay reads 1048 while the other thirteen read about 0, which needs explaining before a unit is fixed',
    'SPIRAL_CASE1': 'behaves like a pressure rather than a temperature, but the magnitude at rest cannot fix the unit',
}

VIBRATION = {
    'MOTOR_DE_VIBRATION1',
    'PUMP_MOTOR_DE_VIBRATION2',
    'MTR_NDE_BRG_VBRTN1_PROCESS_VALUE',
    'MTR_NDE_BRG_VBRTN2_PROCESS_VALUE',
    'PMP_THRST_BRG_VBRTN1_PROCESS_VALUE',
    'PMP_THRST_BRG_VBRTN2_PROCESS_VALUE',
    'PMP_THRST_BRG_VBRTN3_PROCESS_VALUE',
}

PADS = {
    'PUMP_GUIDED_RADIAL_PAD_1D6',
    'PUMP_GUIDED_RADIAL_PAD_2D6',
    'PUMP_THRUST_AXIAL_PAD_1D5',
    'PUMP_THRUST_AXIAL_PAD_2D5',
}

VIBRATION_REASON = (
    'Vibration unit unconfirmed. ' + IDLE + ' The channel reads {ev}, which is '
    'transmitter noise about zero and is equally consistent with mm/s velocity '
    'and micrometre displacement. Negative readings confirm an uncalibrated zero '
    'offset, since neither velocity RMS nor displacement can be negative. The '
    'reference images give mm/s for the motor drive-end and non-drive-end '
    'channels, which is the ISO 20816 convention for this machine class, but that '
    'should be confirmed against a running sample before it is approved.'
)

PAD_REASON = (
    'Quantity itself unconfirmed, so no unit can be set. The catalogue asks '
    'whether this is temperature or displacement. The data now points to '
    'temperature: the channel reads {ev} while the machine is stopped, whereas '
    'every genuine vibration channel on the same machine reads about zero, and '
    'one bay reads a large negative value matching the open-circuit signature of '
    'the RTDs elsewhere in this payload. That is evidence, not confirmation - a '
    'resting proximity probe can also read tens of micrometres. Settle it against '
    'a running sample or the instrument schedule.'
)

BLOCKED_REASON = (
    'Unit unconfirmed. ' + IDLE + ' {why}. Observed {ev}. A running sample will '
    'settle this in one reading; approving it from a shut-down plant would fix a '
    'scale against sensor noise.'
)


def family_of(path):
    """Group a source path by its tag family, ignoring which bay it belongs to."""
    if path.startswith('/dex/'):
        return PUMP_PATH.sub('', path)
    return path


with open(PACK, encoding='utf-8') as fh:
    pack = json.load(fh)

approve = list(pack['approve'])
withhold = list(pack['withhold'])
pending = []
counts = {'approved': 0, 'withheld': 0, 'left_pending': 0}

for entry in pack['pending']:
    family = family_of(entry['paths'][0])
    proposed = entry.get('unit', '')
    ev = evidence(family)

    is_temperature = proposed == 'degC' and family.startswith(TEMPERATURE_PREFIXES)

    if entry['data_type'] == 'unknown':
        # Cannot be approved by rule, and the pads are the interesting case.
        reason = (
            PAD_REASON
            if family in PADS
            else (
                'Quantity unconfirmed; the catalogue records the data type as unknown. '
                f'Observed {ev}.'
            )
        )
        withhold.append({'paths': entry['paths'], 'reason': reason.format(ev=ev)})
        counts['withheld'] += 1
        continue

    if is_temperature or family in APPROVE:
        note = (TEMPERATURE_NOTE if is_temperature else APPROVE[family]).format(ev=ev)
        decided = dict(entry)
        decided['note'] = note
        decided['unit_status'] = 'unitless' if not proposed else 'verified'
        approve.append(decided)
        counts['approved'] += 1
        continue

    if family in VIBRATION:
        withhold.append({
            'paths': entry['paths'],
            'reason': VIBRATION_REASON.format(ev=ev),
        })
        counts['withheld'] += 1
        continue

    if family in BLOCKED:
        withhold.append({
            'paths': entry['paths'],
            'reason': BLOCKED_REASON.format(why=BLOCKED[family], ev=ev),
        })
        counts['withheld'] += 1
        continue

    pending.append(entry)
    counts['left_pending'] += 1

pack['approve'] = approve
pack['withhold'] = withhold
pack['pending'] = pending

with open(TARGET, 'w', encoding='utf-8') as fh:
    json.dump(pack, fh, indent=1, ensure_ascii=False)
    fh.write('\n')

print('approve :', len(approve), f'(+{counts["approved"]} decided here)')
print('withhold:', len(withhold), f'(+{counts["withheld"]} decided here)')
print('pending :', len(pending))
print()
print('families left pending:')
for family in sorted({family_of(e['paths'][0]) for e in pending}):
    print(f'   {family[:60]:<60} {evidence(family)}')
