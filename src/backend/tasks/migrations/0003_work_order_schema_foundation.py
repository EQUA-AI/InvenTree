"""Add the maintenance work-order schema foundation."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_work_order_references(apps, schema_editor):
    """Assign deterministic references to legacy Kanban cards."""
    KanbanCard = apps.get_model('tasks', 'KanbanCard')
    cards = KanbanCard.objects.filter(reference__isnull=True).only('pk')

    for card in cards.iterator():
        card.reference = f'WO-{card.pk:06d}'
        card.save(update_fields=['reference'])


class Migration(migrations.Migration):
    """Add nullable work-order fields, audit models, and reference data."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('approvals', '0003_alter_approval_action_type'),
        ('assets', '0003_assetmachine_customer_idx'),
        ('company', '0080_company_tags'),
        ('tasks', '0002_kanbancardpart'),
    ]

    operations = [
        migrations.AddField(
            model_name='kanbancard',
            name='actual_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='actual_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='assigned_to',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assigned_work_orders',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='customer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='maintenance_work_orders',
                to='company.company',
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='estimated_minutes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='hold_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='lifecycle_status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('planned', 'Planned'),
                    ('ready', 'Ready'),
                    ('in_progress', 'In Progress'),
                    ('on_hold', 'On Hold'),
                    ('verifying', 'Verifying'),
                    ('completed', 'Completed'),
                    ('canceled', 'Canceled'),
                ],
                db_index=True,
                default='draft',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='lifecycle_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='machine',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='work_orders',
                to='assets.assetmachine',
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='reference',
            field=models.CharField(
                blank=True, db_index=True, max_length=32, null=True, unique=True
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='requested_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='requested_work_orders',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='scheduled_end',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='scheduled_start',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='kanbancard',
            name='work_order_type',
            field=models.CharField(
                choices=[
                    ('corrective', 'Corrective'),
                    ('preventive', 'Preventive'),
                    ('inspection', 'Inspection'),
                    ('calibration', 'Calibration'),
                    ('other', 'Other'),
                ],
                db_index=True,
                default='corrective',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='WorkOrderEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(db_index=True, max_length=40)),
                ('from_status', models.CharField(blank=True, max_length=20)),
                ('to_status', models.CharField(blank=True, max_length=20)),
                ('reason', models.TextField(blank=True)),
                ('correlation_id', models.UUIDField(db_index=True)),
                ('idempotency_key', models.CharField(blank=True, db_index=True, max_length=128)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('work_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='tasks.kanbancard')),
            ],
        ),
        migrations.CreateModel(
            name='WorkOrderDeviation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(db_index=True, max_length=40)),
                ('application_key', models.CharField(blank=True, max_length=128)),
                ('step_key', models.CharField(blank=True, max_length=128)),
                ('resource_key', models.CharField(blank=True, max_length=128)),
                ('expected', models.JSONField(blank=True, null=True)),
                ('actual', models.JSONField(blank=True, null=True)),
                ('reason', models.TextField()),
                ('resolution', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='work_order_deviations', to=settings.AUTH_USER_MODEL)),
                ('approval', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='work_order_deviations', to='approvals.approval')),
                ('work_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deviations', to='tasks.kanbancard')),
            ],
        ),
        migrations.CreateModel(
            name='WorkOrderCommand',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('command', models.CharField(max_length=64)),
                ('idempotency_key', models.CharField(max_length=128)),
                ('correlation_id', models.UUIDField(db_index=True)),
                ('request_hash', models.CharField(max_length=64)),
                ('status', models.CharField(db_index=True, max_length=32)),
                ('result_ref', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('work_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='commands', to='tasks.kanbancard')),
            ],
            options={'unique_together': {('work_order', 'idempotency_key')}},
        ),
        migrations.CreateModel(
            name='WorkOrderCloseout',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cause', models.TextField(blank=True)),
                ('action', models.TextField()),
                ('result', models.TextField()),
                ('verification_summary', models.TextField()),
                ('downtime_minutes', models.PositiveIntegerField(blank=True, null=True)),
                ('follow_up_required', models.BooleanField(default=False)),
                ('follow_up', models.TextField(blank=True)),
                ('completed_at', models.DateTimeField()),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('content_hash', models.CharField(max_length=64)),
                ('version', models.PositiveIntegerField(default=1)),
                ('completed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('verified_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('work_order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='structured_closeout', to='tasks.kanbancard')),
            ],
        ),
        migrations.AddIndex(
            model_name='kanbancard',
            index=models.Index(fields=['machine', 'lifecycle_status'], name='tasks_wo_machine_lifecycle'),
        ),
        migrations.AddIndex(
            model_name='kanbancard',
            index=models.Index(fields=['assigned_to', 'lifecycle_status'], name='tasks_wo_assignee_lifecycle'),
        ),
        migrations.AddIndex(
            model_name='kanbancard',
            index=models.Index(fields=['due_date'], name='tasks_wo_due_date'),
        ),
        migrations.AddIndex(
            model_name='workorderevent',
            index=models.Index(fields=['work_order', 'created_at'], name='tasks_wo_event_created'),
        ),
        migrations.RunPython(
            backfill_work_order_references,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
