"""Merged R8 source -> named purchase -> recorded bytes -> receipt integration."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from approvals import services
from approvals.executors import PurchaseOrderExecutor, registry
from approvals.models import Approval
from approvals.tests import _make_approval_data
from order.models import PurchaseOrder, PurchaseOrderDocumentEvidence
from order.procurement_evidence import record_documents
from stock.models import StockLocation
from tasks.jobkit_models import JobKitShortage
from tasks.models import WorkOrderCommand
from tasks.services.job_kits import link_po_to_shortage
from tasks.services.procurement import event_matches_context
from tasks.tests import test_procurement as p1
from tasks.tests.closeout_fixtures import CloseoutEnvMixin
from tasks.workorder_serializers import WorkOrderEventSerializer


@override_settings(AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_JOB_KITS_ENABLED=True)
class ProcurementIntegrationTests(CloseoutEnvMixin, TestCase):
    """Keep the unchanged lane fixture helpers without collecting inherited tests."""

    setUp = p1.ProcurementTests.setUp
    source_payload = p1.ProcurementTests.source_payload
    select = p1.ProcurementTests.select
    ack_payload = p1.ProcurementTests.ack_payload
    ack = p1.ProcurementTests.ack
    context = p1.ProcurementTests.context

    def place_and_link(self, *, link=True):
        """Exercise assigned screen review and actual durable native placement."""
        self.enterContext(registry.replace_for_tests(PurchaseOrderExecutor()))
        api = APIClient()
        api.force_authenticate(self.actor)
        payload = {
            'operation': 'place_purchase_order',
            'supplier_id': self.supplier.pk,
            'currency': 'USD',
            'description': 'R8 integration fixture',
            'line_items': [
                {
                    'supplier_part_id': self.supplier_part.pk,
                    'quantity': '1',
                    'unit_price': '620',
                }
            ],
            'extra_lines': [
                {'description': 'Fixture freight', 'quantity': '1', 'unit_price': '45'}
            ],
            'total': '665',
        }
        response = api.post(
            '/api/approvals/',
            _make_approval_data(payload=payload, assigned_to_user_id=self.actor.pk),
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        approval = Approval.objects.get(pk=response.data['id'])
        services.open_approval(approval.pk, actor=self.actor)
        services.confirm_viewed(approval.pk, actor=self.actor)
        with self.captureOnCommitCallbacks(execute=True):
            result = services.approve(approval.pk, actor=self.actor)
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'succeeded', result.data
        )
        self.po = PurchaseOrder.objects.get(
            pk=result.data['execution_result']['order_id']
        )
        self.po_line = self.po.lines.get()
        self.shortage = JobKitShortage.objects.create(line=self.line, quantity=1)
        if link:
            link_po_to_shortage(
                work_order_id=self.work_order.pk,
                shortage_id=self.shortage.pk,
                purchase_order_line_id=self.po_line.pk,
                actor=self.actor,
            )
        return api

    def document(self):
        """Render and retain exact native PDF/MIME bytes without delivery."""
        return record_documents(
            order_id=self.po.pk,
            actor=self.actor,
            data={
                'operation_key': 'r8-integration-document',
                'to': 'supplier@example.com',
                'from_address': 'buyer@example.com',
                'ship_to': 'Fixture receiving street',
                'buyer_name': 'Fixture buyer',
                'provenance_kind': 'fixture',
            },
        )

    def test_full_chain_is_exact_replay_safe_read_only_and_scoped(self):
        """All producer facts survive shared projection and retry without sends."""
        with (
            patch('django.core.mail.message.EmailMessage.send') as send,
            patch('order.models.notify_responsible') as notify,
        ):
            source = self.select()
            api = self.place_and_link()
            evidence, created = self.document()
            self.assertTrue(created)
            self.assertEqual(self.document()[0].pk, evidence.pk)
            ack = self.ack()
            location = StockLocation.objects.create(name='R8 fixture receiving')
            body = {
                'maintenance_operation_key': 'r8-integration-receipt',
                'maintenance_requirement_version': self.line.version,
                'location': location.pk,
                'items': [
                    {
                        'line_item': self.po_line.pk,
                        'quantity': '1',
                        'status': 10,
                        'batch_code': 'R8-INTEGRATION',
                    }
                ],
            }
            with self.captureOnCommitCallbacks(execute=True):
                receipt = api.post(
                    f'/api/order/po/{self.po.pk}/receive/', body, format='json'
                )
            self.assertEqual(receipt.status_code, 201, receipt.data)
            with self.captureOnCommitCallbacks(execute=True):
                replay = api.post(
                    f'/api/order/po/{self.po.pk}/receive/', body, format='json'
                )
            self.assertEqual(receipt.data, replay.data)
            before = WorkOrderCommand.objects.count()
            with (
                patch(
                    'tasks.services.maintenance_receipts.continue_receipt'
                ) as continuation,
                patch(
                    'django.db.models.Model.save',
                    side_effect=AssertionError('GET attempted a save'),
                ),
            ):
                row = self.context()['lines'][0]
            continuation.assert_not_called()
            self.assertEqual(WorkOrderCommand.objects.count(), before)
            self.assertEqual(row['source_selection']['event_id'], source.pk)
            order = row['orders'][0]
            self.assertEqual(__import__('decimal').Decimal(order['order_total']), 665)
            self.assertEqual(order['binding']['snapshot']['source_event_id'], source.pk)
            execution = order['binding']['snapshot']['approval_execution']
            self.assertEqual(execution['actor_id'], self.actor.pk)
            self.assertEqual(
                execution['result']['payload']['charge_ids'],
                list(self.po.extra_lines.values_list('pk', flat=True)),
            )
            self.assertEqual(order['recorded_document']['pk'], evidence.pk)
            self.assertEqual(order['recorded_document']['attachment_state'], 'verified')
            self.assertEqual(order['acknowledgment']['event_id'], ack.pk)
            self.assertEqual(
                row['receipts'][0]['snapshot']['stocks'][0]['stock_item'],
                receipt.data[0]['pk'],
            )
            self.assertEqual(
                row['receipt_outcomes'][0]['reservation_state'], 'allocated'
            )
            self.assertEqual(
                row['receipt_allocations'][0]['snapshot']['allocation_ids'],
                row['receipt_outcomes'][0]['allocation_ids'],
            )
            self.assertEqual(row['outstanding_quantity'], '0')
            self.assertEqual(PurchaseOrderDocumentEvidence.objects.count(), 1)
            send.assert_not_called()
            notify.assert_not_called()
        # Retain receipt-time scope when Work is moved to another machine.
        self.work_order.machine = None
        self.work_order.save(update_fields=['machine'])
        request = type('Request', (), {'user': self.actor})()
        for event in self.work_order.events.filter(event_type__startswith='PROC_'):
            self.assertFalse(event_matches_context(event, self.work_order))
            self.assertEqual(
                WorkOrderEventSerializer(event, context={'request': request}).data[
                    'metadata'
                ],
                {'hidden': True},
            )

    def test_document_before_link_is_retained_without_duplicate_reference(self):
        """A document recorded before the Work handoff remains discoverable."""
        self.select()
        self.place_and_link(link=False)
        evidence, _ = self.document()
        for _ in range(2):
            link_po_to_shortage(
                work_order_id=self.work_order.pk,
                shortage_id=self.shortage.pk,
                purchase_order_line_id=self.po_line.pk,
                actor=self.actor,
            )
        row = self.context()['lines'][0]['orders'][0]
        self.assertEqual(row['recorded_document']['pk'], evidence.pk)
        self.assertEqual(
            self.work_order.events.filter(event_type='PROC_PO_EMAIL_RECORDED').count(),
            1,
        )

    def test_email_evidence_requires_its_native_role_in_both_projections(self):
        """Purchasing scope alone cannot expose native document/email evidence."""
        from django.contrib.auth.models import Group

        from users.models import RuleSet

        self.select()
        self.place_and_link()
        self.document()
        reader = self.make_scoped_user('r8-po-reader')
        group = Group.objects.create(name='R8 integration purchasing readers')
        RuleSet.objects.update_or_create(
            group=group, name='purchase_order', defaults={'can_view': True}
        )
        reader.groups.add(group)
        order = self.context(reader)['lines'][0]['orders'][0]
        self.assertIsNone(order['recorded_email'])
        self.assertIsNone(order['recorded_document'])
        request = type('Request', (), {'user': reader})()
        event = self.work_order.events.get(event_type='PROC_PO_EMAIL_RECORDED')
        self.assertEqual(
            WorkOrderEventSerializer(event, context={'request': request}).data[
                'metadata'
            ],
            {'hidden': True},
        )

    def test_consumed_previous_part_does_not_fulfill_new_selection(self):
        """Readout agrees with P3's current-Part fulfillment calculation."""
        from decimal import Decimal

        from part.models import Part
        from stock.models import StockItem
        from tasks.jobkit_models import JobKitAllocation

        stock = StockItem.objects.create(part=self.part, quantity=1)
        JobKitAllocation.objects.create(
            line=self.line,
            stock_item=stock,
            quantity=1,
            status='consumed',
            idempotency_key='r8-historical-consumed',
            reserved_by=self.actor,
        )
        self.line.selected_part = Part.objects.create(
            name='R8 different required Part', purchaseable=True
        )
        self.line.save(update_fields=['selected_part'])
        row = self.context()['lines'][0]
        self.assertEqual(Decimal(row['used_quantity']), 0)
        self.assertEqual(Decimal(row['outstanding_quantity']), 1)

    def test_machine_reassignment_does_not_transfer_retained_po_evidence(self):
        """Current access to the new machine cannot launder old scoped bindings."""
        from assets.models import AssetMachine

        self.select()
        self.place_and_link()
        self.document()
        other = AssetMachine.objects.create(
            name='R8 newly assigned fixture machine', client=self.tenant
        )
        self.work_order.machine = other
        self.work_order.save(update_fields=['machine'])
        row = self.context()['lines'][0]
        self.assertIsNone(row['source_selection'])
        self.assertEqual(row['orders'], [])
