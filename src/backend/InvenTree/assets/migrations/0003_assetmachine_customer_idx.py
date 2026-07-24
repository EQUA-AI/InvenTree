"""Ensure a DB index exists for AssetMachine.customer.

Django indexes ForeignKey columns by default on every backend, so this is a
belt-and-suspenders index for PostgreSQL deployments only. The raw SQL uses
PostgreSQL-specific syntax and must not run on other database vendors.
"""

from django.db import migrations


def create_customer_index(apps, schema_editor):
    """Create the customer index explicitly (PostgreSQL only)."""
    if schema_editor.connection.vendor != 'postgresql':
        return

    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS assets_assetmachine_customer_id_idx '
        'ON public.assets_assetmachine (customer_id);'
    )


def drop_customer_index(apps, schema_editor):
    """Drop the explicitly created customer index (PostgreSQL only)."""
    if schema_editor.connection.vendor != 'postgresql':
        return

    schema_editor.execute(
        'DROP INDEX IF EXISTS public.assets_assetmachine_customer_id_idx;'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_assetmachine_location_idx'),
    ]

    operations = [
        migrations.RunPython(create_customer_index, drop_customer_index)
    ]
