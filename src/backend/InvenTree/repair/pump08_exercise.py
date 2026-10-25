"""Prepared Pump08 exercise criteria, not OEM limits or physical observations."""

PROCEDURE_CODE = 'P08-MOTOR-COOL-RTS-EX'
PROCEDURE_NAME = 'Pump 08 | Motor Cooling Return Verification'
STEP_DEFINITIONS = [
    {
        'sequence': 1,
        'title': 'Cooler restored',
        'value_type': 'boolean',
        'unit': '',
        'note': 'Exercise evidence shows cooler air-side fouling addressed; surfaces inspected with no remaining damage requiring a spare. Motor covers and safeguards are represented as restored. Physical execution not asserted.',
        'evidence_codes': ['P08-TEMP-E03', 'P08-TEMP-E05'],
    },
    {
        'sequence': 2,
        'title': 'Winding temperatures below warning',
        'value_type': 'number',
        'unit': '°C',
        'note': 'Controlled-exercise post-service readings: Winding Temperature 5 = 94.0 C; Winding Temperature 6 = 96.0 C; maximum = 96.0 C. Configured application warning = 125.0 C; critical = 145.0 C. These are prepared readings, not live SCADA/historian data or approved motor release limits.',
        'evidence_codes': ['P08-TEMP-E06'],
    },
    {
        'sequence': 3,
        'title': 'Trial stable; thermal check clear',
        'value_type': 'boolean',
        'unit': '',
        'note': 'Prepared 15-minute controlled-exercise trial: motor operating condition represented as stable; no new unresolved winding-temperature event is represented in the trial evidence. Confirm real event/alert resolution separately through supported controls. This step does not grant physical operating permission.',
        'evidence_codes': ['P08-TEMP-E06'],
    },
]
EVIDENCE = {
    'P08-TEMP-E03': 'Prepared exercise observation: deposits represented on assumed motor heat-exchanger air-side surfaces. This is not an actual field inspection. Fitted cooling configuration and the physical root cause remain unverified.',
    'P08-TEMP-E05': 'Prepared exercise service: applicable air-side heat-transfer surfaces represented as cleaned and inspected, no damage or obstruction remaining, covers/guards represented as restored. No physical maintenance, replacement parts, stock consumption or electrical test is asserted.',
    'P08-TEMP-E06': 'Prepared controlled-exercise verification: Winding Temperature 5 = 94.0 C; Winding Temperature 6 = 96.0 C; maximum = 96.0 C. Warning 125 / critical 145 C are configured application references, not verified OEM limits. A 15-minute stable trial is represented, not physically performed or a WEG-mandated duration. Actual source-alarm review and reset remain separate. No historian value is altered and no physical release is issued.',
}


def evidence_bytes(code):
    """Return the exact versioned evidence sheet, explicitly labelled prepared."""
    return f'# {code}\n\nPREPARED EXERCISE EVIDENCE — NOT PHYSICAL WORK\n\n{EVIDENCE[code]}\n\nSource: owner-supplied October25 Pump08 final work-order specification.\n'.encode()
