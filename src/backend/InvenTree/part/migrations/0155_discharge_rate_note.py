"""Bring the stored Discharge Rate definition in step with the catalogue file.

The catalogue note for ``PUMP | DISCHARGE | Discharge Rate`` said the unit was
unconfirmed and that hydraulics excluded cusec. Both were true when written and
neither is now: the unit was settled as cusecs on 2026-09-25 against the plant
operator's published figures, and the exclusion rested on a 34 m lift head that
the published pond levels put nearer 15 m.

The note had to change in two places at once. ``load_pump_catalogue`` treats a
loaded definition as immutable - it compares the checked-in definition against
the marker it stored on the template and refuses a mismatch, so that nobody can
quietly redefine a parameter that approved points already hang off. Correcting
the file alone would therefore have left the command working on a fresh database
and failing on every existing one. This moves the stored marker with it.

Only the note moves. ``units`` and ``unit_status`` are untouched, because the
definition genuinely prescribes no unit - review records it per point, as it
does for the pad temperature families - and ``point_hash`` does not cover
template metadata, so no binding is invalidated by this.
"""

from django.db import migrations

NAMESPACE = 'pump_system_catalogue'
TEMPLATE = 'PUMP | DISCHARGE | Discharge Rate'

BEFORE = (
    'Per-pump discharge rate: reads the stated design discharge while running, 0 '
    'while stopped, and the station /dv is their exact sum. Unit unconfirmed; '
    'hydraulics exclude m3/s, m3/h, L/s, cusec.'
)

AFTER = (
    'Per-pump discharge rate: the stated design discharge while running, 0 while '
    "stopped; the station /dv is their exact sum. Cusecs, settled 2026-09-25: "
    "Annaram's published 11,724 for four pumps is this tag's 2,931 each."
)


def _restate(apps, old, new):
    """Rewrite the stored note, leaving a definition we do not recognise alone."""
    ParameterTemplate = apps.get_model('common', 'ParameterTemplate')
    for template in ParameterTemplate.objects.filter(name=TEMPLATE):
        metadata = dict(template.metadata or {})
        owned = metadata.get(NAMESPACE)
        if not isinstance(owned, dict):
            continue
        definition = dict(owned.get('definition') or {})
        if definition.get('note') != old:
            continue
        definition['note'] = new
        metadata[NAMESPACE] = {**owned, 'definition': definition}
        template.metadata = metadata
        template.save(update_fields=['metadata'])


def restate_note(apps, schema_editor):
    """Adopt the corrected note."""
    _restate(apps, BEFORE, AFTER)


def revert_note(apps, schema_editor):
    """Put the superseded note back, for a rollback to the older catalogue file."""
    _restate(apps, AFTER, BEFORE)


class Migration(migrations.Migration):
    """Data-only; keeps the checked-in catalogue loadable onto an existing database."""

    dependencies = [
        ('part', '0154_partverificationsession_scope_client'),
        ('common', '0049_merge_upstream_sync'),
    ]

    operations = [migrations.RunPython(restate_note, revert_note)]
