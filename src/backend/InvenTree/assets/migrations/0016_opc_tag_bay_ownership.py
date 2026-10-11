"""Give bay-scoped OPC-UA tags to the bay whose name they carry.

The source spells a bay-scoped measurement two ways. Most arrive as
``PUMP5_...``, which the importer recognised; a second set arrives as the raw
node id of an OPC-UA subscription, ``...OS(2)::P5_...``, which it did not. Those
fell to the station, so 120 bay-scoped tags sat in the station's own readings -
an operator opening Pump 5 saw none of them, and the station's list was filled
with other bays' tags instead.

``registry.OPC_PUMP`` fixes that for anything imported from now on. This moves
the rows already stored, which ``import_dictionary`` would not: it skips a path
it has seen, so nothing would ever have revisited them.

Ownership is all that moves. Whether either spelling resolves to a catalogue
parameter is a separate question, and an open one - the two names disagree where
both exist, so nothing here claims they measure the same thing. A point with a
live binding is left alone on principle rather than necessity: none has one
today, because none is approved, and re-owning a bound point would change its
``point_hash`` underneath the binding.
"""

import re

from django.db import migrations

#: Mirrors ``assets.registry.OPC_PUMP``. Duplicated deliberately: a migration
#: must keep describing the database it was written against, even after the
#: importer's own pattern moves on.
OPC_PUMP = re.compile(r'::P([1-9][0-9]{0,3})_')


def _rehome(apps, *, to_bay):
    """Move bay-named station points onto their bay, or back onto the station."""
    AssetMachine = apps.get_model('assets', 'AssetMachine')
    DictionaryPoint = apps.get_model('assets', 'DictionaryPoint')
    MachineSignalBinding = apps.get_model('assets', 'MachineSignalBinding')

    for station in AssetMachine.objects.filter(asset_type='pumphouse'):
        bays = {
            bay.source_key: bay
            for bay in AssetMachine.objects.filter(parent=station)
            if bay.source_key
        }
        if not bays:
            continue
        scope = (
            DictionaryPoint.objects.filter(station=station, machine=station)
            if to_bay
            else DictionaryPoint.objects.filter(
                station=station, machine__parent=station
            )
        )
        for point in scope:
            match = OPC_PUMP.search(point.path)
            if match is None:
                continue
            bay = bays.get(f'P{match[1]}')
            if bay is None:
                continue
            target = bay if to_bay else station
            if point.machine_id == target.pk:
                continue
            if MachineSignalBinding.objects.filter(dictionary_point=point).exists():
                continue
            point.machine = target
            point.save(update_fields=['machine'])


def to_bays(apps, schema_editor):
    """Adopt the bay named in the node id."""
    _rehome(apps, to_bay=True)


def to_station(apps, schema_editor):
    """Hand them back to the station, as an older importer would have filed them."""
    _rehome(apps, to_bay=False)


class Migration(migrations.Migration):
    """Data-only; the importer decides this for every later import."""

    dependencies = [('assets', '0015_cusec_unit')]

    operations = [migrations.RunPython(to_bays, to_station)]
