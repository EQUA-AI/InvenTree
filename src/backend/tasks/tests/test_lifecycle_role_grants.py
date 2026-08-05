"""Lifecycle permissions: declared, grantable, and honoured end-to-end.

Found live 2026-08-05: every maintenance lifecycle codename
(``tasks.plan_workorder`` and friends) was enforced by ``require_permission``
but declared in no model ``Meta`` - the Permission rows did not exist, they
were ungrantable by any mechanism, and the whole work-order write path was
silently superuser-only. These tests pin the fix and, via the declaration
invariant, make the entire enforced-but-undeclared bug class fail loudly.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from InvenTree.unit_test import InvenTreeAPITestCase
from tasks import permissions as task_permissions
from tasks.models import WorkOrderLifecycle
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


class PermissionDeclarationInvariantTest(TestCase):
    """Every enforced permission constant must exist as a real Permission row.

    ``require_permission(actor, 'tasks.x')`` with an undeclared ``x`` is not a
    tighter policy - it is an ungrantable one, satisfiable only by superusers.
    """

    def test_every_enforced_codename_is_declared(self):
        """Each ``tasks.*`` constant in tasks.permissions has a Permission row."""
        constants = [
            value
            for name, value in vars(task_permissions).items()
            if name.isupper() and isinstance(value, str) and value.startswith('tasks.')
        ]
        self.assertGreaterEqual(len(constants), 15)

        missing = []
        for value in constants:
            app_label, codename = value.split('.', 1)
            if not Permission.objects.filter(
                content_type__app_label=app_label, codename=codename
            ).exists():
                missing.append(value)

        self.assertEqual(
            missing,
            [],
            'Enforced permissions with no Permission row (undeclared in any '
            f'model Meta - ungrantable, superuser-only): {missing}',
        )


class LifecycleRoleGrantFlowTest(CloseoutEnvMixin, TestCase):
    """A ruleset-granted (non-superuser) user can run the flow that was dark.

    The grant path is the group role editor: ruleset booleans, synced by
    update_group_roles - no direct permission assignment anywhere.
    """

    def setUp(self):
        """Scoped environment plus one role-granted technician."""
        self.build_env(lifecycle=WorkOrderLifecycle.READY)
        self.tech = get_user_model().objects.create_user(
            username='role-tech', email='role-tech@example.com', password='pw'
        )
        group = Group.objects.create(name='Lifecycle technicians')
        ruleset = group.rule_sets.get(name='work_order')
        for field in (
            'can_view',
            'can_add',
            'can_change',
            'can_execute_workorder',
            'can_capture_closeout',
        ):
            setattr(ruleset, field, True)
        ruleset.save()
        self.tech.groups.add(group)
        # Fresh instance: has_perm caches on the user object.
        self.tech = get_user_model().objects.get(pk=self.tech.pk)
        self.tech.maintenance_scopes = self.actor.maintenance_scopes

    def test_role_granted_user_starts_work_and_captures_closeout(self):
        """READY -> IN_PROGRESS -> narrative capture, all via role grants."""
        from tasks.services.closeout_capture import create_capture
        from tasks.services.work_orders import transition_work_order

        self.assertTrue(self.tech.has_perm('tasks.execute_workorder'))
        self.assertTrue(self.tech.has_perm('tasks.capture_closeout'))
        self.assertFalse(self.tech.is_superuser)

        with self.settings(**CLOSEOUT_FLAGS):
            transition_work_order(
                work_order_id=self.work_order.pk,
                actor=self.tech,
                to_status=WorkOrderLifecycle.IN_PROGRESS,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='role-grant-start',
            )
            self.work_order.refresh_from_db()
            self.assertEqual(
                self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
            )

            capture = create_capture(
                work_order_id=self.work_order.pk,
                actor=self.tech,
                narrative='Replaced the filter; flow restored and verified.',
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='role-grant-capture',
            )
            self.assertIsNotNone(capture)

    def test_ungrated_user_is_still_refused(self):
        """Without the ruleset boolean the same call fails closed."""
        from django.core.exceptions import PermissionDenied

        from tasks.services.work_orders import transition_work_order

        bare = self.make_scoped_user('bare-tech')
        with (
            self.settings(**CLOSEOUT_FLAGS),
            self.assertRaisesMessage(PermissionDenied, 'tasks.execute_workorder'),
        ):
            transition_work_order(
                work_order_id=self.work_order.pk,
                actor=bare,
                to_status=WorkOrderLifecycle.IN_PROGRESS,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='role-grant-denied',
            )


class LifecycleRuleSetApiTest(InvenTreeAPITestCase):
    """The lifecycle grants are managed through the standard RuleSet API."""

    roles = 'all'

    def test_update_lifecycle_permission(self):
        """Mirrors the closeout API test for a lifecycle codename."""
        from django.urls import reverse

        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()

        group = self.group
        ruleset = group.rule_sets.get(name='work_order')
        url = reverse('api-ruleset-detail', kwargs={'pk': ruleset.pk})
        permission = {
            'content_type__app_label': 'tasks',
            'codename': 'plan_workorder',
        }

        response = self.patch(
            url, data={'can_plan_workorder': True}, expected_code=200
        )
        self.assertTrue(response.data['can_plan_workorder'])
        self.assertTrue(group.permissions.filter(**permission).exists())

        response = self.patch(
            url, data={'can_plan_workorder': False}, expected_code=200
        )
        self.assertFalse(response.data['can_plan_workorder'])
        self.assertFalse(group.permissions.filter(**permission).exists())
