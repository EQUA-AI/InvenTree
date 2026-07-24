"""Convert KanbanCard.tags from a PostgreSQL ArrayField to a portable JSONField.

The historical 0001_initial migration has been rewritten to create the column as
JSON directly, so fresh databases (any vendor) need no work here. Databases which
already applied the original ArrayField version still hold a varchar[] column and
are converted in place, preserving data.
"""

from django.db import migrations


def convert_tags_column(apps, schema_editor):
    """Convert an existing varchar[] tags column to jsonb (PostgreSQL only)."""
    connection = schema_editor.connection

    if connection.vendor != 'postgresql':
        return

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'tasks_kanbancard' AND column_name = 'tags'
            """
        )
        row = cursor.fetchone()

        if row and row[0] == 'ARRAY':
            cursor.execute(
                """
                ALTER TABLE tasks_kanbancard
                ALTER COLUMN tags DROP DEFAULT,
                ALTER COLUMN tags TYPE jsonb USING to_jsonb(tags),
                ALTER COLUMN tags SET DEFAULT '[]'::jsonb
                """
            )


class Migration(migrations.Migration):
    """Convert legacy ArrayField tags columns to jsonb in deployed databases."""

    dependencies = [('tasks', '0017_kanbancard_card_kind_kanbancard_parent')]

    operations = [
        migrations.RunPython(convert_tags_column, migrations.RunPython.noop)
    ]
