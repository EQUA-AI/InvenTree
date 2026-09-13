"""Stock approvals share real canonical effects with the proposal rail."""

from decimal import Decimal
from unittest.mock import patch

from aichat.models import StockCommandReceipt
from part.models import Part
from stock.models import StockItem, StockLocation

from . import services
from .executors import StockUpdateExecutor, registry
from .models import ApprovalStatus
from .tests import ApprovalTestBase


class InventoryApprovalTests(ApprovalTestBase):
    """No stub success, no REST-in-transaction and no second execution authority."""

    def setUp(self):
        """Create only disposable stock and grants."""
        super().setUp()
        self.enterContext(registry.replace_for_tests(StockUpdateExecutor()))
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.part = Part.objects.create(name='VOICE-TEST approval stock')
        self.location = StockLocation.objects.create(name='VOICE-TEST approval shelf')
        self.stock = StockItem.objects.create(
            part=self.part, location=self.location, quantity=10
        )

    def review(self, action='stock.add'):
        """Build the canonical review via the same serializer as the admin API."""
        approval = self._create_approval_obj(
            action_type='stock_update',
            assigned_to_user=self.user.pk,
            payload={
                'action': action,
                'intent': {'stock_item_id': self.stock.pk, 'quantity': '2.5'},
            },
        )
        services.open_approval(approval.pk, actor=self.user)
        services.confirm_viewed(approval.pk, actor=self.user)
        approval.refresh_from_db()
        return approval

    def test_real_once_only_execution_and_canonical_snapshot(self):
        """An approved request records the same stock command used by voice proposals."""
        approval = self.review()
        self.assertEqual(approval.payload['snapshot']['part_name'], self.part.name)
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.SUCCEEDED, result.data)
        replay = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result'], replay.data['execution_result']
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('12.5'))
        self.assertEqual(StockCommandReceipt.objects.count(), 1)
        self.assertEqual(approval.executed_effects.count(), 1)

    def test_drift_prevents_submission(self):
        """Quantity changed after review must never be silently overwritten."""
        approval = self.review('stock.count')
        StockItem.objects.filter(pk=self.stock.pk).update(quantity=20)
        result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_domain_failure_rolls_back_effect_and_receipts(self):
        """No stock side effect survives failed tracking persistence."""
        approval = self.review()
        with patch.object(
            StockItem,
            'add_tracking_entry',
            side_effect=RuntimeError('injected failure'),
        ):
            result = services.approve(approval.pk, actor=self.user)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 10)
        self.assertFalse(StockCommandReceipt.objects.exists())
        self.assertFalse(approval.executed_effects.exists())

    def test_review_permission_cannot_borrow_stock_authority(self):
        """The authenticated reviewer, never the payload author, owns execution rights."""
        approval = self.review()
        result = services.approve(approval.pk, actor=self.user2)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_legacy_direct_call_remains_fail_closed(self):
        """A string idempotency key is not an authenticated approval."""
        self.assertFalse(
            StockUpdateExecutor().execute({'action': 'stock.add'}, 'legacy').success
        )
