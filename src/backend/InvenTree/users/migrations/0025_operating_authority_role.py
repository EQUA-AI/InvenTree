"""Expose an independent operations grant without promoting existing roles."""

from django.db import migrations, models


def preserve_operating_grants(apps, schema_editor):
    """Retain already authorized groups; no previously ungranted group gains access."""
    alias = schema_editor.connection.alias
    groups = (
        apps
        .get_model('auth', 'Group')
        .objects.using(alias)
        .filter(
            permissions__content_type__app_label='tasks',
            permissions__content_type__model='operationalauthorityrecord',
            permissions__codename='record_operating_authority',
        )
    )
    rules = apps.get_model('users', 'RuleSet').objects.using(alias)
    for group in groups.iterator():
        rules.update_or_create(
            group_id=group.pk,
            name='work_order',
            defaults={'can_record_operating_authority': True},
        )


class Migration(migrations.Migration):
    """Expose operations independently without changing existing grants."""

    dependencies = [
        ('users', '0024_notification_publish_permission'),
        ('tasks', '0033_operational_authority'),
    ]
    operations = [
        migrations.AddField(
            model_name='ruleset',
            name='can_record_operating_authority',
            field=models.BooleanField(
                default=False,
                verbose_name='Can record operating authority and service state',
            ),
        ),
        migrations.RunPython(preserve_operating_grants, migrations.RunPython.noop),
    ]
