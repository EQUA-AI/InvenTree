"""Role-derived permissions must not depend on their previous database state."""

import json
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from users.models import RuleSet
from users.tasks import group_permission_plan, update_group_roles


class RoleRebuildTests(TestCase):
    """Isolated BOM inheritance and ordinary administrator changes."""

    def setUp(self):
        """No live users, provider calls or shared data."""
        self.group = Group.objects.create(name='role-rebuild-fixture')

    def role(self, name, **values):
        """Save the same model used by the administrator role editor."""
        role = self.group.rule_sets.get(name=name)
        for field in RuleSet.RULE_OPTIONS:
            setattr(role, field, values.get(field, False))
        role.save()

    def bom(self):
        """Current native BOM-item grants, not a cached user object."""
        return set(
            self.group.permissions.filter(
                content_type__app_label='part', content_type__model='bomitem'
            ).values_list('codename', flat=True)
        )

    def test_inherited_grants_survive_three_rebuilds_from_either_state(self):
        """The deployed present/remove/re-add cycle must not recur."""
        self.role('part', can_add=True, can_delete=True)
        grants = Permission.objects.filter(
            content_type__app_label='part', content_type__model='bomitem'
        )
        expected = {
            f'{action}_bomitem' for action in ('view', 'add', 'change', 'delete')
        }
        for initially_present in (True, False):
            with self.subTest(initially_present=initially_present):
                if initially_present:
                    self.group.permissions.add(*grants)
                else:
                    self.group.permissions.remove(*grants)
                for _ in range(3):
                    update_group_roles(self.group)
                    self.assertEqual(self.bom(), expected)

    def test_parent_revocation_is_immediate(self):
        """Do not preserve inherited grants from a stale parent snapshot."""
        self.role('part', can_add=True, can_delete=True)
        self.role('part')
        self.assertEqual(self.bom(), set())

    def test_change_does_not_grant_native_add_or_delete(self):
        """Preserve action-by-action native inheritance, not broader CRUD."""
        self.role('part', can_change=True)
        update_group_roles(self.group)
        self.assertEqual(self.bom(), {'view_bomitem', 'change_bomitem'})

    def test_overlapping_explicit_grant_survives_parent_revoke(self):
        """Another granting role wins until that grant is also revoked."""
        self.role('part', can_add=True)
        self.role('bom', can_delete=True)
        self.role('part')
        self.assertEqual(
            self.bom(), {'view_bomitem', 'change_bomitem', 'delete_bomitem'}
        )
        self.role('build', can_add=True)
        self.role('bom')
        self.assertEqual(self.bom(), {'view_bomitem', 'change_bomitem', 'add_bomitem'})
        self.role('build')
        self.assertEqual(self.bom(), set())

    def test_unmanaged_grants_membership_and_direct_grants_unchanged(self):
        """Rebuilding role-owned grants is not wholesale permission replacement."""
        unmanaged = Permission.objects.get(
            content_type__app_label='sessions', codename='view_session'
        )
        user = get_user_model().objects.create_user('role-rebuild-user')
        user.groups.add(self.group)
        user.user_permissions.add(unmanaged)
        self.group.permissions.add(unmanaged)
        self.role('part', can_add=True)
        for _ in range(3):
            update_group_roles(self.group)
        self.assertTrue(self.group.permissions.filter(pk=unmanaged.pk).exists())
        self.assertEqual(list(user.user_permissions.all()), [unmanaged])
        self.assertEqual(list(user.groups.all()), [self.group])

    def test_missing_rules_are_created_without_recursive_rebuild(self):
        """Missing rows are default-off and the final result stays deterministic."""
        self.group.rule_sets.filter(name='bom').delete()
        self.role('part', can_add=True)
        update_group_roles(self.group)
        self.assertEqual(self.group.rule_sets.filter(name='bom').count(), 1)
        self.assertEqual(self.bom(), {'view_bomitem', 'change_bomitem', 'add_bomitem'})

    def test_read_only_audit_does_not_rebuild_or_create_rules(self):
        """Review an actual pending diff without applying it at command startup."""
        self.role('part', can_add=True)
        self.group.permissions.remove(
            *Permission.objects.filter(codename='add_bomitem')
        )
        self.group.rule_sets.filter(name='bom').delete()
        output = StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command(
                'audit_role_permissions', groups=[self.group.pk], stdout=output
            )
        self.assertTrue(
            all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries)
        )
        result = json.loads(output.getvalue())
        self.assertEqual(result['groups'][0]['add'], ['part.add_bomitem'])
        self.assertFalse(self.group.rule_sets.filter(name='bom').exists())
        from InvenTree.ready import canAppAccessDatabase, isReadOnlyCommand

        with patch('sys.argv', ['manage.py', 'audit_role_permissions']):
            self.assertTrue(isReadOnlyCommand())
            self.assertFalse(canAppAccessDatabase(allow_test=True, allow_shell=True))

    def test_projection_failure_rolls_back_admin_role_write(self):
        """A failed permission diff cannot leave saved-but-unprojected authority."""
        with patch(
            'users.tasks.group_permission_plan',
            side_effect=RuntimeError('test rollback'),
        ):
            with self.assertRaises(RuntimeError):
                self.role('part', can_add=True)
        self.assertFalse(self.group.rule_sets.get(name='part').can_add)
        self.assertFalse(group_permission_plan(self.group).add)

    def test_partial_admin_save_does_not_revive_stale_fields(self):
        """A saved custom checkbox never republishes stale CRUD permissions."""
        self.role('part', can_add=True)
        stale = self.group.rule_sets.get(name='part')
        self.role('part')
        stale.can_view = True
        stale.save(update_fields=['can_view'])
        current = self.group.rule_sets.get(name='part')
        self.assertTrue(current.can_view)
        self.assertFalse(current.can_add)
        self.assertFalse(current.can_change)
        self.assertEqual(self.bom(), {'view_bomitem'})


@skipUnless(
    connection.vendor == 'postgresql',
    'Row-lock concurrency requires private PostgreSQL',
)
class ConcurrentRoleRebuildTests(TransactionTestCase):
    """SQLite cannot certify select_for_update serialization."""

    def test_concurrent_admin_saves_and_missing_rule_rebuilds(self):
        """Competing processes see one final role projection, not partial diffs."""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from django.db import close_old_connections

        group = Group.objects.create(name='concurrent-role-fixture')
        group.rule_sets.filter(name='build').delete()
        barrier = Barrier(2)

        def edit(name, field):
            close_old_connections()
            try:
                role = RuleSet.objects.get(group_id=group.pk, name=name)
                setattr(role, field, True)
                barrier.wait(timeout=5)
                role.save(update_fields=[field])
                for _ in range(3):
                    update_group_roles(Group.objects.get(pk=group.pk))
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(edit, 'part', 'can_add')
            second = executor.submit(edit, 'bom', 'can_delete')
            first.result(timeout=15)
            second.result(timeout=15)
        self.assertEqual(group.rule_sets.filter(name='build').count(), 1)
        self.assertFalse(group_permission_plan(group).add)
        self.assertFalse(group_permission_plan(group).remove)
        self.assertEqual(
            set(
                group.permissions.filter(content_type__model='bomitem').values_list(
                    'codename', flat=True
                )
            ),
            {f'{action}_bomitem' for action in ('view', 'add', 'change', 'delete')},
        )
