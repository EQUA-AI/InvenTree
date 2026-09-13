"""E19 in-app publisher permission is assignable without granting email access."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from users.serializers import RuleSetSerializer
from users.tasks import update_group_roles


class NotificationRoleTests(TestCase):
    """An explicit publisher checkbox grants only the canonical in-app permission."""

    def test_grant_rebuild_and_revoke(self):
        """Review, lifecycle and email permissions are not implied by publishing."""
        group = Group.objects.create(name='VOICE-TEST in-app publishers')
        user = get_user_model().objects.create_user('notification-publisher')
        user.groups.add(group)
        rule = group.rule_sets.get(name='work_order')
        self.assertFalse(RuleSetSerializer(rule).data['can_publish_notifications'])
        serializer = RuleSetSerializer(
            rule, data={'can_publish_notifications': True}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        update_group_roles(group)
        permission = Permission.objects.get(
            content_type__app_label='common', codename='add_notificationmessage'
        )
        self.assertEqual(set(group.permissions.all()), {permission})
        user = get_user_model().objects.get(pk=user.pk)
        self.assertTrue(user.has_perm('common.add_notificationmessage'))
        self.assertFalse(user.has_perm('approvals.review'))
        self.assertFalse(user.has_perm('users.send_email'))
        rule.can_publish_notifications = False
        rule.save(update_fields=['can_publish_notifications'])
        update_group_roles(group)
        self.assertFalse(
            get_user_model()
            .objects.get(pk=user.pk)
            .has_perm('common.add_notificationmessage')
        )
