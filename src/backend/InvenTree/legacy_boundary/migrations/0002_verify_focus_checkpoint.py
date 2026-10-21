"""Converge the focus branch with the independently recorded schema boundary."""

from django.db import migrations

from legacy_boundary.operations import VerifyCheckpoint


class Migration(migrations.Migration):
    """Require compatibility storage and both focus defaults before completion."""

    dependencies = [
        ('legacy_boundary', '0001_observed_schema_checkpoint'),
        ('aichat', '0055_chatthread_focus_db_defaults'),
    ]
    operations = [VerifyCheckpoint()]
