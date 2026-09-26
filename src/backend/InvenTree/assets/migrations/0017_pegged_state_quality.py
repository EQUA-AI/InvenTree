"""Mark cached over-range readings as bad quality.

``machine_health.connectors.pumphouse_payload`` now recognises the source's
16-bit over-range marker - a reading of exactly 3276.7, in either float32
encoding - and ingests it with ``SignalQuality.BAD`` instead of ``GOOD``. That
fixes every reading ingested from now on and nothing already cached.

``MachineSignalState`` is a current-value cache, and for a station whose
history is a recorded window it is a cache that will not be rewritten: every
snapshot up to the recorded end has already been ingested, so a pegged channel
keeps yesterday's ``good`` verdict indefinitely and the Health blade shows
"3276.7 degC · Good" beside it. This applies the same rule to the cache once.

Not reversible: the old value was wrong, not different.
"""

from django.db import migrations

#: Mirrors ``pumphouse_payload.OVER_RANGE`` / ``OVER_RANGE_TOLERANCE``. Copied
#: rather than imported so the migration stays what it was when it ran.
OVER_RANGE = 3276.7
OVER_RANGE_TOLERANCE = 0.001


def mark_pegged_states_bad(apps, schema_editor):
    """Set ``quality='bad'`` on every cached state holding the sentinel."""
    MachineSignalState = apps.get_model('assets', 'MachineSignalState')

    updated = 0
    for state in MachineSignalState.objects.exclude(quality='bad').iterator():
        value = (state.value or {}).get('value') if isinstance(state.value, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if abs(float(value) - OVER_RANGE) > OVER_RANGE_TOLERANCE:
            continue
        state.quality = 'bad'
        state.save(update_fields=['quality'])
        updated += 1

    if updated:
        print(f'\n  Marked {updated} cached over-range readings as bad quality.')


class Migration(migrations.Migration):
    """Apply the over-range rule to readings cached before it existed."""

    dependencies = [('assets', '0016_opc_tag_bay_ownership')]

    operations = [
        migrations.RunPython(mark_pegged_states_bad, migrations.RunPython.noop)
    ]
