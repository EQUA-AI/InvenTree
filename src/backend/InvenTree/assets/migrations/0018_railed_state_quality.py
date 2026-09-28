"""Mark cached converter-rail readings as bad quality.

``machine_health.connectors.pumphouse_payload`` now recognises the signed-16-bit
rails at the range scalings this estate uses - the same converter that produces
the ``3276.7`` over-range marker handled by ``0017``, saturating at its other
settings. That fixes every reading ingested from now on and nothing already
cached.

For a station whose history is a recorded window, "nothing already cached" means
everything. All three checkpoints sit at the end of their recorded window, which
is also what ``read_ceiling`` returns, so every poll reads zero documents;
checkpoints are forward-only and ``_is_replay`` drops a re-read. Without this the
new rule would change nothing at all on the Health blade, which would keep
showing a valve position of -118.5% and a field voltage of -592.6 V as good
measurements.

Twenty rows are affected. None of their bindings carries a transform, so the
cached value is the same number ``_coerce`` would see, and the predicate here
tests what it tests there.

Not reversible: the old verdict was wrong, not different.
"""

from django.db import migrations

#: Mirrors ``pumphouse_payload.RAIL_*``. Copied rather than imported so the
#: migration stays what it was when it ran - the same reason 0017 copies its own.
RAIL_NEGATIVE = 59.25925827026367  # -32768 counts
RAIL_POSITIVE = 59.25745391845703  # +32767 counts
RAIL_SCALARS = (1, 2, 10)
RAIL_RELATIVE_TOLERANCE = 1e-5


def _railed(number):
    """Whether a cached reading sits on a converter rail."""
    magnitude = abs(number)
    if not magnitude:
        return False
    for scalar in RAIL_SCALARS:
        for anchor in (RAIL_NEGATIVE, RAIL_POSITIVE):
            rail = anchor * scalar
            if abs(magnitude - rail) <= rail * RAIL_RELATIVE_TOLERANCE:
                return True
    return False


def mark_railed_states_bad(apps, schema_editor):
    """Set ``quality='bad'`` on every cached state sitting on a rail."""
    MachineSignalState = apps.get_model('assets', 'MachineSignalState')

    updated = 0
    for state in MachineSignalState.objects.exclude(quality='bad').iterator():
        value = (state.value or {}).get('value') if isinstance(state.value, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not _railed(float(value)):
            continue
        state.quality = 'bad'
        state.save(update_fields=['quality'])
        updated += 1

    if updated:
        print(f'\n  Marked {updated} cached converter-rail readings as bad quality.')


class Migration(migrations.Migration):
    """Apply the rail rule to readings cached before it existed."""

    dependencies = [('assets', '0017_pegged_state_quality')]

    operations = [
        migrations.RunPython(mark_railed_states_bad, migrations.RunPython.noop)
    ]
