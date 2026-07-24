"""Seed the four original board columns under their existing keys.

Before columns were persisted, the board hardcoded exactly these four in the
frontend. Every existing ``KanbanCard.status`` holds one of these keys, so the
seed must reproduce the keys verbatim or stored cards stop resolving to a column.

Labels and colors mirror the previous frontend defaults so the board looks
unchanged after the switch. ``is_default`` marks these as system columns, which
the API protects from deletion.

Idempotent via ``get_or_create`` on ``key``, and reversible: the reverse removes
only the four seeded keys, and only when no card still references them, so a
down-migration never orphans a card.
"""

from django.db import migrations

# (key, label, color, order) -- keys are frozen; they are stored in card.status.
SEED_COLUMNS = [
    ('backlog', 'Backlog', 'gray', 0),
    ('in-progress', 'In Progress', 'indigo', 1),
    ('review', 'In Review', 'yellow', 2),
    ('done', 'Done', 'green', 3),
]


def seed_columns(apps, schema_editor):
    """Create the four default columns if they do not already exist."""
    KanbanColumn = apps.get_model('tasks', 'KanbanColumn')

    for key, label, color, order in SEED_COLUMNS:
        KanbanColumn.objects.get_or_create(
            key=key,
            defaults={
                'label': label,
                'color': color,
                'order': order,
                'is_default': True,
            },
        )


def unseed_columns(apps, schema_editor):
    """Remove seeded columns, but never one a card still points at."""
    KanbanColumn = apps.get_model('tasks', 'KanbanColumn')
    KanbanCard = apps.get_model('tasks', 'KanbanCard')

    referenced = set(KanbanCard.objects.values_list('status', flat=True).distinct())

    removable = [key for key, *_ in SEED_COLUMNS if key not in referenced]
    KanbanColumn.objects.filter(key__in=removable, is_default=True).delete()


class Migration(migrations.Migration):
    """Seed the default board columns."""

    """Data-only migration seeding the default board columns."""

    dependencies = [('tasks', '0009_kanbancolumn')]

    operations = [migrations.RunPython(seed_columns, unseed_columns)]
