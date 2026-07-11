"""Seed default safety gate templates."""

from django.db import migrations


def seed_templates(apps, schema_editor):
    SafetyGateTemplate = apps.get_model('repair', 'SafetyGateTemplate')
    defaults = [
        {
            'name': 'Electrical Lockout/Tagout',
            'gate_type': 'loto',
            'instructions': 'Isolate electrical energy, apply lock/tag, and verify zero energy before work.',
            'applies_to': {
                'fault_keywords': ['motor', 'contactor', 'breaker', 'coil', 'voltage', 'vfd', 'wiring'],
            },
            'requires_photo': True,
            'requires_second_person': True,
            'risk_tier': 3,
            'default_sequence': 10,
        },
        {
            'name': 'Rotating Equipment Isolation',
            'gate_type': 'isolation',
            'instructions': 'Confirm rotating equipment is isolated and all stored mechanical energy is controlled.',
            'applies_to': {
                'fault_keywords': ['bearing', 'pump', 'fan', 'shaft', 'gearbox', 'coupling', 'vibration'],
            },
            'requires_photo': False,
            'requires_second_person': False,
            'risk_tier': 2,
            'default_sequence': 20,
        },
        {
            'name': 'Hot Work Permit Required',
            'gate_type': 'hot_work',
            'instructions': 'Confirm hot-work permit, combustible-area check, extinguisher availability, and fire watch.',
            'applies_to': {'fault_keywords': ['weld', 'welding', 'grind', 'grinding', 'cutting', 'hot work']},
            'requires_photo': True,
            'requires_second_person': True,
            'risk_tier': 3,
            'default_sequence': 30,
        },
        {
            'name': 'PPE Check',
            'gate_type': 'ppe',
            'instructions': 'Confirm required PPE is available, appropriate, and worn before work begins.',
            'applies_to': {'criticality_in': ['high', 'critical']},
            'requires_photo': False,
            'requires_second_person': False,
            'risk_tier': 1,
            'default_sequence': 40,
        },
    ]
    for item in defaults:
        SafetyGateTemplate.objects.get_or_create(name=item['name'], defaults=item)


def unseed_templates(apps, schema_editor):
    SafetyGateTemplate = apps.get_model('repair', 'SafetyGateTemplate')
    SafetyGateTemplate.objects.filter(
        name__in=[
            'Electrical Lockout/Tagout',
            'Rotating Equipment Isolation',
            'Hot Work Permit Required',
            'PPE Check',
        ]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('repair', '0003_lockoutpoint_safetygatetemplate_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_templates, reverse_code=unseed_templates),
    ]
