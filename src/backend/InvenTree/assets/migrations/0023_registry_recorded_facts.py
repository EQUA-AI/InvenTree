"""O15 attributed facts; local schema only, no legacy facts inferred."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add recording providers without backfilling unknown facts."""

    dependencies = [
        ('assets', '0022_var_unit'),
        ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
        ('stock', '0128_remove_stockitem_notes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='RegistryCommand',
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
                    'machine',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to='assets.assetmachine'
                    ),
                ),
                (
                    'actor',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                ('key', models.CharField(max_length=128)),
                ('request_hash', models.CharField(max_length=64)),
                ('result', models.JSONField()),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=['machine', 'actor', 'key'], name='registry_command_key'
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name='SignalDesignation',
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
                    'binding',
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name='designations',
                        to='assets.machinesignalbinding',
                    ),
                ),
                ('version', models.PositiveIntegerField()),
                ('critical', models.BooleanField()),
                ('binding_signature', models.CharField(max_length=64)),
                ('reason', models.TextField()),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-version'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=['binding', 'version'], name='signal_designation_version'
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name='ComponentFit',
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
                    'component',
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name='fits',
                        to='assets.assetcomponent',
                    ),
                ),
                (
                    'stock_item',
                    models.ForeignKey(on_delete=models.PROTECT, to='stock.stockitem'),
                ),
                (
                    'work_order',
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        to='tasks.workorder',
                    ),
                ),
                (
                    'usage',
                    models.OneToOneField(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        to='tasks.closeoutpartusage',
                    ),
                ),
                ('fitted_at', models.DateTimeField(blank=True, null=True)),
                (
                    'recorded_by',
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'verified_by',
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('verified_at', models.DateTimeField(null=True, blank=True)),
                ('reason', models.TextField()),
                ('retracted_at', models.DateTimeField(null=True, blank=True)),
                (
                    'retracted_by',
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('retraction_reason', models.TextField(blank=True)),
                (
                    'source',
                    models.CharField(
                        max_length=16,
                        choices=[
                            ('manual', 'Manual entry'),
                            ('closeout', 'Reconciled usage'),
                        ],
                    ),
                ),
                ('serial', models.CharField(max_length=100, blank=True)),
                ('lot', models.CharField(max_length=100, blank=True)),
            ],
            options={'ordering': ['-fitted_at', '-pk']},
        ),
        migrations.CreateModel(
            name='SharedPerformanceView',
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
                    'machine',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to='assets.assetmachine'
                    ),
                ),
                (
                    'owner',
                    models.ForeignKey(
                        on_delete=models.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                ('name', models.CharField(max_length=80)),
                ('config', models.JSONField()),
                ('is_default', models.BooleanField(default=False)),
                ('active', models.BooleanField(default=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'updated_by',
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={},
        ),
    ]
