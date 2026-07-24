"""Mark the seeded 'done' column as terminal (S6c)."""

from django.db import migrations


def mark_done_terminal(apps, schema_editor):
    """Set is_terminal on the 'done' column if it is not already set elsewhere."""
    KanbanColumn = apps.get_model('tasks', 'KanbanColumn')

    if KanbanColumn.objects.filter(is_terminal=True).exists():
        return

    KanbanColumn.objects.filter(key='done').update(is_terminal=True)


def unmark_terminal(apps, schema_editor):
    """Clear the terminal flag from the 'done' column."""
    KanbanColumn = apps.get_model('tasks', 'KanbanColumn')
    KanbanColumn.objects.filter(key='done').update(is_terminal=False)


class Migration(migrations.Migration):
    """Mark the done column terminal."""

    """Data migration marking the default terminal column."""

    dependencies = [('tasks', '0015_kanbancolumn_is_terminal')]

    operations = [migrations.RunPython(mark_done_terminal, unmark_terminal)]
