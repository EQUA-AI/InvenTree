"""Add the job-kit planning models."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create job kits, planned lines, and shortage records."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('approvals', '0001_initial'),
        ('order', '0001_initial'),
        ('part', '0001_initial'),
        ('stock', '0001_initial'),
        ('tasks', '0004_procedure_procedurerevision_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='JobKit',
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
                    'status',
                    models.CharField(
                        choices=[
                            ('draft', 'Draft'),
                            ('short', 'Short'),
                            ('ready', 'Ready'),
                            ('staged', 'Staged'),
                            ('released', 'Released'),
                            ('closed', 'Closed'),
                            ('canceled', 'Canceled'),
                        ],
                        db_index=True,
                        default='draft',
                        max_length=16,
                    ),
                ),
                ('version', models.PositiveIntegerField(default=1)),
                ('source_application_hash', models.CharField(blank=True, max_length=64)),
                ('built_at', models.DateTimeField(blank=True, null=True)),
                ('staged_at', models.DateTimeField(blank=True, null=True)),
                ('released_at', models.DateTimeField(blank=True, null=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'created_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'staging_location',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='stock.stocklocation',
                    ),
                ),
                (
                    'work_order',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='job_kit',
                        to='tasks.kanbancard',
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name='JobKitLine',
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
                ('key', models.UUIDField(default=uuid.uuid4, editable=False)),
                ('sequence', models.PositiveIntegerField()),
                (
                    'kind',
                    models.CharField(
                        choices=[
                            ('part', 'Part'),
                            ('consumable', 'Consumable'),
                            ('tool', 'Tool'),
                            ('safety', 'Safety Equipment'),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    'required_quantity',
                    models.DecimalField(decimal_places=5, max_digits=15),
                ),
                ('required', models.BooleanField(default=True)),
                (
                    'fulfillment_mode',
                    models.CharField(
                        choices=[
                            ('reserve_consume', 'Reserve and Consume'),
                            ('checkout_return', 'Checkout and Return'),
                            ('verify_only', 'Verify Only'),
                        ],
                        max_length=24,
                    ),
                ),
                ('substitution_policy', models.CharField(default='none', max_length=20)),
                ('requires_scan', models.BooleanField(default=False)),
                ('source_snapshot', models.JSONField(blank=True, default=dict)),
                ('source', models.CharField(max_length=20)),
                ('note', models.TextField(blank=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'kit',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='lines',
                        to='tasks.jobkit',
                    ),
                ),
                (
                    'requested_part',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='job_kit_requests',
                        to='part.part',
                    ),
                ),
                (
                    'selected_part',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='job_kit_selections',
                        to='part.part',
                    ),
                ),
                (
                    'source_requirement',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='tasks.procedureresourcerequirement',
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name='JobKitShortage',
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
                ('quantity', models.DecimalField(decimal_places=5, max_digits=15)),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('open', 'Open'),
                            ('requested', 'Requested'),
                            ('ordered', 'Ordered'),
                            ('partial', 'Partially Received'),
                            ('received', 'Received'),
                            ('canceled', 'Canceled'),
                        ],
                        db_index=True,
                        default='open',
                        max_length=16,
                    ),
                ),
                ('reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                (
                    'approval',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='approvals.approval',
                    ),
                ),
                (
                    'line',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='shortages',
                        to='tasks.jobkitline',
                    ),
                ),
                (
                    'purchase_order_line',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='order.purchaseorderlineitem',
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name='jobkitline',
            constraint=models.UniqueConstraint(
                fields=('kit', 'sequence'),
                name='tasks_jobkitline_kit_sequence_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='jobkitline',
            constraint=models.UniqueConstraint(
                condition=models.Q(('source', 'procedure')),
                fields=('kit', 'source_requirement'),
                name='tasks_jobkitline_kit_source_req_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='jobkitline',
            constraint=models.CheckConstraint(
                condition=models.Q(('required_quantity__gt', 0)),
                name='tasks_jobkitline_qty_positive',
            ),
        ),
        migrations.AddConstraint(
            model_name='jobkitline',
            constraint=models.CheckConstraint(
                condition=models.Q(('sequence__gt', 0)),
                name='tasks_jobkitline_sequence_positive',
            ),
        ),
    ]
