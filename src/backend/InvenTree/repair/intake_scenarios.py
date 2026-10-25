"""Explicit prepared exercise copy, never labelled as model output or field fact."""

PUMP08_SCOPE = (
    'Investigate the high Motor Winding Temperature 6 indication on Pump 08. '
    'Validate the event source and timestamp; review Winding 5 and peer channels, '
    'motor running/load state, cooling conditions, maintenance history and the attached '
    'WEG 11866576 motor manual. Consider cooling impairment, electrical/load conditions, '
    'a localized winding concern and measurement-path issues. If the inspection finding '
    'supports fouling of the applicable motor heat-exchanger air-side surfaces, perform '
    'configuration-appropriate cleaning and inspection under an approved procedure. '
    'Record the finding, service scope, evidence, parts decision and post-service '
    'verification. Do not infer a verified physical fault or release equipment solely '
    'from the alarm or exercise readings.'
)


def prepared_target_scenario(machine, target):
    """Match the prepared specification before creating its synthetic source."""
    if not (
        machine.asset_type == 'pump'
        and machine.source_key == 'P8'
        and machine.client.code == 'internal'
        and machine.parent is not None
        and machine.parent.name == 'Cedar Creek Final Effluent Pump Station'
        and target.get('component_code') == 'PS-MOTOR:1'
        and target.get('name', '').replace(' ', '').casefold()
        == 'motorwindingtemperature6'
    ):
        return None
    return {
        'key': 'pump08_motor_winding_2026_10_25',
        'label': 'Pump 08 motor-winding exercise specification',
        'provenance': 'prepared_scenario_not_ai_output',
        'fields': {
            'title': 'Pump 08 | High Motor Winding Temperature',
            'work_scope': PUMP08_SCOPE,
            'symptom': 'Motor Winding Temperature 6 high indication. Review Winding 5, sensor validity, cooling and operating conditions.',
            'production_impact': 'Potential interruption of Pump 08 availability; actual process impact not established.',
        },
        'suggested_owner_username': 'cc.avery.patel',
        'priority': 'high',
        'criticality': 'critical',
        'work_order_type': 'corrective',
        'parts': [],
        'limitations': 'Prepared exercise specification, not AI output or proof of physical work. Installed cooler and OEM limits remain unverified. No replacement parts or reservation.',
    }


def prepared_scenario(machine, anomaly, target):
    """Offer prepared copy only for the matching labelled synthetic exercise."""
    if (
        anomaly.detector != 'test_alarm_ui'
        or anomaly.metrics.get('synthetic') is not True
    ):
        return None
    return prepared_target_scenario(machine, target)
