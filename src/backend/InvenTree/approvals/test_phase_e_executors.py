"""E19 real effects, failure rollback and explicit no-email contracts."""

from unittest.mock import patch

from django.test import override_settings

from tasks.models import WorkOrder, WorkOrderCommand

from common.models import NotificationMessage
from company.models import Company
from order.models import SalesOrder
from part.models import Part

from . import services
from .executors import (
    NotificationExecutor,
    SalesOrderExecutor,
    WorkflowExecutor,
    registry,
)
from .review_sections import voice_eligibility
from .tests import ApprovalTestBase


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_workorder_voice_readback.cancellation_scope'
)
class PhaseEExecutorTests(ApprovalTestBase):
    """Local-only full approval dispatch over canonical domain services."""

    def setUp(self):
        """Register the real executors rather than recording substitutes."""
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        for executor in (
            SalesOrderExecutor(),
            WorkflowExecutor(),
            NotificationExecutor(),
        ):
            self.enterContext(registry.replace_for_tests(executor))
        self.customer = Company.objects.create(
            name='E1 cancellation customer', is_customer=True
        )
        self.part = Part.objects.create(name='VOICE-TEST salable part', salable=True)
        self.work_order = WorkOrder.objects.create(
            title='VOICE-TEST plan', customer=self.customer
        )

    def review(self, action, payload):
        """API serializer resolves the review contract as the current user."""
        approval = self._create_approval_obj(
            action_type=action, assigned_to_user=self.user.pk, payload=payload
        )
        services.open_approval(approval.pk, actor=self.user)
        services.confirm_viewed(approval.pk, actor=self.user)
        approval.refresh_from_db()
        return approval

    def sales(self):
        """Exact quantities and prices, without issuance or shipping fields."""
        return self.review(
            'sales_order',
            {
                'customer_id': self.customer.pk,
                'currency': 'USD',
                'description': 'VOICE-TEST draft only',
                'line_items': [
                    {'part_id': self.part.pk, 'quantity': '2.5', 'unit_price': '4.20'}
                ],
            },
        )

    def notification(self):
        """Explicit in-app-only notification, never an email address."""
        return self.review(
            'notification',
            {
                'channel': 'in_app',
                'recipients': [self.user2.pk],
                'title': 'VOICE-TEST record',
                'message': 'Reviewed in-app notification test.',
            },
        )

    def test_sales_draft_once_with_exact_lines_and_no_email(self):
        """A real draft and one ExecutedEffect replace the old stub success."""
        approval = self.sales()
        self.assertEqual(approval.payload['total'], '10.500')
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'succeeded', result.data
        )
        order = SalesOrder.objects.get()
        self.assertEqual(str(order.lines.get().quantity), '2.50000')
        self.assertEqual(str(order.lines.get().sale_price.amount), '4.200000')
        self.assertFalse(result.data['execution_result']['email_sent'])
        replay = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result'], replay.data['execution_result']
        )
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertFalse(voice_eligibility(approval)[0])

    def test_sales_drift_refuses_before_creation(self):
        """Names and units are part of the reviewed contract."""
        approval = self.sales()
        Part.objects.filter(pk=self.part.pk).update(units='kg')
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(SalesOrder.objects.exists())

    def test_sales_failure_rolls_back_draft(self):
        """Line creation failure leaves no orphan order or effect receipt."""
        approval = self.sales()
        with patch(
            'order.serializers.SalesOrderLineItemSerializer.save',
            side_effect=RuntimeError('injected failure'),
        ):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(SalesOrder.objects.exists())
        self.assertFalse(approval.executed_effects.exists())

    def test_workflow_is_canonical_one_step_and_screen_only(self):
        """Reviewed version and the approval key reach the canonical command."""
        approval = self.review(
            'workflow',
            {
                'workflow': 'update_work_order_plan',
                'work_order_id': self.work_order.pk,
                'fields': {'title': 'VOICE-TEST reviewed title'},
            },
        )
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'succeeded', result.data
        )
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.title, 'VOICE-TEST reviewed title')
        services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            WorkOrderCommand.objects.filter(command='update_plan').count(), 1
        )
        self.assertFalse(voice_eligibility(approval)[0])

    def test_workflow_drift_refuses_without_effect(self):
        """An old plan cannot overwrite a newly edited work order."""
        approval = self.review(
            'workflow',
            {
                'workflow': 'update_work_order_plan',
                'work_order_id': self.work_order.pk,
                'fields': {'title': 'New title'},
            },
        )
        WorkOrder.objects.filter(pk=self.work_order.pk).update(lifecycle_version=99)
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(WorkOrderCommand.objects.exists())

    def test_in_app_record_once_never_external_dispatch(self):
        """The receipt says recorded, not delivered or read, and never sends email."""
        approval = self.notification()
        with patch(
            'common.notifications.trigger_notification',
            side_effect=AssertionError('No channel dispatcher'),
        ):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'succeeded', result.data
        )
        receipt = result.data['execution_result']
        self.assertFalse(receipt['email_sent'])
        self.assertFalse(receipt['read'])
        self.assertEqual(NotificationMessage.objects.get().user_id, self.user2.pk)
        services.approve(approval.pk, actor=self.user)
        self.assertEqual(NotificationMessage.objects.count(), 1)
        self.assertFalse(voice_eligibility(approval)[0])

    def test_notification_drift_and_missing_publisher_permission(self):
        """Review permission alone cannot publish a message or borrow sender authority."""
        approval = self.notification()
        result = services.approve(approval.pk, actor=self.user2)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(NotificationMessage.objects.exists())

    def test_legacy_payloads_and_direct_execution_fail_closed(self):
        """A workflow ID, email recipient or string execution key grants nothing."""
        for executor, payload in (
            (WorkflowExecutor(), {'workflow_id': 1}),
            (
                NotificationExecutor(),
                {
                    'channel': 'email',
                    'recipients': ['nobody@example.invalid'],
                    'message': 'test',
                },
            ),
            (SalesOrderExecutor(), {'customer_id': self.customer.pk}),
        ):
            self.assertTrue(executor.validate(payload))
            self.assertFalse(executor.execute(payload, 'unbound-key').success)

    def test_notification_partial_failure_rolls_back_every_record(self):
        """A failure after canonical insertion cannot leave a partial broadcast."""
        from plugin.builtin.integration.core_notifications import (
            InvenTreeUINotifications,
        )

        approval = self.notification()
        original = InvenTreeUINotifications.send_notification

        def fail_after_insert(handler, **kwargs):
            original(handler, **kwargs)
            raise RuntimeError('injected after canonical insertion')

        with patch.object(
            InvenTreeUINotifications, 'send_notification', fail_after_insert
        ):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(NotificationMessage.objects.exists())
        self.assertFalse(approval.executed_effects.exists())

    def test_inactive_recipient_invalidates_review(self):
        """Recipient activity is rechecked before any in-app record is created."""
        approval = self.notification()
        self.user2.is_active = False
        self.user2.save(update_fields=['is_active'])
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(NotificationMessage.objects.exists())
