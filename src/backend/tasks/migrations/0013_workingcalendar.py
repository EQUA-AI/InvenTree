"""Add the WorkingCalendar model (S6)."""

import django.db.models.deletion
from django.db import migrations, models

import tasks.models


class Migration(migrations.Migration):
    """Introduce the WorkingCalendar model."""

    """Create the working-calendar table."""

    dependencies = [
        ('assets', '0005_alter_assetmaintenancerecord_work_order'),
        ('company', '0080_company_tags'),
        ('tasks', '0012_workorderdeletionrecord'),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkingCalendar',
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
                    'name',
                    models.CharField(max_length=120, unique=True, verbose_name='Name'),
                ),
                (
                    'timezone',
                    models.CharField(
                        default='UTC',
                        help_text='IANA timezone name, e.g. "America/New_York"',
                        max_length=64,
                        verbose_name='Timezone',
                    ),
                ),
                (
                    'windows',
                    models.JSONField(
                        blank=True,
                        default=tasks.models._default_windows,
                        help_text='Weekday (0=Mon..6=Sun) to list of [open, close] time pairs',
                    ),
                ),
                (
                    'holidays',
                    models.JSONField(
                        blank=True, default=list, help_text='List of ISO date strings'
                    ),
                ),
                (
                    'is_default',
                    models.BooleanField(
                        db_index=True,
                        default=False,
                        help_text='The fallback calendar when nothing more specific matches',
                        verbose_name='Default',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'customer',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='working_calendars',
                        to='company.company',
                    ),
                ),
                (
                    'machine',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='working_calendars',
                        to='assets.assetmachine',
                    ),
                ),
            ],
            options={'verbose_name': 'Working Calendar', 'ordering': ['name']},
        )
    ]
