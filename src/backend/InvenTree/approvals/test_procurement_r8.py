"""R8 named placement, complete commercial drift and replay regression tests."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission

from order.models import PurchaseOrder, PurchaseOrderExtraLine
from order.status_codes import PurchaseOrderStatus

from . import services, test_purchasing
from .models import Approval, ApprovalStatus


class ProcurementR8Tests(test_purchasing.PurchasingApprovalTests):
    """Use the actual approval API and native serializers on isolated fixtures."""

    def setUp(self):
        """Add native charge authority to the existing named test approver."""
        super().setUp()
        self.user.user_permissions.add(
            Permission.objects.get(
                codename='add_purchaseorderextraline', content_type__app_label='order'
            )
        )

    def _placed_payload(self):
        payload = self._payload(operation='place_purchase_order')
        payload['line_items'][0].update(quantity='1', unit_price='620.00')
        payload['extra_lines'] = [
            {'description': 'Freight', 'quantity': '1', 'unit_price': '45.00'}
        ]
        payload['total'] = '665.00'
        return payload

    def _named_review(self, payload):
        approval = self._create_approval_obj(
            payload=payload, assigned_to_user_id=self.user.pk
        )
        services.open_approval(approval.pk, actor=self.user)
        services.confirm_viewed(approval.pk, actor=self.user)
        approval.refresh_from_db()
        return approval

    def test_named_placement_goods_freight_receipt_and_no_send(self):
        """One PO, one goods line, one freight and a named actor, with no mail."""
        approval = self._named_review(self._placed_payload())
        with (
            patch('order.models.notify_responsible') as notify,
            patch('ai.core.integrations.email.commands.get_gmail_client') as gmail,
            patch('django.core.mail.send_mail') as smtp,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                result = services.approve(approval.pk, actor=self.user)
                replay = services.approve(approval.pk, actor=self.user)
            notify.assert_not_called()
            gmail.assert_not_called()
            smtp.assert_not_called()
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        self.assertEqual(
            result.data['execution_result'], replay.data['execution_result']
        )
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.status, PurchaseOrderStatus.PLACED.value)
        self.assertEqual(order.lines.get().part_id, self.supplier_part.pk)
        self.assertEqual(order.extra_lines.get().price.amount, Decimal('45'))
        self.assertEqual(order.total_price.amount, Decimal('665'))
        self.assertEqual(
            result.data['execution_result']['approved_by_id'], self.user.pk
        )
        self.assertFalse(result.data['execution_result']['supplier_email_sent'])
        self.assertEqual(approval.executions.count(), 1)

    def test_unassigned_placement_cannot_execute(self):
        """Review authority alone cannot substitute for the assigned person."""
        approval = self._review(self._placed_payload())
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_placement_requires_screen(self):
        """The combined purchase path cannot be used as a service/voice bypass."""
        from .policy import ApprovalPolicyError, require_policy

        approval = self._named_review(self._placed_payload())
        for channel in ('voice', 'service'):
            with self.assertRaises(ApprovalPolicyError):
                require_policy(approval, actor=self.user, channel=channel)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_issue_snapshots_all_charges_and_detects_in_place_edit(self):
        """An existing charge ID does not prove its amount/currency stayed fixed."""
        order = self._draft()
        charge = PurchaseOrderExtraLine.objects.create(
            order=order,
            description='Freight',
            quantity=1,
            price=45,
            price_currency='USD',
        )
        approval = self._review(
            self._payload(
                operation='issue_purchase_order', order_id=order.pk, line_items=[]
            )
        )
        self.assertEqual(Decimal(approval.payload['total']), Decimal('51'))
        PurchaseOrderExtraLine.objects.filter(pk=charge.pk).update(price=46)
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrderStatus.PENDING.value)

    def test_charge_edit_between_preflight_and_dispatch_is_blocked(self):
        """Recheck full charges under the effect transaction, after preflight."""
        from . import purchasing

        order = self._draft()
        charge = PurchaseOrderExtraLine.objects.create(
            order=order,
            description='Freight',
            quantity=1,
            price=45,
            price_currency='USD',
        )
        approval = self._review(
            self._payload(
                operation='issue_purchase_order', order_id=order.pk, line_items=[]
            )
        )
        original = purchasing.execute

        def race(review, *, actor):
            PurchaseOrderExtraLine.objects.filter(pk=charge.pk).update(quantity=2)
            return original(review, actor=actor)

        with patch.object(purchasing, 'execute', side_effect=race):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrderStatus.PENDING.value)
        self.assertFalse(approval.executed_effects.exists())

    def test_issue_freight_discount_and_zero_price(self):
        """Native full totals include actual discounts and explicit zero charges."""
        order = self._draft()
        PurchaseOrderExtraLine.objects.create(
            order=order,
            description='Freight',
            quantity=1,
            price=45,
            price_currency='USD',
            discount=10,
        )
        PurchaseOrderExtraLine.objects.create(
            order=order,
            description='Handling',
            quantity=1,
            price=0,
            price_currency='USD',
        )
        approval = self._review(
            self._payload(
                operation='issue_purchase_order', order_id=order.pk, line_items=[]
            )
        )
        self.assertEqual(Decimal(approval.payload['total']), Decimal('46.5'))
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)

    def test_failed_freight_rolls_back_whole_placement(self):
        """A charge failure leaves no money or duplicateable partial PO effect."""
        approval = self._named_review(self._placed_payload())
        with patch(
            'order.serializers.PurchaseOrderExtraLineSerializer.save',
            side_effect=RuntimeError('injected freight failure'),
        ):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(approval.executed_effects.exists())

    def test_invalid_preconditions_cannot_enter_replay_contract(self):
        """Malformed ledger context is rejected as input, not a server error."""
        response = self._create_approval(
            payload=self._placed_payload(), preconditions=['invalid']
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Approval.objects.exists())

    def test_create_request_replays_after_placement_before_validation(self):
        """Recreating an already issued review returns it; changed intent conflicts."""
        from .tests import _make_approval_data

        order = self._draft()
        data = _make_approval_data(
            payload=self._payload(
                operation='issue_purchase_order', order_id=order.pk, line_items=[]
            )
        )
        response = self.client.post('/api/approvals/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        services.open_approval(pk, actor=self.user)
        services.confirm_viewed(pk, actor=self.user)
        services.approve(pk, actor=self.user)
        response = self.client.post('/api/approvals/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], pk)
        data['payload']['currency'] = 'EUR'
        response = self.client.post('/api/approvals/', data, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(Approval.objects.filter(pk=pk).count(), 1)
