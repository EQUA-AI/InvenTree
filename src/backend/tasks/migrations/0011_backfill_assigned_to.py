"""Back-fill ``KanbanCard.assigned_to`` from the free-text ``assignee``.

Makes ``assigned_to`` (FK to User) authoritative for scheduling, grouping and
conflict detection, which are all wrong against free text. ``assignee`` is left
in place for one release as a display/legacy field and dropped later, so a bad
match stays recoverable.

Matching is conservative (see ``tasks.services.assignee_resolution``): only an
unambiguous username or full-name match sets the FK. Everything else is logged
as UNMATCHED/AMBIGUOUS for an operator to resolve by hand, and the card keeps
its free-text ``assignee`` with a null FK.

Only cards whose ``assigned_to`` is currently null are touched, so an already-set
FK is never overwritten. Idempotent: a second run finds those cards already
linked and does nothing. Not reversible in a way that could restore a wrong
guess, so the reverse is a no-op -- ``assignee`` was never cleared, so the source
data is intact regardless.
"""

import logging

from django.db import migrations

logger = logging.getLogger('inventree')


def backfill_assigned_to(apps, schema_editor):
    """Resolve distinct assignee strings and set the FK where unambiguous."""
    from tasks.services.assignee_resolution import resolve_assignees

    KanbanCard = apps.get_model('tasks', 'KanbanCard')
    User = apps.get_model('auth', 'User')

    pending = KanbanCard.objects.filter(assigned_to__isnull=True).exclude(assignee='')

    names = pending.values_list('assignee', flat=True).distinct()
    report = resolve_assignees(names, User.objects.all())

    for name, user_pk in report.matched.items():
        pending.filter(assignee=name).update(assigned_to_id=user_pk)

    for line in report.as_log_lines():
        logger.info(line)


def noop_reverse(apps, schema_editor):
    """No-op: ``assignee`` was never cleared, so nothing needs restoring."""


class Migration(migrations.Migration):
    """Backfill the typed assignee from legacy text."""

    """Data-only back-fill of the assignee FK."""

    dependencies = [('tasks', '0010_seed_kanban_columns')]

    operations = [migrations.RunPython(backfill_assigned_to, noop_reverse)]
