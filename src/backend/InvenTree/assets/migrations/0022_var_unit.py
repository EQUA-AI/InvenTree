"""Register the var, so reactive power can be recorded in its own unit.

Every station publishes reactive power (``/pmvar`` and ``/pd/<P>/pmvar``, and
``PUMPn_PUMP_REACTIVE_POWER`` in ``dex``), and the reference drawings quote it in
MVAR. Pint does not define the var, so the only units that validated were the
wrong ones: ``MVA`` (apparent power) and ``MW`` (real power). Recording reactive
power under either would be a false statement about the measurement, so those
points have been withheld (see ``test_reactive_power_is_not_approved_as_apparent_power``).

The var is a volt-ampere by dimension, which is why Pint can convert between
``Mvar``, ``MVA`` and ``MW`` at all. The unit check cannot tell them apart; the
review can, and now has a correct unit to record.

Two spellings are registered. ``var`` is the SI/IEC symbol (so ``kvar``,
``Mvar``). ``VAr`` is the form most switchgear and SCADA screens show (so
``kVAr``, ``MVAr``). Other case variants (``MVar``, ``MVAR``) stay undefined:
Pint is case-sensitive and ``mvar`` would already read as milli-var.

Both rows are defined directly as ``volt * ampere`` rather than one in terms of
the other: ``reload_unit_registry`` loads custom units in no particular order,
and an alias read before its base would fail to define and be skipped.

This adds only the unit. It does not approve any point: the reactive-power
points are also withheld because they read 0 under load at two stations and
because the catalogue has no parameter for them. Those are plant questions.
"""

from django.db import migrations

UNITS = (
    {
        'name': 'volt_ampere_reactive',
        'definition': 'volt * ampere',
        'symbol': 'var',
    },
    {'name': 'VAr', 'definition': 'volt * ampere', 'symbol': ''},
)

#: A point whose stored unit ends in one of these relies on the rows above.
_SUFFIXES = ('var', 'VAr')


def add_var(apps, schema_editor):
    """Define the var and its VAr spelling where they are not defined yet."""
    CustomUnit = apps.get_model('common', 'CustomUnit')
    for unit in UNITS:
        CustomUnit.objects.get_or_create(name=unit['name'], defaults=unit)


def drop_var(apps, schema_editor):
    """Remove them again, but only while no reviewed point is stored in them.

    Points store their unit as text, so the delete would succeed and leave those
    points in a unit the registry cannot resolve - the state 0015 documents.
    """
    CustomUnit = apps.get_model('common', 'CustomUnit')
    DictionaryPoint = apps.get_model('assets', 'DictionaryPoint')
    for suffix in _SUFFIXES:
        if DictionaryPoint.objects.filter(unit__endswith=suffix).exists():
            return
    for unit in UNITS:
        CustomUnit.objects.filter(**unit).delete()


class Migration(migrations.Migration):
    """Data-only; the unit lives in common, the reason for it lives here."""

    dependencies = [
        ('assets', '0021_merge_iot_alarms_customizations'),
        ('common', '0049_merge_upstream_sync'),
    ]

    operations = [migrations.RunPython(add_var, drop_var)]




