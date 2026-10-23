"""R8 whole-chain acceptance with native HTTP receipts, use and real Closeout."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from approvals.models import Approval, ApprovalExecution
from assets.models import AssetMaintenanceRecord
from order.models import PurchaseOrderDocumentEvidence
from stock.models import StockItem, StockItemTracking, StockLocation
from tasks.closeout_models import CloseoutPartUsage
from tasks.models import JobKitAllocation, MaintenanceReceipt, WorkOrderCloseout
from tasks.services.closeout import complete_work_order
from tasks.services.closeout_reconcile import refresh_closeout_reconciliation
from tasks.tests import test_procurement as p1
from tasks.tests import test_procurement_integration as integrated
from tasks.tests.closeout_fixtures import VALID_CLOSEOUT, CloseoutEnvMixin


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_JOB_KITS_ENABLED=True,
    AIMMS_CLOSEOUT_WIZARD_ENABLED=True,
)
class R8AcceptanceTests(CloseoutEnvMixin, TestCase):
    """Use unchanged lane helpers without collecting their inherited test methods."""

    setUp = p1.ProcurementTests.setUp
    source_payload = p1.ProcurementTests.source_payload
    select = p1.ProcurementTests.select
    ack_payload = p1.ProcurementTests.ack_payload
    ack = p1.ProcurementTests.ack
    context = p1.ProcurementTests.context
    place_and_link = integrated.ProcurementIntegrationTests.place_and_link
    document = integrated.ProcurementIntegrationTests.document

    def chain_to_receipt(self, *, execute=True):
        """Prepare scoped source, reviewed placement, exact document and explicit ack."""
        self.source = self.select()
        self.api = self.place_and_link()
        self.evidence = self.document()[0]
        self.acknowledgment = self.ack()
        self.body = {
            'maintenance_operation_key': 'r8-fixture-1',
            'maintenance_requirement_version': self.line.version,
            'location': StockLocation.objects.create(
                name='R8 acceptance fixture stores'
            ).pk,
            'items': [
                {
                    'line_item': self.po_line.pk,
                    'quantity': '1',
                    'status': 10,
                    'batch_code': 'R8-FIXTURE-ACCEPTANCE',
                }
            ],
        }
        self.receipt_url = f'/api/order/po/{self.po.pk}/receive/'
        self.use_url = (
            f'/api/tasks/work-orders/{self.work_order.pk}/job-kit/allocations/'
        )
        with self.captureOnCommitCallbacks(execute=execute):
            response = self.api.post(self.receipt_url, self.body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def reload_api(self):
        """Discard in-memory browser/request state while retaining the named person."""
        api = APIClient()
        api.force_authenticate(self.actor)
        return api

    def test_source_to_closeout_retains_cost_and_exact_use_without_second_decrement(
        self,
    ):
        """A whole recorded-only journey retains separate authority and physical facts."""
        with (
            patch('django.core.mail.message.EmailMessage.send') as send,
            patch('order.models.notify_responsible') as notify,
        ):
            receipt = self.chain_to_receipt()
            stock = StockItem.objects.get(pk=receipt.data[0]['pk'])
            allocation = JobKitAllocation.objects.get(line=self.line)
            self.assertEqual(allocation.stock_item_id, stock.pk)
            before_issue = self.api.post(
                f'{self.use_url}{allocation.pk}/issue/',
                {},
                format='json',
                HTTP_X_AIMMS_RECONCILE_ONLY='1',
            )
            self.assertEqual(before_issue.status_code, 409, before_issue.data)
            issue = self.api.post(
                f'{self.use_url}{allocation.pk}/issue/', {}, format='json'
            )
            self.assertEqual(issue.status_code, 200, issue.data)
            self.assertEqual(issue.data['issued_by'], self.actor.pk)
            repeated_issue = self.reload_api().post(
                f'{self.use_url}{allocation.pk}/issue/',
                {},
                format='json',
                HTTP_X_AIMMS_RECONCILE_ONLY='1',
            )
            self.assertEqual(repeated_issue.status_code, 200, repeated_issue.data)
            self.assertEqual(repeated_issue.data, issue.data)
            duplicate_issue = self.reload_api().post(
                f'{self.use_url}{allocation.pk}/issue/', {}, format='json'
            )
            self.assertEqual(duplicate_issue.status_code, 409, duplicate_issue.data)
            # A Closeout row exists before use; recovery updates it from native tracking.
            refresh_closeout_reconciliation(
                work_order_id=self.work_order.pk, actor=self.actor
            )
            used = self.api.post(
                f'{self.use_url}{allocation.pk}/consume/', {}, format='json'
            )
            self.assertEqual(used.status_code, 200, used.data)
            tracking_count = StockItemTracking.objects.filter(item=stock).count()
            stock.refresh_from_db()
            remaining = stock.quantity
            retry = self.reload_api().post(
                f'{self.use_url}{allocation.pk}/consume/',
                {},
                format='json',
                HTTP_X_AIMMS_RECONCILE_ONLY='1',
            )
            self.assertEqual(retry.status_code, 200, retry.data)
            self.assertEqual(used.data, retry.data)
            for _ in range(2):
                refresh_closeout_reconciliation(
                    work_order_id=self.work_order.pk, actor=self.actor
                )
            usage = CloseoutPartUsage.objects.get(allocation=allocation)
            self.assertEqual(usage.state, 'reconciled')
            self.assertEqual(usage.stock_tracking_id, used.data['stock_tracking_id'])
            self.assertEqual(usage.used_quantity, Decimal('1'))
            self.assertEqual(usage.resolved_by_id, self.actor.pk)
            self.work_order.refresh_from_db()
            complete_args = {
                'work_order_id': self.work_order.pk,
                'actor': self.actor,
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'r8-fixture-2',
                'closeout': VALID_CLOSEOUT,
            }
            first = complete_work_order(**complete_args)
            retained = WorkOrderCloseout.objects.get(work_order=self.work_order)
            retained_hash = retained.content_hash
            self.assertEqual(
                complete_work_order(**complete_args).event_id, first.event_id
            )
            self.assertEqual(
                AssetMaintenanceRecord.objects.filter(
                    work_order=self.work_order
                ).count(),
                1,
            )
            # Terminal Work permits tracked readback only; ordinary use stays refused.
            readback = self.reload_api().post(
                f'{self.use_url}{allocation.pk}/consume/',
                {},
                format='json',
                HTTP_X_AIMMS_RECONCILE_ONLY='1',
            )
            self.assertEqual(readback.status_code, 200, readback.data)
            self.assertEqual(readback.data, used.data)
            refused = self.reload_api().post(
                f'{self.use_url}{allocation.pk}/consume/', {}, format='json'
            )
            self.assertEqual(refused.status_code, 409, refused.data)
            # Native receipt replay remains available after PO and Work completion.
            with self.captureOnCommitCallbacks(execute=True):
                recovered = self.reload_api().post(
                    self.receipt_url, self.body, format='json'
                )
            self.assertEqual(recovered.status_code, 201, recovered.data)
            self.assertEqual(recovered.data, receipt.data)
            row = self.context()['lines'][0]
            order = row['orders'][0]
            self.assertEqual(
                (
                    Decimal(order['goods_total']),
                    Decimal(order['charges_total']),
                    Decimal(order['order_total']),
                ),
                (Decimal('620'), Decimal('45'), Decimal('665')),
            )
            self.assertEqual(
                order['recorded_document']['context']['provenance_kind'], 'fixture'
            )
            self.assertEqual(order['recorded_document']['state'], 'recorded_not_sent')
            self.assertEqual(row['source_selection']['event_id'], self.source.pk)
            self.assertEqual(
                order['acknowledgment']['event_id'], self.acknowledgment.pk
            )
            self.assertEqual(Decimal(row['used_quantity']), 1)
            self.assertEqual(Decimal(row['outstanding_quantity']), 0)
            self.assertEqual(Decimal(row['reserved_quantity']), 0)
            stock.refresh_from_db()
            retained.refresh_from_db()
            self.assertEqual(stock.quantity, remaining)
            self.assertEqual(
                StockItemTracking.objects.filter(item=stock).count(), tracking_count
            )
            self.assertEqual(retained.content_hash, retained_hash)
            self.assertEqual(MaintenanceReceipt.objects.count(), 1)
            self.assertEqual(PurchaseOrderDocumentEvidence.objects.count(), 1)
            self.assertEqual(Approval.objects.count(), 1)
            self.assertEqual(
                ApprovalExecution.objects.filter(state='succeeded').count(), 1
            )
            send.assert_not_called()
            notify.assert_not_called()

    def test_reload_retries_failed_reservation_but_never_repeats_native_receive(self):
        """A timeout after commit preserves physical receipt while continuation recovers."""
        first = self.chain_to_receipt(execute=False)
        self.assertEqual(MaintenanceReceipt.objects.get().reservation_state, 'pending')
        before = StockItem.objects.count()
        # Material reads report pending without performing continuation.
        for _ in range(2):
            self.assertEqual(
                self.context()['lines'][0]['receipt_outcomes'][0]['reservation_state'],
                'pending',
            )
        with self.captureOnCommitCallbacks(execute=True):
            replay = self.reload_api().post(self.receipt_url, self.body, format='json')
        self.assertEqual(replay.data, first.data)
        self.assertEqual(StockItem.objects.count(), before)
        self.assertEqual(
            MaintenanceReceipt.objects.get().reservation_state, 'allocated'
        )

    def test_scope_change_after_receive_blocks_continuation_and_all_use_replay(self):
        """Access to the Work envelope never transfers old machine stock evidence."""
        self.chain_to_receipt(execute=False)
        self.work_order.machine = None
        self.work_order.save(update_fields=['machine'])
        before = StockItem.objects.count()
        response = self.reload_api().post(self.receipt_url, self.body, format='json')
        self.assertIn(response.status_code, (403, 404, 409))
        self.assertFalse(JobKitAllocation.objects.exists())
        self.assertEqual(StockItem.objects.count(), before)
        self.assertEqual(self.context()['lines'], [])

    def test_successful_use_replay_rechecks_current_actor_scope(self):
        """Consumption receipt is recoverable only inside current native stock authority."""
        self.chain_to_receipt()
        allocation = JobKitAllocation.objects.get(line=self.line)
        url = f'{self.use_url}{allocation.pk}/consume/'
        used = self.api.post(url, {}, format='json')
        self.assertEqual(used.status_code, 200, used.data)
        self.actor.maintenance_scopes = set()
        for headers in ({}, {'HTTP_X_AIMMS_RECONCILE_ONLY': '1'}):
            self.assertIn(
                self.reload_api().post(url, {}, format='json', **headers).status_code,
                (403, 404),
            )

    def test_reconcile_only_never_applies_an_uncommitted_use(self):
        """The recovery header cannot convert a readback into another stock removal."""
        self.chain_to_receipt()
        allocation = JobKitAllocation.objects.get(line=self.line)
        stock = StockItem.objects.get(pk=allocation.stock_item_id)
        before = stock.quantity
        response = self.api.post(
            f'{self.use_url}{allocation.pk}/consume/',
            {},
            format='json',
            HTTP_X_AIMMS_RECONCILE_ONLY='1',
        )
        self.assertEqual(response.status_code, 409, response.data)
        stock.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(stock.quantity, before)
        self.assertEqual(allocation.status, 'reserved')
        self.assertIsNone(allocation.stock_tracking_id)

    def test_machine_reassignment_cannot_transfer_receipt_custody(self):
        """Clearing the Work machine cannot transfer old receipt custody."""
        self.chain_to_receipt()
        allocation = JobKitAllocation.objects.get(line=self.line)
        self.work_order.machine = None
        self.work_order.save(update_fields=['machine'])
        response = self.reload_api().post(
            f'{self.use_url}{allocation.pk}/consume/', {}, format='json'
        )
        self.assertIn(response.status_code, (403, 404))
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, 'reserved')
