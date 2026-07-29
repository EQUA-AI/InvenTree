"""Drop the sales-customer identity from machines.

Machines are identified by their client (internal tenant) only; a customer
relationship is a claim about a work order or procedure, not about an asset.
The raw index created by 0003_assetmachine_customer_idx dies with the column
(Postgres cascades it; SQLite rebuilds the table), and 0003 itself must stay:
it is a named dependency of tasks and repair migrations.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('assets', '0009_default_client_backfill'),
    ]

    operations = [
        migrations.RemoveField(model_name='assetmachine', name='customer'),
    ]
