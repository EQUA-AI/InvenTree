"""Local fixture integration coverage for receipt replay and exact reservation."""

from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from assets.models import AssetMachine, Client
from company.models import Company, SupplierPart
from InvenTree.helpers import current_date
from order.models import PurchaseOrder, PurchaseOrderLineItem
from order.status_codes import PurchaseOrderStatus
from part.models import Part
from stock.models import StockItem, StockLocation
from stock.status_codes import StockStatus
from tasks.jobkit_serializers import JobKitSerializer
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitLine,
    JobKitShortage,
    MaintenanceReceipt,
    MaintenanceReceiptStock,
    WorkOrder,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kits import (
    reconcile_job_kit,
    release_allocation,
    reserve_job_kit,
)
from tasks.services.maintenance_receipts import continue_receipt
from tasks.services.stock_allocation import StockOverAllocation, mutate_stock_allocation
from tasks.services.work_orders import IdempotencyConflict
from users.models import RuleSet


@override_settings(AIMMS_JOB_KITS_ENABLED=True, AIMMS_WORK_ORDERS_ENABLED=True)
class MaintenanceReceiptTests(TestCase):
    """Real native receive HTTP action, with no real suppliers or showcase data."""

    def setUp(self):
        """Create isolated fixture identities and a placed one-line PO."""
        self.actor = get_user_model().objects.create_superuser(
            username='receipt-fixture', password='fixture'
        )
        self.client_scope = Client.objects.create(
            code='RECEIPT-FIXTURE', name='Receipt fixture'
        )
        self.machine = AssetMachine.objects.create(
            name='Receipt fixture machine', client=self.client_scope
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_scope.pk
            )
        }
        self.customer = Company.objects.create(
            name='Fixture customer', is_customer=True
        )
        self.work = WorkOrder.objects.create(
            title='Fixture receipt Work', machine=self.machine, customer=self.customer
        )
        self.kit = JobKit.objects.create(
            work_order=self.work, created_by=self.actor, status='short'
        )
        self.part = Part.objects.create(
            name='Fixture seal', component=True, purchaseable=True
        )
        self.line = JobKitLine.objects.create(
            kit=self.kit,
            sequence=1,
            kind='part',
            requested_part=self.part,
            selected_part=self.part,
            required_quantity=Decimal('1'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
            source='manual',
        )
        self.supplier = Company.objects.create(
            name='Fixture supplier', is_supplier=True
        )
        self.supplier_part = SupplierPart.objects.create(
            supplier=self.supplier, part=self.part, SKU='FIXTURE'
        )
        self.location = StockLocation.objects.create(name='Fixture stores')
        self.po = PurchaseOrder.objects.create(
            reference='FIXTURE-1',
            supplier=self.supplier,
            status=PurchaseOrderStatus.PLACED.value,
        )
        self.po_line = PurchaseOrderLineItem.objects.create(
            order=self.po, part=self.supplier_part, quantity=1
        )
        self.shortage = JobKitShortage.objects.create(
            line=self.line,
            quantity=1,
            status='ordered',
            purchase_order_line=self.po_line,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = f'/api/order/po/{self.po.pk}/receive/'
        self.body = {
            'maintenance_operation_key': 'fixture-receipt-1',
            'maintenance_requirement_version': self.line.version,
            'location': self.location.pk,
            'items': [
                {
                    'line_item': self.po_line.pk,
                    'quantity': '1',
                    'status': StockStatus.OK.value,
                    'batch_code': 'FIXTURE-BATCH',
                }
            ],
        }

    def receive(self, body=None, execute=True):
        """Exercise the mounted native endpoint with controlled commit callbacks."""
        with self.captureOnCommitCallbacks(execute=execute):
            return self.api.post(self.url, body or self.body, format='json')

    def receipt(self):
        """Read the one durable receipt outcome."""
        return MaintenanceReceipt.objects.get()

    def test_native_receipt_replay_precedes_complete_po_and_returns_exact_snapshot(
        self,
    ):
        """Native receipt replay precedes complete po and returns exact snapshot."""
        with (
            patch('order.models.notify_responsible') as notify,
            patch('django.core.mail.message.EmailMessage.send') as send,
        ):
            first = self.receive()
            self.assertEqual(first.status_code, 201, first.data)
            self.po.refresh_from_db()
            self.assertEqual(self.po.status, PurchaseOrderStatus.COMPLETE.value)
            receipt = self.receipt()
            self.assertEqual(
                receipt.reservation_state, 'allocated', receipt.reservation_code
            )
            stock = StockItem.objects.get(pk=first.data[0]['pk'])
            allocation = JobKitAllocation.objects.get(line=self.line)
            self.assertEqual(allocation.stock_item_id, stock.pk)
            self.assertEqual(allocation.quantity, Decimal('1'))
            self.assertEqual(
                MaintenanceReceiptStock.objects.get().stock_item_id, stock.pk
            )
            self.shortage.refresh_from_db()
            self.kit.refresh_from_db()
            self.assertEqual(self.shortage.status, 'received')
            self.assertIsNotNone(self.shortage.resolved_at)
            self.assertEqual(self.shortage.purchase_order_line_id, self.po_line.pk)
            self.assertEqual(self.kit.status, 'ready')
            self.assertFalse(self.line.shortages.filter(status='open').exists())
            # The replay returns receipt-time fields even after later stock edits.
            StockItem.objects.filter(pk=stock.pk).update(batch='LATER-BATCH')
            replay = self.receive()
            self.assertEqual(replay.status_code, 201, replay.data)
            self.assertEqual(replay.data, first.data)
            self.assertEqual(StockItem.objects.count(), 1)
            self.assertEqual(
                self.work.events.filter(event_type='PROC_RECEIPT_RECORDED').count(), 1
            )
            self.assertEqual(
                self.work.events.filter(event_type='PROC_RECEIPT_ALLOCATED').count(), 1
            )
            self.assertEqual(JobKitAllocation.objects.count(), 1)
            notify.assert_not_called()
            send.assert_not_called()
            self.assertEqual(len(mail.outbox), 0)

    def test_no_reservation_before_commit_and_failed_continuation_is_recoverable(self):
        """No reservation before commit and failed continuation is recoverable."""
        first = self.receive(execute=False)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertFalse(JobKitAllocation.objects.exists())
        self.assertEqual(self.receipt().reservation_state, 'pending')
        with patch(
            'tasks.services.maintenance_receipts.mutate_stock_allocation',
            side_effect=RuntimeError('fixture failure'),
        ):
            continue_receipt(self.receipt().pk, self.actor)
        self.assertEqual(self.receipt().reservation_state, 'attention')
        self.assertEqual(StockItem.objects.count(), 1)
        replay = self.receive()
        self.assertEqual(replay.data, first.data)
        self.assertEqual(self.receipt().reservation_state, 'allocated')
        self.assertEqual(JobKitAllocation.objects.count(), 1)

    def test_changed_payload_same_key_conflicts_without_stock_effect(self):
        """Changed payload same key conflicts without stock effect."""
        self.assertEqual(self.receive().status_code, 201)
        changed = deepcopy(self.body)
        changed['items'][0]['batch_code'] = 'OTHER-FIXTURE'
        self.assertEqual(self.receive(changed).status_code, 409)
        self.assertEqual(StockItem.objects.count(), 1)

    def test_invalid_linked_inputs_have_no_stock_effect(self):
        """Invalid linked inputs have no stock effect."""
        for field, value, expected in [
            ('maintenance_operation_key', None, 400),
            ('maintenance_requirement_version', 999, 409),
            ('batch_code', '', 400),
            ('quantity', '2', 409),
            ('serial_numbers', 'FIXTURE-SERIAL', 409),
        ]:
            with self.subTest(field=field):
                body = deepcopy(self.body)
                if field in body:
                    if value is None:
                        del body[field]
                    else:
                        body[field] = value
                else:
                    body['items'][0][field] = value
                response = self.receive(body)
                self.assertEqual(response.status_code, expected, response.data)
                self.assertFalse(StockItem.objects.exists())
                self.assertFalse(MaintenanceReceipt.objects.exists())

    def test_multiline_or_mismatched_part_is_rejected(self):
        """Multiline or mismatched part is rejected."""
        self.po_line.part = SupplierPart.objects.create(
            supplier=self.supplier,
            part=Part.objects.create(name='Other fixture'),
            SKU='OTHER',
        )
        self.po_line.save()
        self.assertEqual(self.receive().status_code, 409)
        self.po_line.part = self.supplier_part
        self.po_line.save()
        PurchaseOrderLineItem.objects.create(
            order=self.po, part=self.supplier_part, quantity=1
        )
        self.assertEqual(self.receive().status_code, 400)
        self.assertFalse(StockItem.objects.exists())

    def test_unrelated_native_receipt_remains_available_without_key(self):
        """Unrelated native receipt remains available without key."""
        self.shortage.delete()
        body = deepcopy(self.body)
        del body['maintenance_operation_key']
        del body['maintenance_requirement_version']
        self.assertEqual(self.receive(body).status_code, 201)
        self.assertFalse(MaintenanceReceipt.objects.exists())
        self.assertFalse(JobKitAllocation.objects.exists())

    def test_pending_operation_blocks_fresh_key(self):
        """Pending operation blocks fresh key."""
        self.po_line.quantity = 2
        self.po_line.save()
        self.assertEqual(self.receive(execute=False).status_code, 201)
        body = deepcopy(self.body)
        body['maintenance_operation_key'] = 'new-key'
        self.assertEqual(self.receive(body).status_code, 409)
        self.assertEqual(StockItem.objects.count(), 1)

    def test_damaged_receipt_is_physical_stock_without_usable_reservation(self):
        """Damaged receipt is physical stock without usable reservation."""
        body = deepcopy(self.body)
        body['items'][0]['status'] = StockStatus.DAMAGED.value
        self.assertEqual(self.receive(body).status_code, 201)
        self.assertEqual(self.receipt().reservation_state, 'attention')
        self.assertFalse(JobKitAllocation.objects.exists())
        self.shortage.refresh_from_db()
        self.assertEqual(self.shortage.status, 'ordered')

    def test_requirement_change_after_receipt_stops_continuation(self):
        """Requirement change after receipt stops continuation."""
        self.assertEqual(self.receive(execute=False).status_code, 201)
        JobKitLine.objects.filter(pk=self.line.pk).update(version=2)
        self.assertEqual(self.receive().status_code, 201)
        self.assertEqual(self.receipt().reservation_state, 'attention')
        self.assertFalse(JobKitAllocation.objects.exists())
        self.assertEqual(StockItem.objects.count(), 1)

    def test_receipt_association_prevents_history_deletion(self):
        """Receipt association prevents history deletion."""
        self.assertEqual(self.receive().status_code, 201)
        with self.assertRaises(ProtectedError):
            StockItem.objects.get().delete()
        with self.assertRaises(ProtectedError):
            self.shortage.delete()

    def test_machine_scope_is_required_even_for_shared_work_and_replay(self):
        """Machine scope is required even for shared work and replay."""
        self.assertEqual(self.receive().status_code, 201)
        self.actor.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_scope.pk + 1
            )
        }
        self.assertEqual(self.receive().status_code, 403)
        self.assertEqual(StockItem.objects.count(), 1)

    def test_readback_is_readonly_and_requires_purchasing_and_stock_scope(self):
        """Readback is readonly and requires purchasing and stock scope."""
        self.assertEqual(self.receive(execute=False).status_code, 201)
        request = type('Request', (), {'user': self.actor})()
        body = JobKitSerializer(self.kit, context={'request': request}).data
        self.assertEqual(
            body['receipts'][0]['operation_key'], self.body['maintenance_operation_key']
        )
        self.assertEqual(body['receipts'][0]['reservation_state'], 'pending')
        self.assertFalse(JobKitAllocation.objects.exists())
        self.actor.maintenance_scopes = set()
        self.assertEqual(
            JobKitSerializer(self.kit, context={'request': request}).data['receipts'],
            [],
        )

    def test_role_authorization_does_not_follow_shared_work_visibility(self):
        """Role authorization does not follow shared work visibility."""
        user = get_user_model().objects.create_user(username='receipt-reader')
        user.maintenance_scopes = self.actor.maintenance_scopes
        group = Group.objects.create(name='Fixture work and stock readers')
        user.groups.add(group)
        for role in ['work_order', 'stock', 'purchase_order']:
            RuleSet.objects.update_or_create(
                group=group, name=role, defaults={'can_view': True}
            )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='tasks', codename='reserve_jobkit'
            )
        )
        self.api.force_authenticate(user)
        self.assertEqual(self.receive().status_code, 403)
        self.assertFalse(StockItem.objects.exists())

    def test_consumed_fulfillment_survives_repeat_reserve_reconcile_and_partial_release(
        self,
    ):
        """Consumed fulfillment survives repeat reserve reconcile and partial release."""
        stock = StockItem.objects.create(part=self.part, quantity=5)
        # Two units consumed already, and one unit still reserved against need=3.
        self.line.required_quantity = 3
        self.line.save()
        JobKitAllocation.objects.create(
            line=self.line,
            stock_item=stock,
            quantity=2,
            status='consumed',
            reserved_by=self.actor,
            idempotency_key='consumed',
        )
        active = JobKitAllocation.objects.create(
            line=self.line,
            stock_item=stock,
            quantity=1,
            reserved_by=self.actor,
            idempotency_key='active',
        )
        release_allocation(
            work_order_id=self.work.pk, allocation_id=active.pk, actor=self.actor
        )
        # Exact purchasing shortage remains; no duplicate open demand of 3.
        self.assertFalse(self.line.shortages.filter(status='open').exists())
        self.shortage.status = 'canceled'
        self.shortage.save()
        reserve_job_kit(
            work_order_id=self.work.pk,
            actor=self.actor,
            expected_version=self.work.lifecycle_version,
            idempotency_key='after-use',
        )
        self.assertEqual(
            self.line.allocations.get(status='reserved').quantity, Decimal('1')
        )
        reconcile_job_kit(work_order_id=self.work.pk, actor=self.actor)
        self.assertFalse(self.line.shortages.filter(status='open').exists())

    def test_unrelated_stock_does_not_mark_ordered_shortage_received(self):
        """Unrelated stock does not mark ordered shortage received."""
        StockItem.objects.create(part=self.part, quantity=1)
        reserve_job_kit(
            work_order_id=self.work.pk,
            actor=self.actor,
            expected_version=self.work.lifecycle_version,
            idempotency_key='other-stock',
        )
        self.shortage.refresh_from_db()
        self.kit.refresh_from_db()
        self.assertEqual(self.shortage.status, 'ordered')
        self.assertEqual(self.kit.status, 'short')
        self.assertFalse(self.line.shortages.filter(status='open').exists())

    def test_allocator_extension_replay_and_total_commitments(self):
        """Allocator extension replay and total commitments."""
        stock = StockItem.objects.create(part=self.part, quantity=5)

        def reserve(quantity, key):
            return mutate_stock_allocation(
                stock_item_id=stock.pk,
                line_id=self.line.pk,
                requested_quantity=quantity,
                actor=self.actor,
                idempotency_key=key,
            )

        reserve('2', 'initial')
        result = reserve('2', 'extension')
        self.assertEqual(reserve('2', 'extension').quantity, Decimal('4'))
        with self.assertRaises(StockOverAllocation):
            reserve('2', 'too-many')
        with self.assertRaises(IdempotencyConflict):
            reserve('1', 'extension')
        result.refresh_from_db()
        self.assertEqual(result.quantity, Decimal('4'))

    def test_usable_stock_excludes_attention_expiry_external_and_ownership(self):
        """Usable stock excludes attention expiry external and ownership."""
        from datetime import timedelta

        for field, value in [
            ('status', StockStatus.ATTENTION.value),
            ('status', StockStatus.RETURNED.value),
            ('status', StockStatus.QUARANTINED.value),
            ('expiry_date', current_date() - timedelta(days=1)),
        ]:
            with self.subTest(field=field, value=value):
                stock = StockItem.objects.create(
                    part=self.part, quantity=1, **{field: value}
                )
                with self.assertRaises(StockOverAllocation):
                    mutate_stock_allocation(
                        stock_item_id=stock.pk,
                        line_id=self.line.pk,
                        requested_quantity=1,
                        actor=self.actor,
                        idempotency_key=f'ineligible-{stock.pk}',
                    )
        external = StockLocation.objects.create(name='Fixture external', external=True)
        stock = StockItem.objects.create(part=self.part, quantity=1, location=external)
        with self.assertRaises(StockOverAllocation):
            mutate_stock_allocation(
                stock_item_id=stock.pk,
                line_id=self.line.pk,
                requested_quantity=1,
                actor=self.actor,
                idempotency_key='external',
            )

    def test_partial_receipts_preserve_exact_history_then_resolve_linked_shortage(self):
        """Two intentional partial operations receive and allocate exactly twice."""
        self.po_line.quantity = 2
        self.po_line.save()
        self.line.required_quantity = 2
        self.line.save()
        self.shortage.quantity = 2
        self.shortage.save()
        first = self.receive()
        self.assertEqual(first.status_code, 201, first.data)
        self.shortage.refresh_from_db()
        self.assertEqual(self.shortage.status, 'partial')
        second_body = deepcopy(self.body)
        second_body['maintenance_operation_key'] = 'fixture-receipt-2'
        second = self.receive(second_body)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data[0]['pk'], second.data[0]['pk'])
        self.shortage.refresh_from_db()
        self.kit.refresh_from_db()
        self.assertEqual(self.shortage.status, 'received')
        self.assertEqual(self.shortage.quantity, Decimal('2'))
        self.assertEqual(self.kit.status, 'ready')
        self.assertEqual(MaintenanceReceipt.objects.count(), 2)
        self.assertEqual(MaintenanceReceiptStock.objects.count(), 2)
        self.assertEqual(self.receive().data, first.data)
        self.assertEqual(self.receive(second_body).data, second.data)
        self.assertEqual(StockItem.objects.count(), 2)
        self.assertEqual(
            sum(self.line.allocations.values_list('quantity', flat=True)), Decimal('2')
        )

    def test_existing_competitor_blocks_receipt_stock_until_released(self):
        """A committed receipt cannot preempt another Job Kit's allocation."""
        self.assertEqual(self.receive(execute=False).status_code, 201)
        stock = StockItem.objects.get()
        other_work = WorkOrder.objects.create(
            title='Fixture competitor', machine=self.machine
        )
        other_kit = JobKit.objects.create(work_order=other_work, created_by=self.actor)
        other_line = JobKitLine.objects.create(
            kit=other_kit,
            sequence=1,
            kind='part',
            requested_part=self.part,
            selected_part=self.part,
            required_quantity=1,
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
            source='manual',
        )
        competing = mutate_stock_allocation(
            stock_item_id=stock.pk,
            line_id=other_line.pk,
            requested_quantity=1,
            actor=self.actor,
            idempotency_key='competing-fixture',
        )
        self.receive()
        self.assertEqual(self.receipt().reservation_state, 'attention')
        self.assertFalse(self.line.allocations.exists())
        release_allocation(
            work_order_id=other_work.pk, allocation_id=competing.pk, actor=self.actor
        )
        # Releasing the same physical reservation twice returns its first result.
        self.assertEqual(
            release_allocation(
                work_order_id=other_work.pk,
                allocation_id=competing.pk,
                actor=self.actor,
            ).pk,
            competing.pk,
        )
        self.receive()
        self.assertEqual(self.receipt().reservation_state, 'allocated')
        self.assertEqual(self.line.allocations.get().quantity, Decimal('1'))

    def test_custom_ok_status_is_usable_and_real_ownership_denies_nonowner(self):
        """Accepted logical custom statuses work while native ownership is enforced."""
        from django.contrib.contenttypes.models import ContentType

        from common.models import InvenTreeCustomUserStateModel, InvenTreeSetting
        from users.models import Owner

        state = InvenTreeCustomUserStateModel.objects.create(
            key=11,
            name='Fixture accepted',
            label='Fixture accepted',
            color='success',
            logical_key=StockStatus.OK.value,
            model=ContentType.objects.get_for_model(StockItem),
            reference_status='StockStatus',
        )
        stock = StockItem.objects.create(part=self.part, quantity=2)
        stock.set_status(state.key)
        stock.save()
        allowed = mutate_stock_allocation(
            stock_item_id=stock.pk,
            line_id=self.line.pk,
            requested_quantity=1,
            actor=self.actor,
            idempotency_key='custom-ok',
        )
        self.assertEqual(allowed.quantity, Decimal('1'))
        owner = get_user_model().objects.create_user(username='fixture-stock-owner')
        outsider = get_user_model().objects.create_user(
            username='fixture-stock-outsider'
        )
        stock.owner = Owner.get_owner(owner)
        stock.save()
        InvenTreeSetting.set_setting('STOCK_OWNERSHIP_CONTROL', True, self.actor)
        with self.assertRaises(StockOverAllocation):
            mutate_stock_allocation(
                stock_item_id=stock.pk,
                line_id=self.line.pk,
                requested_quantity=1,
                actor=outsider,
                idempotency_key='ownership-denied',
            )

    def test_receipt_events_redact_metadata_without_stock_and_po_authority(self):
        """The generic Work audit timeline respects receipt evidence boundaries."""
        from tasks.workorder_serializers import WorkOrderEventSerializer

        self.assertEqual(self.receive().status_code, 201)
        event = self.work.events.get(event_type='PROC_RECEIPT_RECORDED')
        request = type('Request', (), {'user': self.actor})()
        self.assertIn(
            'stocks',
            WorkOrderEventSerializer(event, context={'request': request}).data[
                'metadata'
            ],
        )
        self.actor.maintenance_scopes = set()
        self.assertEqual(
            WorkOrderEventSerializer(event, context={'request': request}).data[
                'metadata'
            ],
            {'receipt_visible': False},
        )

    def test_receipt_migration_matches_runtime_models(self):
        """The narrow new migration has the same fields as the runtime models."""
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        state = MigrationLoader(connection).project_state()
        for model in (MaintenanceReceipt, MaintenanceReceiptStock):
            historical = state.apps.get_model('tasks', model.__name__)
            for field in model._meta.local_fields:
                self.assertEqual(
                    field.deconstruct()[1:],
                    historical._meta.get_field(field.name).deconstruct()[1:],
                )

    def test_native_effect_and_receipt_ledger_rollback_together(self):
        """A ledger failure cannot leave unassociated stock or a received delta."""
        with patch(
            'tasks.services.maintenance_receipts.MaintenanceReceipt.objects.create',
            side_effect=RuntimeError('fixture ledger failure'),
        ):
            with self.assertRaises(RuntimeError):
                self.receive()
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.received, Decimal('0'))
        self.assertFalse(StockItem.objects.exists())
        self.assertFalse(
            self.work.events.filter(event_type='PROC_RECEIPT_RECORDED').exists()
        )
        self.assertEqual(self.receive().status_code, 201)
        self.assertEqual(StockItem.objects.count(), 1)

    def test_consumed_material_of_old_selected_part_does_not_fulfill_new_part(self):
        """A substitution retains old consumption without claiming the new need met."""
        old = StockItem.objects.create(part=self.part, quantity=1)
        JobKitAllocation.objects.create(
            line=self.line,
            stock_item=old,
            quantity=1,
            status='consumed',
            reserved_by=self.actor,
            idempotency_key='old-part',
        )
        self.shortage.status = 'canceled'
        self.shortage.save()
        self.line.selected_part = Part.objects.create(
            name='New fixture selection', component=True
        )
        self.line.save()
        kit = reserve_job_kit(
            work_order_id=self.work.pk,
            actor=self.actor,
            expected_version=self.work.lifecycle_version,
            idempotency_key='new-part',
        )
        self.assertEqual(kit.status, 'short')
        self.assertEqual(self.line.shortages.get(status='open').quantity, Decimal('1'))

    def test_scope_recheck_blocks_generic_reserve_replay_and_release(self):
        """Shared Work and a successful old command do not grant stock scope."""
        from tasks.services.work_orders import WorkOrderScopeError

        stock = StockItem.objects.create(part=self.part, quantity=1)
        reserve_job_kit(
            work_order_id=self.work.pk,
            actor=self.actor,
            expected_version=self.work.lifecycle_version,
            idempotency_key='scope-reserve',
        )
        self.actor.maintenance_scopes = set()
        with self.assertRaises(WorkOrderScopeError):
            reserve_job_kit(
                work_order_id=self.work.pk,
                actor=self.actor,
                expected_version=self.work.lifecycle_version,
                idempotency_key='scope-reserve',
            )
        allocation = self.line.allocations.get(stock_item=stock)
        with self.assertRaises(WorkOrderScopeError):
            release_allocation(
                work_order_id=self.work.pk,
                allocation_id=allocation.pk,
                actor=self.actor,
            )

    def test_readback_requires_po_role_and_can_recover_exact_intent(self):
        """A stock reader still needs purchasing authority for receipt evidence."""
        self.assertEqual(self.receive(execute=False).status_code, 201)
        request = type('Request', (), {'user': self.actor})()
        row = JobKitSerializer(self.kit, context={'request': request}).data['receipts'][
            0
        ]
        self.assertEqual(row['request'], self.body)
        self.assertEqual(self.receive(row['request']).status_code, 201)
        reader = get_user_model().objects.create_user(username='fixture-stock-only')
        reader.maintenance_scopes = self.actor.maintenance_scopes
        group = Group.objects.create(name='Fixture stock only')
        RuleSet.objects.update_or_create(
            group=group, name='stock', defaults={'can_view': True}
        )
        reader.groups.add(group)
        request.user = reader
        self.assertEqual(
            JobKitSerializer(self.kit, context={'request': request}).data['receipts'],
            [],
        )

    def test_expired_native_receipt_is_not_reserved(self):
        """Physical receipt does not imply usable acceptance of expired stock."""
        from datetime import timedelta

        body = deepcopy(self.body)
        body['items'][0]['expiry_date'] = (
            current_date() - timedelta(days=1)
        ).isoformat()
        self.assertEqual(self.receive(body).status_code, 201)
        self.assertEqual(self.receipt().reservation_state, 'attention')
        self.assertFalse(JobKitAllocation.objects.exists())

    def test_reconcile_preserves_staged_released_and_closed_history(self):
        """Supply reconciliation must not erase Kit custody lifecycle history."""
        from django.utils import timezone

        self.shortage.status = 'canceled'
        self.shortage.save()
        StockItem.objects.create(part=self.part, quantity=1)
        reserve_job_kit(
            work_order_id=self.work.pk,
            actor=self.actor,
            expected_version=self.work.lifecycle_version,
            idempotency_key='history-reserve',
        )
        recorded_at = timezone.now()
        for state in ('staged', 'released', 'closed', 'canceled'):
            with self.subTest(state=state):
                JobKit.objects.filter(pk=self.kit.pk).update(
                    status=state, staged_at=recorded_at, released_at=recorded_at
                )
                result = reconcile_job_kit(work_order_id=self.work.pk, actor=self.actor)
                self.assertEqual(result.status, state)
                self.assertEqual(result.staged_at, recorded_at)
                self.assertEqual(result.released_at, recorded_at)
                self.assertEqual(self.line.allocations.count(), 1)
