"""Link a machine anomaly to the repair packet raised for it.

Split out of 0007 deliberately. ``repair``'s own migrations need the evidence
snapshot table that 0007 creates, so declaring this foreign key there would make
the two apps depend on each other and Django would refuse to order them.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the repair-packet link once both apps' tables exist."""

    dependencies = [
        ('assets', '0007_healthsource_machinesignalbinding_machineanomaly_and_more'),
        ('repair', '0008_approvedrepairscope_repairinvestigationfinding'),
    ]

    operations = [
        migrations.AddField(
            model_name='machineanomaly',
            name='repair_packet',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='anomalies',
                to='repair.repairpacket',
                verbose_name='Repair Packet',
            ),
        )
    ]
