"""Initial migration for the repair (Repair Packet) application."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('assets', '0003_assetmachine_customer_idx'),
        ('tasks', '0002_kanbancardpart'),
        ('approvals', '0002_alter_approval_options'),
    ]

    operations = [
        migrations.CreateModel(
            name='RepairPacket',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('draft', 'Draft'),
                            ('diagnosed', 'Diagnosed'),
                            ('approved', 'Approved'),
                            ('executing', 'Executing'),
                            ('closed', 'Closed'),
                            ('canceled', 'Canceled'),
                        ],
                        db_index=True,
                        default='draft',
                        max_length=20,
                        verbose_name='Status',
                    ),
                ),
                (
                    'reference',
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text='Auto-generated packet reference (e.g. RP-000123)',
                        max_length=32,
                        verbose_name='Reference',
                    ),
                ),
                ('fault_summary', models.TextField(blank=True, verbose_name='Fault Summary')),
                ('symptom', models.CharField(blank=True, max_length=255, verbose_name='Symptom')),
                (
                    'criticality',
                    models.CharField(
                        choices=[
                            ('low', 'Low'),
                            ('medium', 'Medium'),
                            ('high', 'High'),
                            ('critical', 'Critical'),
                        ],
                        db_index=True,
                        default='medium',
                        max_length=12,
                        verbose_name='Criticality',
                    ),
                ),
                ('production_impact', models.TextField(blank=True, verbose_name='Production Impact')),
                (
                    'diagnosis',
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text='Structured diagnosis result from the AI workflow',
                        verbose_name='Diagnosis',
                    ),
                ),
                ('closeout', models.JSONField(blank=True, default=dict, verbose_name='Closeout')),
                ('agent_run_id', models.CharField(blank=True, db_index=True, max_length=64, verbose_name='Agent Run ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='repair_packets',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Created By',
                    ),
                ),
                (
                    'machine',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='repair_packets',
                        to='assets.assetmachine',
                        verbose_name='Asset',
                    ),
                ),
                (
                    'maintenance_record',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='repair_packets',
                        to='assets.assetmaintenancerecord',
                        verbose_name='Maintenance Record',
                    ),
                ),
                (
                    'work_order',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='repair_packet',
                        to='tasks.kanbancard',
                        verbose_name='Work Order',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Repair Packet',
                'verbose_name_plural': 'Repair Packets',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='RepairPacketGate',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                (
                    'gate_type',
                    models.CharField(
                        choices=[
                            ('loto', 'Lockout/Tagout'),
                            ('permit', 'Permit'),
                            ('ppe', 'PPE'),
                            ('isolation', 'Isolation'),
                            ('hot_work', 'Hot Work'),
                            ('other', 'Other'),
                        ],
                        default='other',
                        max_length=16,
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('pending', 'Pending'),
                            ('confirmed', 'Confirmed'),
                            ('waived', 'Waived'),
                        ],
                        db_index=True,
                        default='pending',
                        max_length=12,
                    ),
                ),
                ('requires_photo', models.BooleanField(default=False)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'confirmed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'packet',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='gates',
                        to='repair.repairpacket',
                    ),
                ),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='RepairPacketEvidence',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'kind',
                    models.CharField(
                        choices=[('photo', 'Photo'), ('reading', 'Reading'), ('doc', 'Document')],
                        default='reading',
                        max_length=16,
                    ),
                ),
                ('label', models.CharField(blank=True, max_length=255)),
                ('value', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'packet',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='evidence',
                        to='repair.repairpacket',
                    ),
                ),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='RepairPacketApprovalLink',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'purpose',
                    models.CharField(
                        choices=[
                            ('spend', 'Spend'),
                            ('rfq', 'RFQ'),
                            ('po', 'Purchase Order'),
                            ('safety', 'Safety'),
                        ],
                        default='spend',
                        max_length=32,
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'approval',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='repair_packet_links',
                        to='approvals.approval',
                    ),
                ),
                (
                    'packet',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='approval_links',
                        to='repair.repairpacket',
                    ),
                ),
            ],
            options={'ordering': ['created_at']},
        ),
    ]
