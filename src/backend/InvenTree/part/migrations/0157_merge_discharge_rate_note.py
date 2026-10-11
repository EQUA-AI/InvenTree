"""Join the two histories of ``part`` after 0154_partverificationsession_scope_client.

One branch is 0155_discharge_rate_note, a data migration that corrects the note
on one catalogue parameter template. The other is the customisation line's
upstream merge (0156). Neither touches what the other does.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """One leaf for the two branches; no operations."""

    dependencies = [
        ('part', '0155_discharge_rate_note'),
        ('part', '0156_merge_upstream_20260912'),
    ]

    operations = []
