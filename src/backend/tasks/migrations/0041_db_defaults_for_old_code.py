"""Give the columns R6 added to existing tables a database default.

``AddField`` sets a default only while it fills existing rows and then drops
it, so a NOT NULL column whose default lives in Python alone rejects every
INSERT from code that does not know the column. The schema is applied ahead of
the image that knows these fields, so the running older app must keep
inserting work orders and closeout captures. Each default equals the Python
one; no row is changed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Expand only: SET DEFAULT on four columns, values unchanged."""

    dependencies = [('tasks', '0040_merge_r6_o21')]

    operations = [
        migrations.AlterField(
            model_name='closeoutcapture',
            name='voice_unavailable_reason',
            field=models.CharField(
                blank=True, db_default='', default='', max_length=500
            ),
        ),
        migrations.AlterField(
            model_name='investigationrequest',
            name='escalation_reason',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='workorder',
            name='corrective_finding',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='workorder',
            name='corrective_origin_kind',
            field=models.CharField(
                blank=True,
                choices=[
                    ('recurrence', 'Recurrence'),
                    ('routine_finding', 'Routine finding'),
                ],
                db_default='',
                default='',
                max_length=24,
            ),
        ),
    ]
