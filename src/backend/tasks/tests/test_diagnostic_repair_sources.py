"""Diagnostic evidence chronology and exact native Work/source ownership."""

import hashlib
from dataclasses import replace
from io import BytesIO
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from ai.core.auth import AIPrincipal
from ai.core.focus.contract import FocusTarget, focus_to_stored
from ai.core.reasoning.repair_source_context import (
    bind_repair_sources,
    inspection_requested,
)
from ai.core.tools.diagnostics import (
    DiagnosticAuthorizationError,
    DiagnosticRecordRoot,
    build_diagnostic_context,
    get_diagnostic_tool_registry,
)
from aichat.models import ChatThread
from common.models import Attachment
from tasks.tests.test_repair_scope_drafts import DraftFixture


def source_capabilities(actor):
    """Use native view authority for this isolated diagnostic resolver."""
    from users.permissions import check_user_role

    return (
        ('diagnostics.packet.read', 'diagnostics.manuals.read')
        if check_user_role(actor, 'work_order', 'view')
        else ()
    )


def pdf_bytes():
    """Generate a real text PDF using an already installed production dependency."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    buffer = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=842)
    font = DictionaryObject(
        {
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        }
    )
    page[NameObject('/Resources')] = DictionaryObject(
        {
            NameObject('/Font'): DictionaryObject(
                {NameObject('/F1'): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b'BT /F1 12 Tf 40 800 Td (4.6.3 Cooling surfaces: synthetic literal manual passage) Tj ET'
    )
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(buffer)
    return buffer.getvalue()


@override_settings(
    USE_TZ=True,
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER='tasks.tests.test_diagnostic_repair_sources.source_capabilities',
)
class DiagnosticRepairSourcesTest(DraftFixture, TestCase):
    """Real registry/domain source reads; no provider calls or live data writes."""

    def setUp(self):
        """Bind a genuinely owned thread to the fixture's exact Work and packet."""
        super().setUp()
        self.manual.attachment.save(
            'source-manual.pdf', ContentFile(pdf_bytes()), save=True
        )
        self.principal = AIPrincipal(
            subject=f'user:{self.actor.pk}',
            actor=f'user:{self.actor.pk}',
            user_pk=str(self.actor.pk),
            username=self.actor.username,
            authentication_method='session',
            scope='site:test',
            policy_version='test',
            is_staff=True,
            is_superuser=True,
        )
        focus = focus_to_stored(
            FocusTarget(
                kind='work_order',
                id=self.work.pk,
                label=self.work.reference,
                machine_id=self.machine.pk,
                work_order_id=self.work.pk,
            )
        )
        self.snapshot = {'focus': focus, 'version': 1}
        self.thread = ChatThread.objects.create(
            owner=self.actor,
            scope_key='site:test',
            scope_hash=hashlib.sha256(b'site:test').hexdigest(),
            conversation_focus=focus,
            focus_version=1,
        )
        self.context = build_diagnostic_context(
            self.principal,
            server_record_roots=(
                DiagnosticRecordRoot(
                    entity_type='repair_packet',
                    entity_id=self.packet.pk,
                    expected_revision=self.packet.updated_at.isoformat(),
                    linked_machine_id=self.machine.pk,
                ),
            ),
            server_allowed_capabilities=source_capabilities(self.actor),
            max_age_seconds=300,
        )
        self.registry = get_diagnostic_tool_registry()
        self.args = {
            'repair_packet_id': self.packet.pk,
            'expected_revision': self.packet.updated_at.isoformat(),
        }

    def bound(self, content):
        """Apply the same server target binding used before diagnostic provider dispatch."""
        return bind_repair_sources(
            self.context,
            actor=self.principal,
            thread_id=self.thread.pk,
            focus_snapshot=self.snapshot,
            content=content,
        )

    def test_initial_checks_and_sensor_alternative_cannot_retrieve_e03(self):
        """Verify initial checks and sensor alternative cannot retrieve e03."""
        for content in (
            'What are the three most useful checks for this high temperature?',
            'Could this be a sensor or wiring problem rather than fouling?',
        ):
            context = self.bound(content)
            names = {
                row['name'] for row in self.registry.provider_tools(context=context)
            }
            self.assertIn('search_related_work_manuals', names)
            self.assertNotIn('get_requested_prepared_inspection', names)
            with self.assertRaises(DiagnosticAuthorizationError):
                self.registry.execute(
                    'get_requested_prepared_inspection', self.args, context=context
                )

    def test_e03_requires_explicit_current_request_and_exact_focus(self):
        """Verify e03 requires explicit current request and exact focus."""
        self.assertTrue(
            inspection_requested(
                'Open inspection record E-zero-three in this same work order.'
            )
        )
        for content in (
            'E03 reports deposits.',
            'Do not open E03.',
            'Review post-repair E05 and E06.',
        ):
            self.assertFalse(inspection_requested(content))
        context = self.bound('Open E03.')
        self.assertTrue(context.prepared_inspection_requested)
        with self.assertRaises(DiagnosticAuthorizationError):
            self.registry.execute(
                'get_requested_prepared_inspection',
                {**self.args, 'repair_packet_id': self.packet.pk + 99},
                context=context,
            )
        with self.assertRaises(DiagnosticAuthorizationError):
            self.registry.execute(
                'get_requested_prepared_inspection', self.args, context=self.context
            )

    def test_manual_reads_literal_source_without_consuming_any_inspection(self):
        """Verify manual reads literal source without consuming any inspection."""
        context = self.bound('Show the motor manual passage.')
        with patch(
            'tasks.services.repair_scope_drafts._evidence_registry',
            side_effect=AssertionError(
                'Manual reads must not consume inspection evidence'
            ),
        ):
            result = self.registry.execute(
                'search_related_work_manuals',
                {
                    **self.args,
                    'query': '4.6.3 cooling',
                    'attachment_id': self.manual.pk,
                    'limit': 3,
                },
                context=context,
            )
        self.assertEqual(result['status'], 'ok', result)
        evidence = result['evidence'][0]
        self.assertEqual(evidence['source_type'], 'related_attached_manual')
        self.assertIn('synthetic literal manual passage', evidence['claim'])
        self.assertIn('related_reference_unverified', evidence['claim'])
        self.assertIn('Not an approved', evidence['claim'])
        self.assertEqual(evidence['revision'], hashlib.sha256(pdf_bytes()).hexdigest())
        self.assertTrue(evidence['locator'].endswith('#page=1'))

    def test_requested_inspection_cites_actual_e03_only_and_retains_prepared_label(
        self,
    ):
        """Verify requested inspection cites actual e03 only and retains prepared label."""
        from tasks.work_execution.exercise_verification import evidence_bytes

        context = self.bound(
            'Open inspection record E-zero-three in this same work order.'
        )
        with patch(
            'tasks.work_execution.exercise_verification.evidence_bytes',
            wraps=evidence_bytes,
        ) as read:
            result = self.registry.execute(
                'get_requested_prepared_inspection', self.args, context=context
            )
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(
            {call.args[0] for call in read.call_args_list}, {'P08-TEMP-E03'}
        )
        self.assertEqual(len(result['evidence']), 1)
        evidence = result['evidence'][0]
        self.assertEqual(evidence['source_type'], 'prepared_inspection_record')
        self.assertEqual(evidence['id'], str(self.e03_id))
        self.assertIn('Prepared exercise', evidence['claim'])
        self.assertNotIn('P08-TEMP-E05', evidence['claim'])
        self.assertNotIn('P08-TEMP-E06', evidence['claim'])
        self.assertNotIn('94.0', evidence['claim'])
        self.assertNotIn('96.0', evidence['claim'])

    def test_source_owner_scope_and_actor_revocation_fail_closed(self):
        """Verify source owner scope and actor revocation fail closed."""
        context = self.bound('Open E03.')
        Attachment.objects.filter(pk=self.e03_id).update(model_id=self.work.pk + 999)
        result = self.registry.execute(
            'get_requested_prepared_inspection', self.args, context=context
        )
        self.assertEqual(result['status'], 'abstain')
        self.assertEqual(result['evidence'], [])
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(DiagnosticAuthorizationError):
            self.registry.execute(
                'get_requested_prepared_inspection', self.args, context=context
            )

    def test_stale_focus_and_unowned_thread_cannot_enable_source_tools(self):
        """Verify stale focus and unowned thread cannot enable source tools."""
        self.thread.focus_version = 2
        self.thread.save(update_fields=['focus_version'])
        context = self.bound('Open E03.')
        self.assertIsNone(context.focused_repair_packet_id)
        context = bind_repair_sources(
            self.context,
            actor=replace(self.principal, user_pk=str(self.reviewer.pk)),
            thread_id=self.thread.pk,
            focus_snapshot=self.snapshot,
            content='Open E03.',
        )
        self.assertIsNone(context.focused_repair_packet_id)

    def test_other_manual_owner_and_stale_revision_cannot_supply_evidence(self):
        """Verify other manual owner and stale revision cannot supply evidence."""
        context = self.bound('Read the motor manual.')
        Attachment.objects.filter(pk=self.manual.pk).update(
            model_id=self.machine.pk + 999
        )
        result = self.registry.execute(
            'search_related_work_manuals',
            {**self.args, 'query': 'cooling', 'attachment_id': self.manual.pk},
            context=context,
        )
        self.assertEqual(result['status'], 'abstain')
        with self.assertRaises(DiagnosticAuthorizationError):
            self.registry.execute(
                'search_related_work_manuals',
                {**self.args, 'expected_revision': 'stale', 'query': 'cooling'},
                context=context,
            )
