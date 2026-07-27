"""RBAC for the work-order (Kanban) and asset endpoints.

Before the ``work_order`` ruleset existed these endpoints guarded only with
``IsAuthenticatedOrReadScope``: any authenticated user could create, edit, move and
archive any card, on any customer's machine. These tests pin the new behaviour, and
in particular pin the two things that would silently undo it:

* the grant migration -- without it every group gets an all-``False`` ruleset by
  default and the task page goes dark for every non-superuser on deploy; and
* the mapping from HTTP method to permission, so that "can view the board" never
  quietly becomes "can reschedule the board".
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import WorkOrder
from users.models import RuleSet
from users.ruleset import RULESET_NAMES, RuleSetEnum


def _grant(group, ruleset_name, **permissions):
    """Set one ruleset's permissions for a group, creating the row if needed."""
    ruleset, _created = RuleSet.objects.get_or_create(group=group, name=ruleset_name)

    for field, value in permissions.items():
        setattr(ruleset, field, value)

    ruleset.save()
    return ruleset


class WorkOrderRulesetRegistrationTest(TestCase):
    """The ruleset must be registered before anything can depend on it."""

    def test_ruleset_is_registered(self):
        self.assertIn(RuleSetEnum.WORK_ORDER, RULESET_NAMES)
        self.assertEqual(RuleSetEnum.WORK_ORDER, 'work_order')

    def test_ruleset_covers_work_order_and_asset_models(self):
        from users.ruleset import get_ruleset_models

        models = get_ruleset_models()[RuleSetEnum.WORK_ORDER]

        self.assertEqual(
            set(models),
            {
                'tasks_workorder',
                'tasks_workorderpart',
                'assets_assetmachine',
                'assets_assetmaintenancerecord',
                'assets_machinepart',
            },
        )

    def test_new_groups_get_a_work_order_ruleset(self):
        """``update_group_roles`` back-fills rulesets; it must know about this one."""
        group = Group.objects.create(name='freshly-created')

        self.assertTrue(
            group.rule_sets.filter(name=RuleSetEnum.WORK_ORDER).exists(),
            'a new group did not receive a work_order ruleset',
        )

    def test_new_groups_default_to_no_access(self):
        """Fail-closed for anything created after the grant migration."""
        group = Group.objects.create(name='defaults-check')
        ruleset = group.rule_sets.get(name=RuleSetEnum.WORK_ORDER)

        self.assertFalse(ruleset.can_view)
        self.assertFalse(ruleset.can_add)
        self.assertFalse(ruleset.can_change)
        self.assertFalse(ruleset.can_delete)


class WorkOrderPermissionEnforcementTest(TestCase):
    """Each HTTP method maps to the permission it should require."""

    def setUp(self):
        self.group = Group.objects.create(name='techs')
        self.user = get_user_model().objects.create_user(
            username='tech', email='tech@example.com', password='pw'
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)

        self.machine = AssetMachine.objects.create(name='RBAC Press')
        self.work_order = WorkOrder.objects.create(
            title='RBAC card', status='backlog', priority='low', machine=self.machine
        )
        self.list_url = reverse('kanban-card-list')
        self.detail_url = reverse(
            'kanban-card-detail', kwargs={'pk': self.work_order.pk}
        )

    def _set(self, **permissions):
        _grant(self.group, RuleSetEnum.WORK_ORDER, **permissions)

    def test_no_permissions_denies_reading(self):
        self._set(can_view=False, can_add=False, can_change=False, can_delete=False)

        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_view_permission_allows_reading(self):
        self._set(can_view=True)

        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.get(self.detail_url).status_code, 200)

    def test_view_permission_does_not_allow_editing(self):
        """The distinction this ruleset exists for: reading is not scheduling."""
        self._set(can_view=True, can_change=False)

        response = self.client.patch(
            self.detail_url,
            data={'scheduled_start': '2026-08-01T09:00:00Z'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.work_order.refresh_from_db()
        self.assertIsNone(self.work_order.scheduled_start)

    def test_change_permission_allows_rescheduling(self):
        self._set(can_view=True, can_change=True)

        response = self.client.patch(
            self.detail_url,
            data={'scheduled_start': '2026-08-01T09:00:00Z'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.work_order.refresh_from_db()
        self.assertIsNotNone(self.work_order.scheduled_start)

    def test_add_permission_is_required_to_create(self):
        self._set(can_view=True, can_add=False)

        payload = {
            'title': 'Nope',
            'status': 'backlog',
            'priority': 'low',
            'machine': self.machine.pk,
        }
        self.assertEqual(
            self.client.post(
                self.list_url, data=payload, content_type='application/json'
            ).status_code,
            403,
        )

        self._set(can_view=True, can_add=True)
        self.assertEqual(
            self.client.post(
                self.list_url, data=payload, content_type='application/json'
            ).status_code,
            201,
        )

    def test_delete_permission_is_required_to_archive(self):
        self._set(can_view=True, can_change=True, can_delete=False)

        self.assertEqual(self.client.delete(self.detail_url).status_code, 403)
        self.work_order.refresh_from_db()
        self.assertTrue(self.work_order.is_active)

        self._set(can_view=True, can_change=True, can_delete=True)
        self.assertEqual(self.client.delete(self.detail_url).status_code, 204)
        self.work_order.refresh_from_db()
        self.assertFalse(self.work_order.is_active)

    def test_restore_is_gated_on_change_rather_than_add(self):
        """Restore is a POST, but it modifies an existing card rather than adding one.

        ``RuleSet.save()`` enforces ``can_add or can_delete implies can_change``, so
        "add without change" is not a representable state and cannot be asserted.
        The representable and meaningful case is the inverse: a user who may change
        but not add. Under the default POST -> 'add' mapping they would be refused;
        under ``role_required = 'work_order.change'`` they are allowed, which is the
        correct reading of what restoring a card is.
        """
        self.work_order.is_active = False
        self.work_order.save(update_fields=['is_active'])
        url = reverse('kanban-card-restore', kwargs={'pk': self.work_order.pk})

        self._set(can_view=True, can_add=False, can_change=False)
        self.assertEqual(self.client.post(url).status_code, 403)

        self._set(can_view=True, can_add=False, can_change=True)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.work_order.refresh_from_db()
        self.assertTrue(self.work_order.is_active)

    def test_allocate_parts_is_gated_on_change_rather_than_add(self):
        """Re-checking stock allocation edits the card; it does not create one."""
        url = reverse('kanban-card-allocate', kwargs={'work_order_pk': self.work_order.pk})

        self._set(can_view=True, can_add=False, can_change=False)
        self.assertEqual(self.client.post(url).status_code, 403)

        self._set(can_view=True, can_add=False, can_change=True)
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_add_implies_change(self):
        """Pin the InvenTree invariant the two tests above depend on.

        If this ever stops holding, those tests are asserting something weaker
        than they claim to and should be revisited.
        """
        ruleset = _grant(
            self.group, RuleSetEnum.WORK_ORDER, can_add=True, can_change=False
        )
        ruleset.refresh_from_db()

        self.assertTrue(ruleset.can_change)
        self.assertTrue(ruleset.can_view)

    def test_superuser_bypasses_the_ruleset(self):
        superuser = get_user_model().objects.create_superuser(
            username='rbac-sup', email='sup@example.com', password='pw'
        )
        self.client.force_login(superuser)

        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_anonymous_access_is_still_denied(self):
        self.client.logout()

        self.assertIn(self.client.get(self.list_url).status_code, (401, 403))


class AssetPermissionEnforcementTest(TestCase):
    """Machines and maintenance history share the work-order ruleset."""

    def setUp(self):
        self.group = Group.objects.create(name='asset-techs')
        self.user = get_user_model().objects.create_user(
            username='asset-tech', email='at@example.com', password='pw'
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Asset RBAC Machine')

    def test_machines_require_view_permission(self):
        url = reverse('asset-machine-list')

        _grant(self.group, RuleSetEnum.WORK_ORDER, can_view=False)
        self.assertEqual(self.client.get(url).status_code, 403)

        _grant(self.group, RuleSetEnum.WORK_ORDER, can_view=True)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_editing_a_machine_requires_change_permission(self):
        url = reverse('asset-machine-detail', kwargs={'pk': self.machine.pk})

        _grant(self.group, RuleSetEnum.WORK_ORDER, can_view=True, can_change=False)
        response = self.client.patch(
            url,
            data={'description': 'should not apply'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

        _grant(self.group, RuleSetEnum.WORK_ORDER, can_view=True, can_change=True)
        response = self.client.patch(
            url, data={'description': 'applied'}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.description, 'applied')


class WorkOrderGrantMigrationTest(TestCase):
    """The migration that stops the rollout locking everyone out.

    The forward function is exercised directly against the live app registry
    rather than through a migration executor: what matters is the behaviour on the
    two shapes it meets in the wild -- a group with no ``work_order`` ruleset, and
    a group that already has one created with ``False`` defaults by the
    ``post_save`` signal on ``Group``.
    """

    def _forward(self):
        """Load and run the migration's forward function."""
        import importlib

        from django.apps import apps

        module = importlib.import_module(
            'users.migrations.0016_work_order_ruleset_grant'
        )
        module.grant_work_order_ruleset(apps, None)

    def test_grants_full_access_to_a_group_with_no_ruleset(self):
        group = Group.objects.create(name='no-ruleset-yet')
        # The post_save signal back-fills rulesets, so remove it to reproduce the
        # pre-migration shape of a group that predates this ruleset entirely.
        group.rule_sets.filter(name=RuleSetEnum.WORK_ORDER).delete()

        self._forward()

        ruleset = group.rule_sets.get(name=RuleSetEnum.WORK_ORDER)
        self.assertTrue(ruleset.can_view)
        self.assertTrue(ruleset.can_add)
        self.assertTrue(ruleset.can_change)
        self.assertTrue(ruleset.can_delete)

    def test_upgrades_a_locked_ruleset_created_by_the_signal(self):
        """The lockout case: the signal got there first, with all-False defaults."""
        group = Group.objects.create(name='signal-created')
        ruleset = group.rule_sets.get(name=RuleSetEnum.WORK_ORDER)
        self.assertFalse(ruleset.can_view)

        self._forward()

        ruleset.refresh_from_db()
        self.assertTrue(ruleset.can_view)
        self.assertTrue(ruleset.can_change)

    def test_is_idempotent(self):
        group = Group.objects.create(name='rerun')

        self._forward()
        self._forward()

        self.assertEqual(
            group.rule_sets.filter(name=RuleSetEnum.WORK_ORDER).count(), 1
        )

    def test_a_granted_group_can_actually_use_the_api(self):
        """End to end: the migration's output is a working permission."""
        group = Group.objects.create(name='post-migration')
        user = get_user_model().objects.create_user(
            username='migrated', email='m@example.com', password='pw'
        )
        user.groups.add(group)
        self._forward()

        self.client.force_login(user)

        self.assertEqual(
            self.client.get(reverse('kanban-card-list')).status_code, 200
        )
