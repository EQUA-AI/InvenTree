"""R8 documents use real native fixture records and the actual PDF renderer."""

import base64
import hashlib
import io
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from django.urls import reverse

from asgiref.sync import async_to_sync
from pypdf import PdfReader
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from ai.core.integrations.email import commands
from ai.core.pdf.service import PDFService
from company.models import Company, SupplierPart
from order.models import (
    PurchaseOrder,
    PurchaseOrderDocumentEvidence,
    PurchaseOrderExtraLine,
    PurchaseOrderLineItem,
)
from order.procurement_evidence import (
    EvidenceConflict,
    evidence_result,
    record_documents,
)
from part.models import Part


class ProcurementEvidenceTests(TestCase):
    """Exact bytes, safe replay, explicit provenance and no transmission."""

    def setUp(self):
        """Only test fixtures are created; every sender is fenced by a mock."""
        self.actor = get_user_model().objects.create_superuser(
            username='R8-fixture-buyer', email='buyer@example.com', password='fixture'
        )
        self.supplier = Company.objects.create(
            name='R8 fixture supplier', is_supplier=True, email='supplier@example.com'
        )
        self.part = Part.objects.create(
            name='R8 fixture kit', active=True, purchaseable=True
        )
        self.sp = SupplierPart.objects.create(
            part=self.part, supplier=self.supplier, SKU='R8-fixture-SKU'
        )
        self.order = PurchaseOrder.objects.create(
            reference='R8-FIXTURE-PO', supplier=self.supplier, order_currency='USD'
        )
        self.line = PurchaseOrderLineItem.objects.create(
            order=self.order,
            part=self.sp,
            quantity='1.25',
            purchase_price='496',
            purchase_price_currency='USD',
        )
        self.freight = PurchaseOrderExtraLine.objects.create(
            order=self.order,
            description='Freight',
            quantity=1,
            price=45,
            price_currency='USD',
        )
        self.data = {
            'operation_key': 'r8-fixture-evidence-1',
            'to': 'supplier@example.com',
            'from_address': 'buyer@example.com',
            'ship_to': 'Fixture receiving street, Test City',
            'buyer_name': 'Named fixture buyer',
            'provenance_kind': 'fixture',
        }
        self.gmail = self.enterContext(
            patch('ai.core.integrations.email.commands.get_gmail_client')
        )
        self.send = self.enterContext(
            patch('ai.core.integrations.email.commands.send_message')
        )
        self.smtp = self.enterContext(patch('django.core.mail.send_mail'))
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.url = reverse('api-po-recorded-evidence', kwargs={'pk': self.order.pk})

    def tearDown(self):
        """No recording path may even initialize a provider or SMTP sender."""
        self.gmail.assert_not_called()
        self.send.assert_not_called()
        self.smtp.assert_not_called()
        super().tearDown()

    def record(self, data=None):
        """Invoke the actual record-only service."""
        return record_documents(
            order_id=self.order.pk, actor=self.actor, data=data or self.data
        )

    def test_exact_pdf_email_context_and_hashes(self):
        """Native 620 + 45 is rendered once, fractional quantity stays 1.25."""
        evidence, created = self.record()
        self.assertTrue(created)
        pdf, email = bytes(evidence.pdf_bytes), bytes(evidence.email_bytes)
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn('1.25', text)
        self.assertIn('665.00', text)
        self.assertIn('Pending', text)
        self.assertIn('EQUA AIMMS', text)
        self.assertIn(self.data['ship_to'], text)
        self.assertIn('Representative fixture', text)
        self.assertEqual(text.count('Freight'), 1)
        self.assertNotIn('Shipping', text)
        self.assertNotIn('InvenTree', text)
        self.assertEqual(evidence.pdf_sha256, hashlib.sha256(pdf).hexdigest())
        self.assertEqual(evidence.email_sha256, hashlib.sha256(email).hexdigest())
        message = BytesParser(policy=policy.default).parsebytes(email)
        self.assertEqual(
            list(message.iter_attachments())[0].get_payload(decode=True), pdf
        )
        self.assertEqual(
            message
            .get_body(preferencelist=('plain',))
            .get_content()
            .replace('\r\n', '\n'),
            evidence.context['email']['body'],
        )
        self.assertEqual(evidence_result(evidence)['state'], 'recorded_not_sent')
        self.assertEqual(evidence_result(evidence)['attachment_state'], 'verified')
        self.assertEqual(evidence.context['actor']['pk'], self.actor.pk)
        self.assertEqual(evidence.context['lines'][0]['supplier_part_id'], self.sp.pk)
        self.assertIsNone(evidence.context['tax'])
        if Path('/evidence').is_dir():
            Path('/evidence/r8-po-fixture.pdf').write_bytes(pdf)
            Path('/evidence/r8-po-fixture.html').write_text(
                PDFService().render_html(
                    'purchase_order.html', evidence.context.copy()
                ),
                encoding='utf-8',
            )

    def test_supplier_pack_is_not_labelled_as_base_quantity(self):
        """Order packs and their actual base conversion remain distinct in PDF."""
        self.sp.pack_quantity = '3'
        self.sp.full_clean()
        self.sp.save()
        evidence, _ = self.record()
        line = evidence.context['lines'][0]
        self.assertEqual(line['unit'], 'order unit')
        self.assertEqual(line['base_unit'], 'each')
        text = '\n'.join(
            page.extract_text()
            for page in PdfReader(io.BytesIO(bytes(evidence.pdf_bytes))).pages
        )
        text = ' '.join(text.split())
        self.assertIn('1 order unit = 3 each', text)
        self.assertIn('1.25 order unit', text)
        if Path('/evidence').is_dir():
            Path('/evidence/r8-po-pack-fixture.pdf').write_bytes(
                bytes(evidence.pdf_bytes)
            )

    def test_replay_returns_original_after_native_edits_without_rendering(self):
        """A completed record is authoritative before current order validation."""
        first, _ = self.record()
        PurchaseOrderLineItem.objects.filter(pk=self.line.pk).update(
            purchase_price=None
        )
        with patch('order.procurement_evidence.get_pdf_service') as renderer:
            second, created = self.record()
        renderer.assert_not_called()
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(bytes(first.pdf_bytes), bytes(second.pdf_bytes))
        self.assertEqual(PurchaseOrderDocumentEvidence.objects.count(), 1)

    def test_authorized_replay_preserves_the_original_recorder(self):
        """Another evidence reader may recover bytes without rewriting attribution."""
        original, _ = self.record()
        other = get_user_model().objects.create_superuser(username='r8-other-recorder')
        replay, created = record_documents(
            order_id=self.order.pk, actor=other, data=self.data
        )
        self.assertFalse(created)
        self.assertEqual(replay.pk, original.pk)
        self.assertEqual(replay.actor_id, self.actor.pk)
        self.assertEqual(PurchaseOrderDocumentEvidence.objects.count(), 1)

    def test_changed_intent_same_key_conflicts(self):
        """Reusing an operation for a different address cannot replace the bytes."""
        self.record()
        with self.assertRaises(EvidenceConflict):
            self.record({**self.data, 'ship_to': 'Different fixture receiving street'})
        self.assertEqual(PurchaseOrderDocumentEvidence.objects.count(), 1)

    def test_missing_context_null_prices_and_foreign_currency_stop(self):
        """No missing commercial source may turn into plausible sample data."""
        for field in (
            'operation_key',
            'ship_to',
            'buyer_name',
            'to',
            'from_address',
            'provenance_kind',
        ):
            data = dict(self.data)
            data.pop(field)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.record(data)
        for price, currency in ((None, 'USD'), (45, 'EUR')):
            PurchaseOrderExtraLine.objects.filter(pk=self.freight.pk).update(
                price=price, price_currency=currency
            )
            with self.assertRaises(ValidationError):
                self.record()
        self.assertFalse(PurchaseOrderDocumentEvidence.objects.exists())

    def test_renderer_failure_has_no_partial_evidence(self):
        """Retry is safe when rendering failed before any durable document."""
        with patch(
            'order.procurement_evidence.get_pdf_service',
            side_effect=RuntimeError('fixture renderer failure'),
        ):
            with self.assertRaises(RuntimeError):
                self.record()
        self.assertFalse(PurchaseOrderDocumentEvidence.objects.exists())
        self.assertTrue(self.record()[1])

    def test_new_evidence_model_matches_its_migration(self):
        """Known unrelated app drift cannot conceal a missing P2 schema field."""
        historical = (
            MigrationLoader(connection)
            .project_state()
            .apps.get_model('order', 'PurchaseOrderDocumentEvidence')
        )
        expected = {
            field.name: field.deconstruct()[1:]
            for field in historical._meta.local_fields
        }
        actual = {
            field.name: field.deconstruct()[1:]
            for field in PurchaseOrderDocumentEvidence._meta.local_fields
        }
        self.assertEqual(actual, expected)

    def test_zero_prices_tax_and_precision_in_templates(self):
        """Known zero and five-decimal quantities survive Jinja rendering."""
        context = {
            'reference': 'FIXTURE',
            'company_name': 'EQUA AIMMS',
            'currency': 'USD',
            'lines': [
                {
                    'part_name': 'Fixture',
                    'quantity': '0.00001',
                    'unit_price': 0,
                    'total_price': 0,
                    'target_price': 0,
                    'your_price': 0,
                }
            ],
            'subtotal': 0,
            'tax': 0,
            'tax_rate': 0,
            'shipping_cost': 0,
            'total_price': 0,
        }
        for template in ('purchase_order.html', 'rfq.html'):
            html = PDFService().render_html(template, context.copy())
            self.assertIn('0.00001', html)
            self.assertIn('0.00', html)
        html = PDFService().render_html('purchase_order.html', context)
        self.assertIn('Tax', html)
        self.assertIn('Subtotal', html)
        self.assertIn('Shipping', html)

    def test_api_record_list_read_bytes_and_scope(self):
        """Native PO authorization plus email-evidence capability guards reads."""
        result = self.client.post(self.url, self.data, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertNotIn('pdf_base64', result.data)
        self.assertEqual(
            self.client.post(self.url, self.data, format='json').status_code, 200
        )
        read = self.client.get(self.url, {'operation_key': self.data['operation_key']})
        self.assertEqual(read.status_code, 200, read.data)
        self.assertTrue(base64.b64decode(read.data['pdf_base64']).startswith(b'%PDF-'))
        self.assertFalse(read.data['supplier_email_sent'])
        self.assertEqual(len(self.client.get(self.url).data), 1)
        other = get_user_model().objects.create_user(username='R8-no-purchasing')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            record_documents(order_id=self.order.pk, actor=other, data=self.data)

    def test_current_evidence_permission_is_rechecked_on_replay(self):
        """Purchasing permission cannot imply access to recipient/body evidence."""
        self.record()
        owner = get_user_model().objects.create_user(username='R8-permissioned-buyer')
        owner.user_permissions.add(
            Permission.objects.get(
                codename='change_purchaseorder', content_type__app_label='order'
            )
        )
        with self.assertRaises(PermissionDenied):
            record_documents(order_id=self.order.pk, actor=owner, data=self.data)
        email_permission = Permission.objects.get(
            codename='view_email', content_type__app_label='users'
        )
        owner.user_permissions.add(email_permission)
        self.assertFalse(
            record_documents(order_id=self.order.pk, actor=owner, data=self.data)[1]
        )
        owner.user_permissions.remove(email_permission)
        with self.assertRaises(PermissionDenied):
            record_documents(order_id=self.order.pk, actor=owner, data=self.data)

    def test_corruption_is_known_and_evidence_cannot_be_saved_over(self):
        """Stored-byte reconciliation returns corrupt, never an inferred send."""
        evidence, _ = self.record()
        with self.assertRaises(ValueError):
            evidence.save()
        PurchaseOrderDocumentEvidence.objects.filter(pk=evidence.pk).update(
            pdf_bytes=b'%PDF-corrupt'
        )
        evidence.refresh_from_db()
        self.assertEqual(evidence_result(evidence)['attachment_state'], 'corrupt')
        self.assertFalse(evidence_result(evidence)['supplier_email_sent'])


class ProcurementWrapperTests(TestCase):
    """Test the real raw tools through stub HTTP clients, never a server."""

    def test_supplierpart_is_native_part_and_internal_part_never_sent(self):
        """Ambiguous, inactive and wrong-supplier records cannot be guessed."""
        from ai.core.tools.inventree.write import purchase_orders

        # Some SDK versions wrap functions in FunctionTool; `.func` is the body.
        function = getattr(
            purchase_orders.add_po_line_item, 'func', purchase_orders.add_po_line_item
        )
        client = Mock()
        client.get_purchase_order = AsyncMock(return_value={'pk': 1, 'supplier': 10})
        good = {
            'pk': 77,
            'part': 42,
            'supplier': 10,
            'active': True,
            'part_detail': {'active': True, 'purchaseable': True},
        }
        client.get_supplier_parts = AsyncMock(return_value=[good])
        client._request = AsyncMock(return_value={'pk': 12})
        with patch(
            'ai.core.integrations.inventree.client.get_inventree_client',
            return_value=client,
        ):
            async_to_sync(function)(
                order_id=1, part_id=42, quantity=1, purchase_price=0
            )
            data = client._request.call_args.kwargs['json_data']
            self.assertEqual(data['part'], 77)
            self.assertNotIn('supplier_part', data)
            self.assertFalse(data['auto_pricing'])
            for candidates in (
                [good, {**good, 'pk': 78}],
                [{**good, 'supplier': 11}],
                [{**good, 'active': False}],
            ):
                client.get_supplier_parts.return_value = candidates
                with self.assertRaises(ValueError):
                    async_to_sync(function)(order_id=1, part_id=42, quantity=1)
            self.assertEqual(client._request.await_count, 1)
            client.get_supplier_parts.return_value = [good, {**good, 'pk': 78}]
            async_to_sync(function)(
                order_id=1,
                part_id=42,
                supplier_part_id=78,
                quantity=1,
                purchase_price=0,
            )
            self.assertEqual(client._request.call_args.kwargs['json_data']['part'], 78)

    def test_legacy_missing_source_data_never_uses_sample_or_sends(self):
        """Even the legacy compound tool fails closed before PDF or sender."""
        from ai.core.integrations.email import legacy_tools

        function = getattr(
            legacy_tools.generate_and_send_document,
            'func',
            legacy_tools.generate_and_send_document,
        )
        while hasattr(function, '__wrapped__'):
            function = function.__wrapped__
        with (
            patch.object(legacy_tools, '_build_sample_data') as sample,
            patch.object(legacy_tools, 'send_email') as send,
        ):
            for data in (None, {}, {'reference': 'FIXTURE-PO'}):
                result = async_to_sync(function)(
                    document_type='purchase_order',
                    to='fixture@example.com',
                    document_data=data,
                )
                self.assertFalse(result['success'])
            sample.assert_not_called()
            send.assert_not_called()


class AttachmentReconciliationTests(TestCase):
    """Read-only mailbox reconciliation recognizes exact attachment bytes."""

    def test_matching_attachment_is_known_and_changed_attachment_is_unknown(self):
        """Provider lookup verifies hashes and multiplicity without resending."""
        payload = {
            'to': 'fixture@example.com',
            'subject': 'Fixture evidence',
            'body': 'Exact fixture body\n',
            'attachments': [
                {
                    'filename': 'fixture.pdf',
                    'data_bytes': b'%PDF-fixture',
                    'mime_type': 'application/pdf',
                }
            ],
        }
        msg = commands._mime(
            payload, sender='sender@example.com', operation_id='fixture-key'
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        client = Mock(email='sender@example.com')
        messages = (
            client._get_service.return_value.users.return_value.messages.return_value
        )
        messages.list.return_value.execute.return_value = {
            'messages': [{'id': 'fixture-id'}]
        }
        messages.get.return_value.execute.return_value = {
            'id': 'fixture-id',
            'threadId': 'fixture-thread',
            'labelIds': ['SENT'],
            'raw': raw,
        }
        with patch.object(commands, 'get_gmail_client', return_value=client):
            self.assertEqual(
                commands.reconcile_message(payload, operation_id='fixture-key')[
                    'outcome'
                ],
                'succeeded',
            )
            payload['attachments'][0]['data_bytes'] = b'%PDF-different'
            self.assertEqual(
                commands.reconcile_message(payload, operation_id='fixture-key')[
                    'outcome'
                ],
                'unknown',
            )
        messages.send.assert_not_called()
