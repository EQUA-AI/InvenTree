"""Register the cusec, so a fresh database can rebuild the pumphouse estate.

Thirty approved discharge points carry ``cusec`` as their unit. Pint does not
define it - it is the customary unit of the irrigation schemes this estate was
migrated from, and the unit the plant operator's own published figures are
quoted in - so it was added to this deployment by hand as a ``CustomUnit`` row.

That left the estate unreproducible. Nothing in the repository created the unit,
so a fresh database had no ``cusec``, and ``apply_dictionary_review`` refuses a
pack whose unit will not convert to its catalogue target: rebuilding the review
from the checked-in packs failed at the thirty discharge points, and the only
way past it was to know to create the row first. The failure was at least loud -
the reviewer validates units - but "loud" is not the same as "documented".

Creating it here ties the unit to the migration history that the approvals
depend on. ``get_or_create`` because this deployment already has the row.
"""

from django.db import migrations

UNIT = {'name': 'cusec', 'definition': 'foot**3/second', 'symbol': 'cusec'}


def add_cusec(apps, schema_editor):
    """Define the cusec if this database has not got one already."""
    CustomUnit = apps.get_model('common', 'CustomUnit')
    CustomUnit.objects.get_or_create(name=UNIT['name'], defaults=UNIT)


def drop_cusec(apps, schema_editor):
    """Remove it again, but only while nothing is relying on it.

    Approved points store the unit as text, so deleting the row would not fail -
    it would leave those points with a unit the registry cannot resolve, which
    is the state this migration exists to prevent.
    """
    CustomUnit = apps.get_model('common', 'CustomUnit')
    DictionaryPoint = apps.get_model('assets', 'DictionaryPoint')
    if DictionaryPoint.objects.filter(unit=UNIT['name']).exists():
        return
    CustomUnit.objects.filter(**UNIT).delete()


class Migration(migrations.Migration):
    """Data-only; the unit lives in common, the reason for it lives here."""

    dependencies = [
        ('assets', '0014_station_activation'),
        ('common', '0049_merge_upstream_sync'),
    ]

    operations = [migrations.RunPython(add_cusec, drop_cusec)]
