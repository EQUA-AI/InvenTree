"""O15 process decisions; apply only in an explicitly approved environment."""

from django.conf import settings
from django.db import migrations, models


def expert_group(apps, schema_editor):
    """Create the configured routing group only if it is absent; grant no roles."""
    apps.get_model('auth', 'Group').objects.using(
        schema_editor.connection.alias
    ).get_or_create(
        name=getattr(settings, 'AIMMS_EXPERT_GROUP_NAME', 'Reliability engineers')
    )


class Migration(migrations.Migration):
    """Local process records, without acceptance or expert membership backfill."""

    dependencies = [
        ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
        ('assets', '0007_healthsource_machinesignalbinding_machineanomaly_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
        # Create the new group after historical blanket role grants.
        ('users', '0019_approval_review_role'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='WorkHandover',
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
                    'work_order',
                    models.ForeignKey(
                        'tasks.WorkOrder',
                        on_delete=models.CASCADE,
                        related_name='handovers',
                    ),
                ),
                (
                    'previous_owner',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name='+',
                    ),
                ),
                (
                    'incoming_owner',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name='+',
                    ),
                ),
                (
                    'proposed_by',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name='+',
                    ),
                ),
                ('proposed_version', models.PositiveIntegerField()),
                ('proposed_at', models.DateTimeField(auto_now_add=True)),
                (
                    'accepted_by',
                    models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name='+',
                    ),
                ),
                ('accepted_at', models.DateTimeField(null=True)),
                ('accepted_version', models.PositiveIntegerField(null=True)),
                ('withdrawn_at', models.DateTimeField(null=True)),
                ('previous_owner_name', models.CharField(max_length=320, blank=True)),
                ('incoming_owner_name', models.CharField(max_length=320, blank=True)),
                ('proposed_by_name', models.CharField(max_length=320, blank=True)),
                ('accepted_by_name', models.CharField(max_length=320, blank=True)),
            ],
        ),
        migrations.AddField(
            'workorder',
            'recurrence_of',
            models.ForeignKey(
                'tasks.WorkOrder',
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name='corrective_recurrences',
            ),
        ),
        migrations.AddField(
            'workorder',
            'recurrence_occurrence',
            models.OneToOneField(
                'assets.MachineAnomaly',
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name='recurrence_work',
            ),
        ),
        migrations.AddField(
            'investigationrequest',
            'escalation_group',
            models.ForeignKey(
                'auth.Group',
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name='+',
            ),
        ),
        migrations.AddField(
            'investigationrequest',
            'escalated_by',
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name='+',
            ),
        ),
        migrations.AddField(
            'investigationrequest',
            'escalated_at',
            models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            'investigationrequest', 'escalation_reason', models.TextField(blank=True)
        ),
        migrations.RunPython(expert_group, migrations.RunPython.noop),
    ]
