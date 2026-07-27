"""Rename indexes still carrying the model's old table name.

Renaming a table does not rename its indexes: Postgres left every index and
constraint on ``tasks_workorder`` called ``tasks_kanbancard_*``, and the same
for the two satellite tables. That was cosmetic until now.

It stops being cosmetic in the next migration. The board card takes the
``tasks_kanbancard`` name, and Django derives index names from the table and
column - so creating it raises ``relation "tasks_kanbancard_card_kind_..."
already exists``, pointing at an index belonging to a completely different
table. Any deployment that ran the rename hits this, so the stale names are
cleared before the name is reused.

Postgres only. SQLite has no ``ALTER INDEX ... RENAME``, and it names indexes
per-table without the collision, so there is nothing to do there.
"""

from django.db import migrations

#: old table prefix -> the table it became
RENAMED_TABLES = {
    'tasks_kanbancard': 'tasks_workorder',
    'tasks_kanbancardpart': 'tasks_workorderpart',
    'tasks_kanbancarddependency': 'tasks_workorderdependency',
}


def rename_stale_indexes(apps, schema_editor):
    """Re-prefix any index whose name predates the table rename."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    with connection.cursor() as cursor:
        for old_prefix, table in RENAMED_TABLES.items():
            cursor.execute(
                'SELECT indexname FROM pg_indexes '
                'WHERE schemaname = current_schema() '
                'AND tablename = %s AND indexname LIKE %s',
                [table, f'{old_prefix}%'],
            )
            stale = [row[0] for row in cursor.fetchall()]

            for name in stale:
                # Only the prefix changes, so the discriminating hash Django
                # generated stays intact and the name stays unique.
                new_name = f'{table}{name[len(old_prefix) :]}'
                cursor.execute(f'ALTER INDEX "{name}" RENAME TO "{new_name}"')


def restore_stale_indexes(apps, schema_editor):
    """Put the old names back, so the rename is reversible."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    with connection.cursor() as cursor:
        for old_prefix, table in RENAMED_TABLES.items():
            cursor.execute(
                'SELECT indexname FROM pg_indexes '
                'WHERE schemaname = current_schema() '
                'AND tablename = %s AND indexname LIKE %s',
                [table, f'{table}%'],
            )
            for name in [row[0] for row in cursor.fetchall()]:
                new_name = f'{old_prefix}{name[len(table) :]}'
                cursor.execute(f'ALTER INDEX "{name}" RENAME TO "{new_name}"')


class Migration(migrations.Migration):
    """Free the ``tasks_kanbancard`` index namespace before it is reused."""

    dependencies = [('tasks', '0022_workorder_rename_field_metadata')]

    operations = [migrations.RunPython(rename_stale_indexes, restore_stale_indexes)]
