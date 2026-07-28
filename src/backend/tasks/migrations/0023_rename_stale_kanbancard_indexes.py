"""Rename indexes still carrying the model's old table name.

Renaming a table does not rename its indexes: after 0021 every index on
``tasks_workorder`` was still called ``tasks_kanbancard_*``, and the same for
the two satellite tables. That was cosmetic until now.

It stops being cosmetic in the next migration. The board card takes the
``tasks_kanbancard`` name, and Django derives index names from the table and
column - so creating it fails with ``index tasks_kanbancard_card_kind_...
already exists``, pointing at an index belonging to a completely different
table. Any deployment that ran the rename hits this, so the stale names are
cleared before the name is reused.

Both engines collide. An earlier revision of this migration skipped everything
but PostgreSQL, on the assumption that SQLite scopes index names per table. It
does not - index names share one namespace per database file - so SQLite hit
exactly the same collision, and the failure was worse there because it happens
during test-database setup and takes down suites that have nothing to do with
this app.

The two engines need different mechanics rather than different outcomes:
PostgreSQL renames in place, SQLite has no ``ALTER INDEX ... RENAME`` and must
drop and recreate from the stored definition.
"""

from django.db import migrations

#: old table prefix -> the table it became
RENAMED_TABLES = {
    'tasks_kanbancard': 'tasks_workorder',
    'tasks_kanbancardpart': 'tasks_workorderpart',
    'tasks_kanbancarddependency': 'tasks_workorderdependency',
}


def _rename_indexes(schema_editor, mapping):
    """Re-prefix every index on ``table`` whose name starts with ``old``.

    ``mapping`` is ``{old_prefix: table}``. Only the prefix changes, so the
    discriminating hash Django generated stays intact and names stay unique.
    """
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        for old_prefix, table in mapping.items():
            if connection.vendor == 'postgresql':
                cursor.execute(
                    'SELECT indexname, NULL FROM pg_indexes '
                    'WHERE schemaname = current_schema() '
                    'AND tablename = %s AND indexname LIKE %s',
                    [table, f'{old_prefix}%'],
                )
            else:
                # ``sql`` is NULL for the implicit indexes SQLite builds behind
                # UNIQUE constraints. Those cannot be dropped or recreated
                # independently of the table, and they are never named after
                # the model, so they are filtered out rather than skipped later.
                cursor.execute(
                    # ``%s`` rather than ``?``: Django's SQLite cursor
                    # translates the placeholders, and hands raw ``?`` to its
                    # own query logger unformatted.
                    'SELECT name, sql FROM sqlite_master '
                    "WHERE type = 'index' AND tbl_name = %s AND name LIKE %s "
                    'AND sql IS NOT NULL',
                    [table, f'{old_prefix}%'],
                )

            stale = cursor.fetchall()

            for name, definition in stale:
                new_name = f'{table}{name[len(old_prefix) :]}'

                if connection.vendor == 'postgresql':
                    cursor.execute(f'ALTER INDEX "{name}" RENAME TO "{new_name}"')
                else:
                    # Recreate from the definition SQLite stored, with only the
                    # index's own name swapped - the column list and any
                    # uniqueness are carried over verbatim.
                    cursor.execute(f'DROP INDEX "{name}"')
                    cursor.execute(definition.replace(name, new_name, 1))


def rename_stale_indexes(apps, schema_editor):
    """Move each index onto the name its table now has."""
    _rename_indexes(schema_editor, RENAMED_TABLES)


def restore_stale_indexes(apps, schema_editor):
    """Put the old names back, so the rename is reversible."""
    _rename_indexes(
        schema_editor, {table: old for old, table in RENAMED_TABLES.items()}
    )


class Migration(migrations.Migration):
    """Free the ``tasks_kanbancard`` index namespace before it is reused."""

    dependencies = [('tasks', '0022_workorder_rename_field_metadata')]

    operations = [migrations.RunPython(rename_stale_indexes, restore_stale_indexes)]
