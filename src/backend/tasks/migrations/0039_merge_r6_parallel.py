"""Join the parallel R6 batch migrations into one leaf (local only)."""

from django.db import migrations


class Migration(migrations.Migration):
    """Merge the O15 authority, data, process and media batches and O20."""

    dependencies = [
        ('tasks', '0033_operational_authority'),
        ('tasks', '0034_work_time_downtime'),
        ('tasks', '0035_process_decisions'),
        ('tasks', '0036_capture_originals_accepted_reports'),
        ('tasks', '0037_calibration_traceability'),
    ]

    operations = []
