"""Add the durable WorkOrderDeletionRecord audit table (S5)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the durable governed-deletion record."""

    """Create the deletion-audit table for governed work-order delete."""

    dependencies = [
        ('assets', '0005_alter_assetmaintenancerecord_work_order'),
        ('company', '0080_company_tags'),
        ('tasks', '0011_backfill_assigned_to'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkOrderDeletionRecord',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'work_order_pk',
                    models.PositiveIntegerField(
                        db_index=True, help_text='Primary key of the deleted KanbanCard'
                    ),
                ),
                (
                    'reference',
                    models.CharField(blank=True, db_index=True, max_length=32),
                ),
                ('title', models.CharField(blank=True, max_length=200)),
                ('lifecycle_status', models.CharField(blank=True, max_length=20)),
                ('reason', models.TextField(blank=True)),
                ('correlation_id', models.UUIDField(db_index=True)),
                (
                    'idempotency_key',
                    models.CharField(blank=True, db_index=True, max_length=128),
                ),
                ('snapshot', models.JSONField(blank=True, default=dict)),
                ('deleted_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    'actor',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='deleted_work_orders',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'customer',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='deleted_work_orders',
                        to='company.company',
                    ),
                ),
                (
                    'machine',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='deleted_work_orders',
                        to='assets.assetmachine',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Work Order Deletion Record',
                'ordering': ['-deleted_at'],
            },
        )
    ]
