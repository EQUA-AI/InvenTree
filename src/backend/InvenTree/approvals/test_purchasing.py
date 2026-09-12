"""Real canonical purchasing effects on isolated database fixtures only."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission

from company.models import Company, SupplierPart
from order.models import PurchaseOrder, PurchaseOrderLineItem
from order.status_codes import PurchaseOrderStatus
from part.models import Part

from . import services
from .executors import PurchaseOrderExecutor, registry
from .models import Approval, ApprovalStatus
from .tests import ApprovalTestBase


class PurchasingApprovalTests(ApprovalTestBase):
    """Draft/line/issue are separate reviewed, permissioned and atomic actions."""

    def setUp(self):
        """Create test-only supplier/part and explicit purchasing permissions."""
        super().setUp()
        self.enterContext(registry.replace_for_tests(PurchaseOrderExecutor()))
        self.supplier = Company.objects.create(
            name='VOICE-TEST supplier', is_supplier=True
        )
        self.part = Part.objects.create(
            name='VOICE-TEST bearing', purchaseable=True, active=True
        )
        self.supplier_part = SupplierPart.objects.create(
            supplier=self.supplier, part=self.part, SKU='VOICE-TEST-1'
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='order',
                codename__in=(
                    'add_purchaseorder',
                    'change_purchaseorder',
                    'add_purchaseorderlineitem',
                ),
            )
        )

    def _payload(self, **overrides):
        result = {
            'supplier_id': self.supplier.pk,
            'currency': 'USD',
            'description': 'VOICE-TEST draft only',
            'line_items': [
                {
                    'supplier_part_id': self.supplier_part.pk,
                    'quantity': '2',
                    'unit_price': '3.00',
                }
            ],
        }
        result.update(overrides)
        return result

    def _review(self, payload):
        approval = self._create_approval_obj(payload=payload)
        services.open_approval(approval.pk, actor=self.user)
        services.confirm_viewed(approval.pk, actor=self.user)
        approval.refresh_from_db()
        return approval

    def _draft(self):
        approval = self._review(self._payload())
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        return PurchaseOrder.objects.get(pk=result.data['execution_result']['order_id'])

    def test_create_canonical_draft_and_once_only_receipt(self):
        """One reviewed draft and exact prices, without issuance or email."""
        approval = self._review(self._payload())
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        replay = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result'], replay.data['execution_result']
        )
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.created_by_id, self.user.pk)
        self.assertEqual(order.status, PurchaseOrderStatus.PENDING.value)
        self.assertEqual(order.lines.get().quantity, Decimal('2'))
        self.assertEqual(order.lines.get().purchase_price.amount, Decimal('3.00'))
        self.assertEqual(approval.executed_effects.count(), 1)
        self.assertFalse(result.data['execution_result']['email_sent'])

    def test_review_resolves_names_and_total_not_client_labels(self):
        """The authoritative identity is visible before assent."""
        approval = self._review(self._payload(supplier_name='untrusted name'))
        self.assertEqual(approval.payload['supplier_name'], self.supplier.name)
        self.assertEqual(approval.payload['line_items'][0]['part_name'], self.part.name)
        self.assertEqual(Decimal(approval.payload['total']), Decimal('6.00'))

    def test_missing_purchasing_grant_cannot_borrow_requester_permission(self):
        """Review role is not purchasing authority and payload actor IDs are ignored."""
        approval = self._review(self._payload(actor_id=self.user.pk))
        result = services.approve(approval.pk, actor=self.user2)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_line_failure_rolls_back_order_and_receipt(self):
        """All database writes share the dispatch transaction."""
        approval = self._review(self._payload())
        with patch(
            'order.serializers.PurchaseOrderLineItemSerializer.save',
            side_effect=RuntimeError('injected line failure'),
        ):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(PurchaseOrderLineItem.objects.exists())
        self.assertFalse(approval.executed_effects.exists())

    def test_add_line_and_issue_are_distinct_actions(self):
        """Adding retains draft status; issuance uses the canonical Placed state."""
        order = self._draft()
        add = self._review(
            self._payload(operation='add_po_line_item', order_id=order.pk)
        )
        result = services.approve(add.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        services.approve(add.pk, actor=self.user)
        self.assertEqual(order.lines.count(), 2)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrderStatus.PENDING.value)
        issue = self._review(
            self._payload(
                operation='issue_purchase_order', order_id=order.pk, line_items=[]
            )
        )
        self.assertEqual(len(issue.payload['line_items']), 2)
        result = services.approve(issue.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrderStatus.PLACED.value)
        self.assertFalse(result.data['execution_result']['email_sent'])

    def test_order_drift_blocks_addition(self):
        """Another actor's order edits require a fresh review."""
        order = self._draft()
        approval = self._review(
            self._payload(operation='add_po_line_item', order_id=order.pk)
        )
        order.description = 'Other actor changed the order'
        order.save()
        with self.assertRaises(services.ApprovalConflictError):
            services.approve(approval.pk, actor=self.user)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.CHANGES_REQUESTED)
        self.assertEqual(order.lines.count(), 1)
        self.assertFalse(approval.executions.exists())

    def test_zero_price_and_empty_draft_do_not_issue(self):
        """Zero is explicit; empty drafts remain unissued."""
        payload = self._payload()
        payload['line_items'][0]['unit_price'] = '0'
        approval = self._review(payload)
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        self.assertEqual(
            PurchaseOrder.objects.get().lines.get().purchase_price.amount, 0
        )
        empty = self._review(self._payload(line_items=[]))
        result = services.approve(empty.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        self.assertEqual(PurchaseOrder.objects.count(), 2)

    def test_ambiguous_parts_and_fabricated_total_are_refused(self):
        """No best-guess part or understated total can enter the inbox."""
        SupplierPart.objects.create(
            supplier=self.supplier, part=self.part, SKU='VOICE-TEST-2'
        )
        for payload in (
            self._payload(
                line_items=[{'part_id': self.part.pk, 'quantity': 2, 'unit_price': 3}]
            ),
            self._payload(total='0.01'),
        ):
            response = self._create_approval(payload=payload)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Approval.objects.exists())
        self.assertFalse(PurchaseOrder.objects.exists())
