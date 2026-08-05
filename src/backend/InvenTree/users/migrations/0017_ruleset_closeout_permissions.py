"""Add named closeout actions to the work-order ruleset."""

from django.db import migrations, models

PERMISSION_FIELDS = {
    'capture_closeout': 'can_capture_closeout',
    'review_closeout': 'can_review_closeout',
    'reconcile_closeout_parts': 'can_reconcile_closeout_parts',
    'verify_closeout': 'can_verify_closeout',
    'amend_closeout': 'can_amend_closeout',
    'view_closeout_audit': 'can_view_closeout_audit',
}


def preserve_group_permissions(apps, schema_editor):
    """Seed work-order rulesets from closeout permissions already on groups."""
    Group = apps.get_model('auth', 'Group')
    RuleSet = apps.get_model('users', 'RuleSet')

    for group in Group.objects.prefetch_related('permissions__content_type'):
        granted = {
            permission.codename
            for permission in group.permissions.all()
            if permission.content_type.app_label == 'tasks'
            and permission.codename in PERMISSION_FIELDS
        }
        if not granted:
            continue

        ruleset, _created = RuleSet.objects.get_or_create(
            group=group, name='work_order'
        )
        fields = []
        for codename in granted:
            field = PERMISSION_FIELDS[codename]
            setattr(ruleset, field, True)
            fields.append(field)
        ruleset.save(update_fields=fields)


class Migration(migrations.Migration):
    """Add and backfill closeout permission fields."""

    dependencies = [('users', '0016_work_order_ruleset_grant')]

    operations = [
        migrations.AddField(
            model_name='ruleset',
            name='can_amend_closeout',
            field=models.BooleanField(
                default=False, verbose_name='Can amend completed closeouts'
            ),
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_capture_closeout',
            field=models.BooleanField(
                default=False, verbose_name='Can capture closeout narratives'
            ),
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_reconcile_closeout_parts',
            field=models.BooleanField(
                default=False, verbose_name='Can reconcile closeout part usage'
            ),
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_review_closeout',
            field=models.BooleanField(
                default=False, verbose_name='Can review closeout proposals'
            ),
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_verify_closeout',
            field=models.BooleanField(
                default=False, verbose_name='Can verify completed closeouts'
            ),
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_view_closeout_audit',
            field=models.BooleanField(
                default=False, verbose_name='Can view closeout audit surfaces'
            ),
        ),
        migrations.RunPython(preserve_group_permissions, migrations.RunPython.noop),
    ]
