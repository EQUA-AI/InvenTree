"""R8 P1 synthetic integration tests. No supplier, sender or shared database."""

import copy
import hashlib
import io
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings

from pypdf import PdfWriter
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from company.models import Company, SupplierPart
from common.models import Attachment
from order.models import PurchaseOrder, PurchaseOrderExtraLine, PurchaseOrderLineItem
from stock.models import StockItem
from tasks.jobkit_models import JobKitAllocation, JobKitShortage
from tasks.materials_readout import material_readout
from tasks.models import WorkOrderCommand, WorkOrderEvent
from tasks.procurement_api import ProcurementAcknowledgment, ProcurementSourceSelection
from tasks.services.procurement import (
    EMAIL_RECORDED, PO_ACKNOWLEDGED, RECEIPT_RECORDED, SOURCE_SELECTED,
    ProcurementError, ProcurementStale, acknowledge_order,
    configuration_revision, select_source,
)
from tasks.services.work_orders import IdempotencyConflict
from tasks.tests.closeout_fixtures import CloseoutEnvMixin
from tasks.workorder_serializers import WorkOrderEventSerializer

FLAGS = {'AIMMS_WORK_ORDERS_ENABLED': True, 'AIMMS_JOB_KITS_ENABLED': True}


class ProcurementFlagTests(SimpleTestCase):
    """The environment flag reaches the actual Django settings bridge."""

    def test_registry_and_real_environment_reader(self):
        """A bool registry entry uses the same reader as the boot bridge."""
        from django.conf import settings
        from aimms_flags import django_flags
        from InvenTree.config import get_boolean_setting

        entry = next(entry for entry in django_flags() if entry.env_name == 'AIMMS_JOB_KITS_ENABLED')
        self.assertFalse(entry.default)
        self.assertEqual(entry.kind, 'bool')
        self.assertIsInstance(settings.AIMMS_JOB_KITS_ENABLED, bool)
        for value, expected in [('true', True), ('false', False)]:
            with patch.dict('os.environ', {entry.env_name: value}):
                self.assertIs(get_boolean_setting(entry.env_name, entry.config_key, entry.default), expected)


@override_settings(**FLAGS)
class ProcurementTests(CloseoutEnvMixin, TestCase):
    """Real Work/kit/native PO records, original synthetic PDFs and commands."""

    def setUp(self):
        """Isolated fixture Work is deliberately distinct from the showcase."""
        self.build_env(username='r8-p1')
        self.work_order.tags = ['r8-fixture:p1-tests']
        self.work_order.save(update_fields=['tags'])
        self.line = self.build_kit_line(quantity='1')
        self.part = self.line.selected_part
        self.part.purchaseable = True
        self.part.units = 'ea'
        self.part.save(update_fields=['purchaseable', 'units'])
        self.supplier = Company.objects.create(name='P1 fixture supplier', is_supplier=True)
        self.supplier_part = SupplierPart.objects.create(part=self.part, supplier=self.supplier, SKU='FIXTURE-ONLY', pack_quantity='1', packaging='kit')
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        stream = io.BytesIO()
        writer.write(stream)
        self.pdf = stream.getvalue()
        self.attachment = Attachment.objects.create(model_type='workorder', model_id=self.work_order.pk, attachment=ContentFile(self.pdf, name='p1-fixture.pdf'))
        self.addCleanup(self.attachment.attachment.delete, save=False)
        self.ref = {'attachment_id': self.attachment.pk, 'sha256': hashlib.sha256(self.pdf).hexdigest(), 'page': 1, 'revision': 'fixture-1'}
        self.provenance = {'mode': 'recorded_fixture', 'method': 'recorded_document', 'reference': 'P1 synthetic evidence', 'fixture_namespace': 'p1-tests'}

    def source_payload(self, key='source-1'):
        """Two fully comparable fixture offers and a sourced deadline."""
        self.line.refresh_from_db()
        offer = {
            'key': 'offer-a', 'supplier_part_id': self.supplier_part.pk,
            'quote_reference': 'P1-FIXTURE-A', 'revision': '1',
            'order_quantity': '1', 'order_uom': 'kit', 'base_units_per_order_unit': '1',
            'unit_price': '620', 'discount': '0', 'freight': '45', 'tax': '0',
            'other_charges': '0', 'delivered_total': '665', 'currency': 'USD',
            'terms': 'Fixture only; net 30', 'promised_at': '2026-10-16T14:00:00Z',
            'promise_basis': 'supplier_quote', 'valid_through': '2026-10-16T15:00:00Z',
            'technical_eligibility': 'supported', 'source': copy.deepcopy(self.ref),
        }
        alternative = {**copy.deepcopy(offer), 'key': 'offer-b', 'quote_reference': 'P1-FIXTURE-B', 'unit_price': '560', 'freight': '20', 'delivered_total': '580', 'promised_at': '2026-10-19T14:00:00Z'}
        return {
            'operation_key': key, 'expected_version': self.work_order.lifecycle_version,
            'kit_line_id': self.line.pk, 'source_at': '2026-10-15T15:00:00Z',
            'provenance': copy.deepcopy(self.provenance), 'requirement_version': self.line.version,
            'configuration_revision': configuration_revision(self.work_order, self.line),
            'needed_at': '2026-10-16T17:00:00Z', 'deadline_source': copy.deepcopy(self.ref),
            'dependent_step': 'Fixture seal replacement', 'technical_evidence': [copy.deepcopy(self.ref)],
            'offers': [offer, alternative], 'selected_offer_key': 'offer-a',
            'reason': 'Fixture selected offer meets the evidenced deadline', 'supersedes_event_id': None,
        }

    def select(self, payload=None):
        """Invoke the real transactional evidence service."""
        return select_source(work_order_id=self.work_order.pk, actor=self.actor, **(payload or self.source_payload()))

    def native_order(self):
        """Native fixture PO with one goods and one freight line."""
        self.po = PurchaseOrder.objects.create(supplier=self.supplier, reference=f'P1-{self.work_order.pk}', order_currency='USD')
        self.po_line = PurchaseOrderLineItem.objects.create(order=self.po, part=self.supplier_part, quantity=1, purchase_price=620, purchase_price_currency='USD')
        PurchaseOrderExtraLine.objects.create(order=self.po, reference='Fixture freight', quantity=1, price=45, price_currency='USD')
        self.shortage = JobKitShortage.objects.create(line=self.line, quantity=1, purchase_order_line=self.po_line)
        return self.po_line

    def ack_payload(self, key='ack-1'):
        """An original acknowledgment identifies the affected native line."""
        return {'operation_key': key, 'expected_version': self.work_order.lifecycle_version,
                'kit_line_id': self.line.pk, 'source_at': '2026-10-15T15:35:00Z',
                'provenance': copy.deepcopy(self.provenance), 'purchase_order_line_id': self.po_line.pk,
                'supplier_reference': 'P1-FIXTURE-ACK', 'accepted_quantity': '1',
                'terms': 'Fixture net 30, freight included', 'promised_at': '2026-10-16T14:00:00Z',
                'source': copy.deepcopy(self.ref), 'deviations': []}

    def ack(self, payload=None):
        """Record real evidence without inferring supplier confirmation."""
        return acknowledge_order(work_order_id=self.work_order.pk, actor=self.actor, **(payload or self.ack_payload()))

    def context(self, actor=None):
        """Use the same shared Materials composition as the UI lanes."""
        self.work_order.refresh_from_db()
        return material_readout(actor or self.actor, self.work_order)['procurement']

    def post(self, view, body, actor=None):
        """Exercise scoped DRF command behavior."""
        request = APIRequestFactory().post('/', body, format='json')
        force_authenticate(request, user=actor or self.actor)
        return view.as_view()(request, pk=self.work_order.pk)

    def test_source_replay_returns_the_original_event_after_versions_change(self):
        """Commit once; retries still return the original identity, never another event."""
        payload = self.source_payload()
        first = self.select(payload)
        self.work_order.lifecycle_version += 1
        self.work_order.save(update_fields=['lifecycle_version'])
        self.line.version += 1
        self.line.save(update_fields=['version'])
        replay = self.select(payload)
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.filter(event_type=SOURCE_SELECTED).count(), 1)
        self.assertEqual(first.metadata['offers'][0]['supplier_sku'], 'FIXTURE-ONLY')
        self.assertEqual(first.metadata['actor']['pk'], self.actor.pk)
        self.assertEqual(first.metadata['approval_policy'], 'named_approver_per_po')
        self.assertNotEqual(first.metadata['source_at'], first.created_at.isoformat())

    def test_key_collision_with_different_payload_is_rejected(self):
        """Money/scope cannot change beneath the same durable operation key."""
        payload = self.source_payload()
        self.select(payload)
        payload['reason'] = 'Changed request'
        with self.assertRaises(IdempotencyConflict):
            self.select(payload)
        self.assertEqual(WorkOrderCommand.objects.count(), 1)

    def test_supersession_is_append_only_and_requires_latest_identity(self):
        """A revised source retains the original snapshot and explicit chain."""
        first = self.select()
        original = copy.deepcopy(first.metadata)
        changed = self.source_payload('source-2')
        with self.assertRaises(ProcurementStale):
            self.select(changed)
        changed['supersedes_event_id'] = first.pk
        changed['selected_offer_key'] = 'offer-b'
        second = self.select(changed)
        first.refresh_from_db()
        self.assertEqual(first.metadata, original)
        self.assertEqual(second.metadata['supersedes_event_id'], first.pk)
        self.assertEqual(self.context()['lines'][0]['source_selection']['event_id'], second.pk)

    def test_requirement_and_machine_drift_invalidate_the_selection(self):
        """Configuration edits cannot keep a historical selection current."""
        self.select()
        self.assertTrue(self.context()['lines'][0]['selection_current'])
        self.machine.model = 'Changed configuration'
        self.machine.save(update_fields=['model'])
        row = self.context()['lines'][0]
        self.assertFalse(row['selection_current'])
        self.assertIsNone(row['needed_at'])
        self.assertIsNotNone(row['source_selection'])

    def test_stale_requirement_is_rejected_before_evidence_append(self):
        """The line version and configuration fingerprint are both enforced."""
        for field, value in [('requirement_version', 99), ('configuration_revision', '0' * 64)]:
            with self.subTest(field=field):
                payload = self.source_payload()
                payload[field] = value
                with self.assertRaises(ProcurementStale):
                    self.select(payload)
        self.assertEqual(WorkOrderCommand.objects.count(), 0)

    def test_pdf_hash_owner_and_page_are_verified(self):
        """A syntactically plausible reference is insufficient evidence."""
        for field, value in [('sha256', '0' * 64), ('page', 2), ('attachment_id', self.attachment.pk + 1000)]:
            with self.subTest(field=field):
                payload = self.source_payload()
                payload['offers'][0]['source'][field] = value
                with self.assertRaises(ProcurementError):
                    self.select(payload)
        self.assertEqual(WorkOrderEvent.objects.count(), 0)

    def test_foreign_work_pdf_is_rejected(self):
        """Shared Work must not become a cross-machine evidence conduit."""
        self.attachment.model_id += 1000
        self.attachment.save(update_fields=['model_id'])
        with self.assertRaises(ProcurementError):
            self.select()

    def test_complete_cost_arithmetic_and_unknowns(self):
        """Known zero remains zero; an incomplete quote never gains a fake total."""
        payload = self.source_payload()
        payload['offers'][0]['delivered_total'] = '620'
        with self.assertRaises(ValidationError):
            self.select(payload)
        payload['offers'][0]['freight'] = None
        payload['offers'][0]['delivered_total'] = None
        event = self.select(payload)
        offer = event.metadata['offers'][0]
        self.assertIsNone(offer['freight'])
        self.assertIsNone(offer['delivered_total'])
        self.assertEqual(Decimal(offer['tax']), 0)

    def test_supplier_part_and_pack_must_match_requirement(self):
        """No internal-Part-as-SupplierPart error or silent UOM conversion."""
        payload = self.source_payload()
        payload['offers'][0]['base_units_per_order_unit'] = '2'
        with self.assertRaises(ProcurementError):
            self.select(payload)
        self.supplier_part.active = False
        self.supplier_part.save(update_fields=['active'])
        with self.assertRaises(ProcurementError):
            self.select()

    def test_fixtures_cannot_bind_to_the_real_showcase(self):
        """WO-003826 stays honest even if someone mistakenly tags it a fixture."""
        self.work_order.reference = 'WO-003826'
        self.work_order.save(update_fields=['reference'])
        with self.assertRaises(ProcurementError):
            self.select()
        self.work_order.reference = 'OTHER-WORK'
        self.work_order.tags = []
        self.work_order.save(update_fields=['reference', 'tags'])
        with self.assertRaises(ProcurementError):
            self.select()

    def test_placed_and_generic_reply_do_not_create_acknowledgment_or_email(self):
        """Native status and unrelated reply are independent from acceptance."""
        self.native_order()
        self.po.issue_date = '2026-10-15'
        self.po.status = 20
        self.po.save()
        WorkOrderEvent.objects.create(work_order=self.work_order, event_type='SUPPLIER_REPLY', correlation_id=uuid.uuid4(), metadata={'text': 'Thanks'})
        order = self.context()['lines'][0]['orders'][0]
        self.assertIsNotNone(order['placed'])
        self.assertIsNone(order['acknowledgment'])
        self.assertEqual(order['email_status'], 'not_recorded')
        self.assertIsNone(order['recorded_email'])

    def test_native_totals_include_freight_once_and_preserve_zero(self):
        """Read live native monetary rows, including discounts and fractional qty."""
        self.native_order()
        order = self.context()['lines'][0]['orders'][0]
        self.assertEqual(Decimal(order['goods_total']), 620)
        self.assertEqual(Decimal(order['charges_total']), 45)
        self.assertEqual(Decimal(order['order_total']), 665)
        self.po_line.quantity = Decimal('0.5')
        self.po_line.discount = Decimal('10')
        self.po_line.save()
        self.assertEqual(Decimal(self.context()['lines'][0]['orders'][0]['order_total']), 324)
        self.po_line.purchase_price = 0
        self.po_line.save()
        self.assertEqual(Decimal(self.context()['lines'][0]['orders'][0]['goods_total']), 0)
        self.po_line.purchase_price = None
        self.po_line.save()
        self.assertIsNone(self.context()['lines'][0]['orders'][0]['order_total'])

    def test_ack_replay_preserves_source_time_and_exact_native_binding(self):
        """Repeated acceptance has one event, with source and ingestion times distinct."""
        self.native_order()
        payload = self.ack_payload()
        first = self.ack(payload)
        self.assertEqual(first.pk, self.ack(payload).pk)
        ack = self.context()['lines'][0]['orders'][0]['acknowledgment']
        self.assertEqual(ack['event_id'], first.pk)
        self.assertEqual(ack['snapshot']['purchase_order_id'], self.po.pk)
        self.assertEqual(ack['snapshot']['purchase_order_line_id'], self.po_line.pk)
        self.assertNotEqual(ack['source_at'], ack['recorded_at'])
        self.assertEqual(WorkOrderEvent.objects.filter(event_type=PO_ACKNOWLEDGED).count(), 1)
        self.assertEqual(self.po.supplier_reference, '')

    def test_ack_rejects_wrong_line_excess_quantity_and_generic_reply(self):
        """All actual acceptance fields are mandatory; identity is not fuzzy."""
        self.native_order()
        payload = self.ack_payload()
        payload['accepted_quantity'] = '2'
        with self.assertRaises(ProcurementError):
            self.ack(payload)
        other = PurchaseOrderLineItem.objects.create(order=self.po, part=self.supplier_part, quantity=1)
        payload = self.ack_payload()
        payload['purchase_order_line_id'] = other.pk
        with self.assertRaises(ProcurementError):
            self.ack(payload)
        response = self.post(ProcurementAcknowledgment, {'reply': 'Thanks'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkOrderCommand.objects.count(), 0)

    def test_changed_native_line_makes_previous_acceptance_stale(self):
        """A later quantity edit cannot inherit earlier supplier acceptance."""
        self.native_order()
        self.ack()
        self.assertTrue(self.context()['lines'][0]['orders'][0]['acknowledgment_current'])
        self.po_line.quantity = 2
        self.po_line.save()
        self.assertFalse(self.context()['lines'][0]['orders'][0]['acknowledgment_current'])

    def test_receipt_projection_requires_exact_line_and_stock_role(self):
        """Same Part/PO stock is not an association; immutable receipt snapshot is."""
        self.native_order()
        metadata = {'schema_version': 'r8.1', 'machine_id': self.machine.pk, 'machine_client_id': self.machine.client_id,
                    'kit_line_id': self.line.pk, 'purchase_order_id': self.po.pk,
                    'purchase_order_line_id': self.po_line.pk,
                    'source_at': '2026-10-16T14:05:00Z',
                    'stocks': [{'stock_item': StockItem.objects.first().pk, 'quantity': '1', 'batch': 'FIXTURE-BATCH', 'location': None, 'status': 10, 'status_custom_key': None, 'expiry_date': None}]}
        event = WorkOrderEvent.objects.create(work_order=self.work_order, event_type=RECEIPT_RECORDED, correlation_id=uuid.uuid4(), idempotency_key='receipt-fixture', metadata=metadata)
        self.assertEqual(len(self.context()['lines'][0]['receipts']), 1)
        reader = self.make_scoped_user('po-reader')
        from django.contrib.auth.models import Group
        from users.models import RuleSet
        group = Group.objects.create(name='P1 PO readers')
        RuleSet.objects.update_or_create(group=group, name='purchase_order', defaults={'can_view': True})
        reader.groups.add(group)
        self.assertEqual(self.context(reader)['lines'][0]['receipts'], [])
        request = APIRequestFactory().get('/')
        request.user = reader
        self.assertEqual(WorkOrderEventSerializer(event, context={'request': request}).data['metadata'], {'hidden': True})
        event.metadata['purchase_order_line_id'] += 1000
        event.save(update_fields=['metadata'])
        self.assertEqual(self.context()['lines'][0]['receipts'], [])

    def test_purchasing_and_foreign_machine_evidence_are_redacted_everywhere(self):
        """Generic event serializers cannot bypass the procurement read boundary."""
        event = self.select()
        self.native_order()
        reader = self.make_scoped_user('shared-reader')
        body = material_readout(reader, self.work_order)
        self.assertFalse(body['procurement']['purchasing_visible'])
        self.assertEqual(body['procurement']['lines'], [])
        self.assertIsNone(body['requirements'][0]['shortages'][0]['purchase_order_line'])
        request = APIRequestFactory().get('/')
        request.user = reader
        self.assertEqual(WorkOrderEventSerializer(event, context={'request': request}).data['metadata'], {'hidden': True})
        self.actor.maintenance_scopes = set()
        self.assertEqual(self.context()['lines'], [])
        request.user = self.actor
        self.assertEqual(WorkOrderEventSerializer(event, context={'request': request}).data['metadata'], {'hidden': True})

    def test_read_is_effect_free_and_custody_buckets_are_disjoint(self):
        """Repeated refresh never reserves; consumed fulfillment is not a shortage."""
        self.select()
        stock = StockItem.objects.filter(part=self.part).first()
        allocation = JobKitAllocation.objects.create(line=self.line, stock_item=stock, quantity=1, status='consumed', reserved_by=self.actor, idempotency_key='fixture-consume')
        counts = (WorkOrderEvent.objects.count(), WorkOrderCommand.objects.count(), JobKitAllocation.objects.count(), StockItem.objects.count())
        for _ in range(2):
            row = self.context()['lines'][0]
            self.assertEqual(Decimal(row['used_quantity']), 1)
            self.assertEqual(Decimal(row['reserved_quantity']), 0)
            self.assertEqual(Decimal(row['outstanding_quantity']), 0)
        self.assertEqual(counts, (WorkOrderEvent.objects.count(), WorkOrderCommand.objects.count(), JobKitAllocation.objects.count(), StockItem.objects.count()))
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, 'consumed')

    def test_no_added_command_or_read_sends_email_or_changes_native_business_facts(self):
        """SMTP and Django mail are forbidden effects, including on replay."""
        self.native_order()
        counts = (PurchaseOrder.objects.count(), PurchaseOrderLineItem.objects.count(), StockItem.objects.count(), JobKitAllocation.objects.count())
        with patch('smtplib.SMTP', side_effect=AssertionError('SMTP forbidden')), patch('django.core.mail.message.EmailMessage.send', side_effect=AssertionError('mail forbidden')):
            payload = self.source_payload()
            self.select(payload)
            self.select(payload)
            ack = self.ack_payload()
            self.ack(ack)
            self.ack(ack)
            self.context()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(counts, (PurchaseOrder.objects.count(), PurchaseOrderLineItem.objects.count(), StockItem.objects.count(), JobKitAllocation.objects.count()))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 10)
        self.po_line.refresh_from_db()
        self.assertEqual(self.po_line.received, 0)

    def test_api_flags_roles_replay_and_strict_shape(self):
        """The mounted contract uses stable results; typos and actor injection fail."""
        payload = self.source_payload()
        first = self.post(ProcurementSourceSelection, payload)
        self.assertEqual(first.status_code, 200, first.data)
        replay = self.post(ProcurementSourceSelection, payload)
        self.assertEqual(first.data, replay.data)
        self.assertEqual(first['Cache-Control'], 'no-store')
        self.assertEqual(self.post(ProcurementSourceSelection, {**payload, 'actor': 123}).status_code, 400)
        from django.http import Http404
        with self.settings(AIMMS_JOB_KITS_ENABLED=False), self.assertRaises(Http404):
            self.post(ProcurementSourceSelection, payload)
        reader = self.make_scoped_user('command-reader')
        self.assertEqual(self.post(ProcurementSourceSelection, payload, actor=reader).status_code, 403)

    def test_timezone_is_required_and_unknown_provenance_is_rejected(self):
        """Historical source time is never interpreted in a guessed server zone."""
        for at in ['2026-10-15', '2026-10-15T10:00:00']:
            payload = self.source_payload()
            payload['source_at'] = at
            self.assertEqual(self.post(ProcurementSourceSelection, payload).status_code, 400)
        payload = self.source_payload()
        payload['provenance']['fixture_namespace'] = None
        self.assertEqual(self.post(ProcurementSourceSelection, payload).status_code, 400)

    def test_event_names_fit_native_limit(self):
        """Cross-lane event identities remain legal native Work event values."""
        from tasks.services.procurement import PROCUREMENT_EVENT_TYPES
        self.assertTrue(all(len(value) <= 40 for value in PROCUREMENT_EVENT_TYPES))
        self.assertIn(EMAIL_RECORDED, PROCUREMENT_EVENT_TYPES)

    def test_recorded_email_is_separate_from_acknowledgment(self):
        """A recorded document fact never implies transport or supplier acceptance."""
        self.native_order()
        WorkOrderEvent.objects.create(
            work_order=self.work_order, event_type=EMAIL_RECORDED,
            correlation_id=uuid.uuid4(), idempotency_key='fixture-email',
            metadata={
                'schema_version': 'r8.1', 'machine_id': self.machine.pk, 'machine_client_id': self.machine.client_id,
                'kit_line_id': self.line.pk, 'purchase_order_id': self.po.pk,
                'purchase_order_line_id': self.po_line.pk,
                'source_at': '2026-10-15T15:12:00Z',
                'provenance': self.provenance,
            },
        )
        order = self.context()['lines'][0]['orders'][0]
        self.assertEqual(order['email_status'], 'recorded_only')
        self.assertEqual(order['recorded_email']['operation_key'], 'fixture-email')
        self.assertIsNone(order['acknowledgment'])
        self.assertIsNone(order['placed'])

    def test_receipt_and_po_history_survive_shortage_resolution(self):
        """A durable exact receipt link survives removal of its shortage row."""
        self.native_order()
        event = self.ack()
        self.shortage.delete()
        row = self.context()['lines'][0]
        self.assertEqual(row['orders'][0]['purchase_order_line_id'], self.po_line.pk)
        self.assertEqual(row['orders'][0]['acknowledgment']['event_id'], event.pk)

    def test_command_ledger_uniqueness_prevents_a_second_commit(self):
        """The database protects the operation identity, not an indexed event key."""
        from django.db import IntegrityError, transaction

        self.select()
        command = WorkOrderCommand.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkOrderCommand.objects.create(
                work_order=self.work_order, command=command.command,
                idempotency_key=command.idempotency_key,
                correlation_id=uuid.uuid4(), request_hash=command.request_hash,
                status='succeeded', result_ref=command.result_ref,
            )
        self.assertEqual(WorkOrderCommand.objects.count(), 1)

    def test_stale_acknowledgment_does_not_confirm_changed_freight(self):
        """Acceptance is bound to the complete native commercial baseline."""
        self.native_order()
        self.ack()
        extra = self.po.extra_lines.get()
        extra.price = Decimal('55')
        extra.save()
        self.assertFalse(self.context()['lines'][0]['orders'][0]['acknowledgment_current'])

    def test_missing_currency_conversion_is_unknown(self):
        """A missing FX rate cannot turn a commercial cost into zero."""
        from djmoney.contrib.exchange.exceptions import MissingRate

        self.native_order()
        with patch('tasks.procurement_readout.convert_money', side_effect=MissingRate):
            order = self.context()['lines'][0]['orders'][0]
        self.assertIsNone(order['order_total'])

    def test_requirement_drift_preserves_historical_decision(self):
        """A quantity change invalidates the decision without editing its evidence."""
        event = self.select()
        original = copy.deepcopy(event.metadata)
        self.line.required_quantity = Decimal('2')
        self.line.version += 1
        self.line.save(update_fields=['required_quantity', 'version'])
        row = self.context()['lines'][0]
        self.assertFalse(row['selection_current'])
        self.assertEqual(row['source_selection']['snapshot'], original)

    def test_native_routes_are_registered(self):
        """UI paths resolve through the real tasks URL configuration."""
        from django.urls import resolve

        for suffix, view in [('source-selection', ProcurementSourceSelection), ('acknowledgment', ProcurementAcknowledgment)]:
            match = resolve(f'/api/tasks/work-orders/{self.work_order.pk}/procurement/{suffix}/')
            self.assertEqual(match.func.view_class, view)

    def test_order_uom_is_sourced_and_packaging_is_not_a_unit(self):
        """Native packaging labels must not be invented order-unit facts."""
        self.native_order()
        self.supplier_part.packaging = 'Plastic bag'
        self.supplier_part.save(update_fields=['packaging'])
        order = self.context()['lines'][0]['orders'][0]
        self.assertIsNone(order['order_uom'])
        self.assertEqual(order['packaging'], 'Plastic bag')
        self.select()
        order = self.context()['lines'][0]['orders'][0]
        self.assertEqual(order['order_uom'], 'kit')
        self.assertEqual(Decimal(order['base_units_per_order_unit']), 1)

    def test_api_replay_requires_current_machine_scope(self):
        """Previously authorized evidence does not bypass later scope revocation."""
        payload = self.source_payload()
        self.select(payload)
        self.actor.maintenance_scopes = set()
        self.assertEqual(self.post(ProcurementSourceSelection, payload).status_code, 404)

    def test_source_and_ingestion_times_have_explicit_offsets(self):
        """The wire contract is zoned even with USE_TZ=False in offline tests."""
        from django.utils.dateparse import parse_datetime
        from django.utils.timezone import is_aware

        response = self.post(ProcurementSourceSelection, self.source_payload())
        self.assertEqual(response.status_code, 200, response.data)
        for field in ('source_at', 'recorded_at'):
            self.assertTrue(is_aware(parse_datetime(response.data[field])))

    def test_work_move_cannot_replay_foreign_machine_evidence(self):
        """Command replay follows the same historical-scope boundary as reads."""
        from assets.models import AssetMachine

        payload = self.source_payload()
        self.select(payload)
        replacement = AssetMachine.objects.create(name='P1 replacement fixture machine', client=self.tenant)
        self.work_order.machine = replacement
        self.work_order.save(update_fields=['machine'])
        response = self.post(ProcurementSourceSelection, payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'PROCUREMENT_STALE')
        self.assertIsNone(self.context()['lines'][0]['source_selection'])

    def test_client_move_hides_history_and_starts_a_new_source_chain(self):
        """A new tenant cannot inherit or replay the old tenant's source evidence."""
        from assets.models import Client
        from tasks.scope import MaintenanceScope

        payload = self.source_payload()
        self.select(payload)
        tenant = Client.objects.create(code='p1-new-tenant', name='P1 new fixture tenant')
        self.machine.client = tenant
        self.machine.save(update_fields=['client'])
        self.actor.maintenance_scopes = {MaintenanceScope(customer_id=None, site_key=None, client_id=tenant.pk)}
        row = self.context()['lines'][0]
        self.assertIsNone(row['source_selection'])
        response = self.post(ProcurementSourceSelection, payload)
        self.assertEqual(response.status_code, 409)
        changed = self.source_payload('source-in-new-tenant')
        changed['configuration_revision'] = row['configuration_revision']
        event = self.select(changed)
        self.assertEqual(event.metadata['machine_client_id'], tenant.pk)
        self.assertIsNone(event.metadata['supersedes_event_id'])
