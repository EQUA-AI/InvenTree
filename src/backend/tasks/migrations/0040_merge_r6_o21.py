"""Join O21's routine-finding origin with the merged R6 batch migrations (local only)."""

from django.db import migrations


class Migration(migrations.Migration):
    """Merge O21 (0038, built on 0035) with the parallel-batch merge (0039)."""

    dependencies = [
        ('tasks', '0038_routine_corrective_origin'),
        ('tasks', '0039_merge_r6_parallel'),
    ]

    operations = []
