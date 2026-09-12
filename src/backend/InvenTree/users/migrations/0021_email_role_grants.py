"""Expose email capabilities on every group, preserving only existing grants."""

from django.db import migrations


def populate_email_roles(apps, schema_editor):
    """Migrate legacy named groups once; names are not a runtime permission."""
    alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    RuleSet = apps.get_model('users', 'RuleSet')
    content_type, _ = ContentType.objects.using(alias).get_or_create(
        app_label='users', model='ruleset'
    )
    permissions = {}
    for action, label in (
        ('view', 'Can read emails and attachments'),
        ('send', 'Can send emails'),
    ):
        permissions[action], _ = Permission.objects.using(alias).get_or_create(
            content_type_id=content_type.pk,
            codename=f'{action}_email',
            defaults={'name': label},
        )
    for group in Group.objects.using(alias).iterator():
        existing_permissions = set(group.permissions.values_list('pk', flat=True))
        grants = {
            action: group.name == f'aimms.email.{action}'
            or permission.pk in existing_permissions
            for action, permission in permissions.items()
        }
        rule, _ = RuleSet.objects.using(alias).get_or_create(
            group_id=group.pk,
            name='email',
            defaults={
                'can_view': False,
                'can_add': False,
                'can_change': False,
                'can_delete': False,
            },
        )
        for action, granted in grants.items():
            if granted:
                setattr(rule, f'can_{action}_emails', True)
                group.permissions.add(permissions[action])
        rule.save(using=alias, update_fields=['can_view_emails', 'can_send_emails'])


class Migration(migrations.Migration):
    """No users, memberships, or default mailbox grants are created."""

    dependencies = [('users', '0020_email_permissions')]

    operations = [migrations.RunPython(populate_email_roles, migrations.RunPython.noop)]
