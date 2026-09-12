"""Expose approval review in Group Roles without broadening existing grants."""

from django.db import migrations, models


def preserve_review_grants(apps, schema_editor):
    """Expose existing group grants without granting review to a new group."""
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    RuleSet = apps.get_model('users', 'RuleSet')
    groups = Group.objects.using(alias).filter(
        permissions__content_type__app_label='approvals',
        permissions__content_type__model='approval',
        permissions__codename='review',
    )
    for group in groups.iterator():
        RuleSet.objects.using(alias).update_or_create(
            group_id=group.pk,
            name='work_order',
            defaults={'can_review_approvals': True},
        )


class Migration(migrations.Migration):
    """Add the checkbox and preserve previously assigned review permissions."""

    dependencies = [('users', '0018_ruleset_can_apply_procedure_and_more')]

    operations = [
        migrations.AddField(
            model_name='ruleset',
            name='can_review_approvals',
            field=models.BooleanField(
                default=False,
                db_default=False,
                verbose_name='Can review approval requests',
            ),
        ),
        migrations.RunPython(preserve_review_grants, migrations.RunPython.noop),
    ]
