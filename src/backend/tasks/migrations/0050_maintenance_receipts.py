"""Retain exact native maintenance receipts independently of kit continuation."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """New tables only; existing deployed writers need no new columns."""

    dependencies = [
        ('tasks', '0041_db_defaults_for_old_code'),
        # The compatibility replacement lives in another app. Keep an explicit
        # tasks parent so Django does not leave 0030 as a second tasks leaf.
        ('tasks', '0030_workorder_tasks_wo_actual_completed_and_more'),
        ('order', '0122_remove_purchaseorder_notes_remove_returnorder_notes_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MaintenanceReceipt',
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
                ('operation_key', models.CharField(max_length=128, unique=True)),
                ('request_hash', models.CharField(max_length=64)),
                ('request_snapshot', models.JSONField()),
                ('requirement_snapshot', models.JSONField()),
                (
                    'received_before',
                    models.DecimalField(decimal_places=5, max_digits=15),
                ),
                (
                    'received_after',
                    models.DecimalField(decimal_places=5, max_digits=15),
                ),
                ('response_snapshot', models.JSONField()),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'reservation_state',
                    models.CharField(default='pending', max_length=16),
                ),
                ('reservation_code', models.CharField(blank=True, max_length=64)),
                ('allocation_ids', models.JSONField(default=list)),
                ('continued_at', models.DateTimeField(null=True)),
                (
                    'actor',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'event',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.workorderevent',
                    ),
                ),
                (
                    'kit_line',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.jobkitline',
                    ),
                ),
                (
                    'purchase_order_line',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='maintenance_receipts',
                        to='order.purchaseorderlineitem',
                    ),
                ),
                (
                    'shortage',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.jobkitshortage',
                    ),
                ),
                (
                    'work_order',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.workorder',
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name='MaintenanceReceiptStock',
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
                ('snapshot', models.JSONField()),
                (
                    'receipt',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='stocks',
                        to='tasks.maintenancereceipt',
                    ),
                ),
                (
                    'stock_item',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to='stock.stockitem',
                    ),
                ),
            ],
        ),
    ]
