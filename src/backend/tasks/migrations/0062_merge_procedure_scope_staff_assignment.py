"""Join additive procedure ownership and staff assignment migration branches."""

from django.db import migrations


class Migration(migrations.Migration):
    """Preserve both already-published migration identities."""

    dependencies = [
        ('tasks', '0061_procedure_client_scope'),
        ('tasks', '0061_staff_assignment_requirements'),
    ]

    operations = []
