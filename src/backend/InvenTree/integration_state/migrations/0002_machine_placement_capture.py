"""Install placement capture only after the selected native schema boundary."""

from django.db import migrations

from integration_state.migration_operations import (
    apply_placement_capture,
    reverse_placement_capture,
)


class Migration(migrations.Migration):
    """Native operational histories remain unchanged."""

    dependencies = [
        ('integration_state', '0001_initial'),
        ('assets', '0016_assetmachine_placement_version_and_more'),
        ('legacy_boundary', '0002_verify_focus_checkpoint'),
    ]

    operations = [
        migrations.RunPython(apply_placement_capture, reverse_placement_capture)
    ]
