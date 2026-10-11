"""Join the two histories of ``assets`` that grew apart after 0014.

One branch is the pump-station work (0015 cusec unit, 0016 bay ownership of
OPC tags, 0017-0018 pegged and railed reading qualities, 0019 vote groups,
0020 when a reading last changed). The other is the customisation line (0015
anomaly work order, 0016 placement, 0017-0018 demo metrics, 0019 its own merge
of the registry, 0020 retiring the synthetic ledger). They touch different
fields, so there is nothing to reconcile: this only gives the graph one leaf.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """One leaf for the two branches; no operations."""

    dependencies = [
        ('assets', '0020_retire_synthetic_ledger'),
        ('assets', '0020_state_value_changed_at'),
    ]

    operations = []
