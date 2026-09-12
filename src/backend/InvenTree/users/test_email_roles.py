"""Email group-role assignment, revocation, migration and execution authority."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from django.urls import reverse

from asgiref.sync import async_to_sync
from rest_framework.test import APIClient

from ai.core.integrations.email.authorization import email_capability
from ai.core.integrations.email.commands import require_sender
from ai.core.tools.rbac import _native_pairs
from users.serializers import RuleSetSerializer
from users.tasks import update_group_roles


class EmailRoleTests(TestCase):
    """Ordinary groups get independent, default-off email capabilities."""

    def setUp(self):
        """Use isolated users/groups with no mailbox or provider calls."""
        self.group = Group.objects.create(name='VOICE-TEST email team')
        self.role = self.group.rule_sets.get(name='email')
        self.user = get_user_model().objects.create_user('email-role-user')
        self.user.groups.add(self.group)

    def _save(self, **values):
        serializer = RuleSetSerializer(self.role, data=values, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

    def _pairs(self):
        return _native_pairs(get_user_model().objects.get(pk=self.user.pk))

    def test_defaults_are_off_and_no_admin_permissions_are_implied(self):
        """Mailbox rights must not accidentally grant local user/group CRUD."""
        self.assertFalse(self.role.can_view_emails)
        self.assertFalse(self.role.can_send_emails)
        self.assertEqual(self.role.get_models(), [])
        self.assertEqual(self._pairs(), frozenset())
        self.assertFalse(self.group.permissions.exists())

    def test_view_and_send_are_independent_and_survive_rebuild(self):
        """An admin may grant read-only access or send-only access separately."""
        for view, send in ((True, False), (False, True), (True, True), (False, False)):
            self._save(can_view_emails=view, can_send_emails=send)
            update_group_roles(self.group)
            expected = {
                ('email', action)
                for action, granted in [('view', view), ('send', send)]
                if granted
            }
            self.assertEqual(self._pairs(), expected)
            self.role.refresh_from_db()
            self.assertFalse(
                any(
                    getattr(self.role, field)
                    for field in ('can_view', 'can_add', 'can_change', 'can_delete')
                )
            )
            permissions = set(self.group.permissions.values_list('codename', flat=True))
            self.assertEqual(permissions, {f'{action}_email' for _, action in expected})

    def test_legacy_name_is_not_a_runtime_grant_and_revoke_is_immediate(self):
        """Renaming a group cannot restore an unchecked permission."""
        self._save(can_send_emails=True)
        # Cache a grant on the old object, then revoke; command rehydrates it.
        self.assertTrue(self.user.has_perm('users.send_email'))
        self._save(can_send_emails=False)
        self.group.name = 'aimms.email.send'
        self.group.save()
        self.assertEqual(self._pairs(), frozenset())
        with self.assertRaises(PermissionError):
            require_sender(self.user)

    def test_grant_cannot_be_placed_on_an_unrelated_ruleset(self):
        """The Email row alone owns these permissions."""
        other = self.group.rule_sets.get(name='work_order')
        other.can_send_emails = True
        other.save()
        self.assertEqual(self._pairs(), frozenset())

    def test_inactive_and_removed_membership_cannot_send(self):
        """Current actor state is checked even with an old authenticated object."""
        self._save(can_send_emails=True)
        require_sender(self.user)
        self.user.groups.remove(self.group)
        with self.assertRaises(PermissionError):
            require_sender(self.user)
        self.user.groups.add(self.group)
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(PermissionError):
            require_sender(self.user)

    def test_permission_lookup_errors_fail_closed(self):
        """A permission backend failure cannot expose mailbox tools."""
        with patch.object(
            self.user, 'has_perm', side_effect=RuntimeError('unavailable')
        ):
            self.assertEqual(_native_pairs(self.user), frozenset())

    def test_read_tool_rechecks_role_after_it_was_offered(self):
        """Revoking inbox access stops invocation before any mailbox call."""
        provider = AsyncMock()

        @email_capability('view')
        async def recording_read():
            return await provider()

        self._save(can_view_emails=True)
        with patch(
            'ai.core.auth.get_current_principal',
            return_value=SimpleNamespace(user_pk=self.user.pk),
        ):
            async_to_sync(recording_read)()
            self._save(can_view_emails=False)
            with self.assertRaises(PermissionError):
                async_to_sync(recording_read)()
        provider.assert_awaited_once()

    def test_standard_admin_api_grants_persists_and_revokes(self):
        """The same API used by Group Roles exposes both named email fields."""
        admin = get_user_model().objects.create_superuser(
            'email-role-admin', 'admin@example.test', 'test'
        )
        client = APIClient()
        client.force_authenticate(admin)
        url = reverse('api-ruleset-detail', kwargs={'pk': self.role.pk})
        response = client.patch(
            url, {'can_view_emails': True, 'can_send_emails': True}, format='json'
        )
        self.assertEqual(response.status_code, 200, response.data)
        response = client.get(url)
        self.assertTrue(response.data['can_view_emails'])
        self.assertTrue(response.data['can_send_emails'])
        self.assertEqual(self._pairs(), {('email', 'view'), ('email', 'send')})
        response = client.patch(
            url, {'can_view_emails': False, 'can_send_emails': False}, format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._pairs(), frozenset())

    def test_mailbox_user_cannot_assign_roles_to_themselves(self):
        """Sending permission is not authority to edit a group's roles."""
        self._save(can_send_emails=True)
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.patch(
            reverse('api-ruleset-detail', kwargs={'pk': self.role.pk}),
            {'can_view_emails': True},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.role.refresh_from_db()
        self.assertFalse(self.role.can_view_emails)

    def test_migration_preserves_legacy_access_without_granting_other_groups(self):
        """Only prior view/send groups gain the corresponding checkbox on upgrade."""
        legacy_view = Group.objects.create(name='aimms.email.view')
        legacy_send = Group.objects.create(name='aimms.email.send')
        migration = importlib.import_module('users.migrations.0021_email_role_grants')
        historical_apps = (
            MigrationLoader(connection)
            .project_state([('users', '0021_email_role_grants')])
            .apps
        )
        migration.populate_email_roles(
            historical_apps, SimpleNamespace(connection=connection)
        )
        for group, view, send in (
            (legacy_view, True, False),
            (legacy_send, False, True),
            (self.group, False, False),
        ):
            role = group.rule_sets.get(name='email')
            self.assertEqual((role.can_view_emails, role.can_send_emails), (view, send))
            update_group_roles(group)
            self.assertEqual(
                group.permissions.filter(codename='view_email').exists(), view
            )
            self.assertEqual(
                group.permissions.filter(codename='send_email').exists(), send
            )
        self.assertEqual(
            set(self.user.groups.values_list('pk', flat=True)), {self.group.pk}
        )
