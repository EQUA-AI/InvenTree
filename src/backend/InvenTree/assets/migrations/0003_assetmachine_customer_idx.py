"""Ensure a DB index exists for AssetMachine.customer.

Django typically indexes ForeignKey columns by default, but we make this explicit
and safe to apply via IF NOT EXISTS.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_assetmachine_location_idx'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX IF NOT EXISTS assets_assetmachine_customer_id_idx "
                "ON public.assets_assetmachine (customer_id);"
            ),
            reverse_sql=(
                "DROP INDEX IF EXISTS public.assets_assetmachine_customer_id_idx;"
            ),
        ),
    ]
