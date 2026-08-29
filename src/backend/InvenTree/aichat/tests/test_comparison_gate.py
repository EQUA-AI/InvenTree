"""S9 WP-C11: the §8.5 comparison eligibility gate."""

import datetime
import unittest
import uuid

from django.apps import apps

if not apps.is_installed('assets'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client
from aichat.models import ControlledDocument, ControlledDocumentState
from aichat.services import applicability
from ai.core.analysis.comparison import (
    evaluate_comparison_gate,
    explicit_work_order_pk,
)
from tasks.models import WorkOrder
from tasks.procedure_models import (
    Procedure,
    ProcedureRevision,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.scope import MaintenanceScope

READ_FLAGS = {'AIMMS_MAINTENANCE_AI_READ_ENABLED': True}
SCOPE_KEY = 'epcon-experimental'


@override_settings(**READ_FLAGS)
class ComparisonGateTestCase(TestCase):
    """A small completed-work-order graph with one structured execution."""

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        users = get_user_model().objects
        cls.actor = users.create_superuser(
            username=f'gate-actor-{suffix}', email='ga@example.com', password='pw'
        )
        cls.author = users.create_user(username=f'gate-author-{suffix}')
        cls.proposer = users.create_user(username=f'gate-proposer-{suffix}')
        cls.verifier = users.create_user(username=f'gate-verifier-{suffix}')
        cls.verifier.user_permissions.add(
            Permission.objects.get(
                codename='verify_document_applicability',
                content_type__app_label='aichat',
            )
        )
        cls.tenant = Client.objects.create(
            name=f'Gate Plant {suffix}', code=f'gate-{suffix}'
        )
        cls.machine = AssetMachine.objects.create(
            name=f'Gate HX {suffix}', client=cls.tenant, serial=f'GATE-{suffix}'
        )

        cls.structured_wo = WorkOrder.objects.create(
            title='Structured corrective',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine,
            lifecycle_status='completed',
            actual_completed_at=datetime.datetime(2026, 1, 15, 12, 0),
        )
        cls.procedure = Procedure.objects.create(
            code=f'GATE-{suffix}', name='Gate procedure', created_by=cls.author
        )
        cls.revision = ProcedureRevision.objects.create(
            procedure=cls.procedure,
            revision=1,
            work_order_type='corrective',
            content_hash='c' * 64,
            created_by=cls.author,
        )
        cls.application = WorkOrderProcedureApplication.objects.create(
            work_order=cls.structured_wo,
            revision=cls.revision,
            snapshot={'title': 'Gate procedure'},
            snapshot_hash='d' * 64,
            applied_by=cls.author,
            idempotency_key=f'gate-{suffix}',
        )
        WorkOrderStepExecution.objects.create(
            application=cls.application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'title': 'Isolate'},
            status='completed',
            passed=True,
        )

    def setUp(self):
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }

    def _manual_wo(self, *, completed=datetime.datetime(2026, 2, 10, 9, 0), machine=None):
        return WorkOrder.objects.create(
            title='Manual-route corrective',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
            machine=machine if machine is not None else self.machine,
            lifecycle_status='completed',
            actual_completed_at=completed,
        )

    def _verified_document(self, *, effective_from=None, effective_to=None):
        document = ControlledDocument.objects.create(
            document_id=f'gate-manual-{uuid.uuid4().hex[:6]}',
            revision='2.0',
            title='Gate manual',
            document_class='technical_manual',
            scope_key=SCOPE_KEY,
            scope_hash='a' * 64,
            access_class='maintenance_authorized',
            source_filename='gate.md',
            source_location='/tmp/gate.md',
            source_sha256='b' * 64,
            state=ControlledDocumentState.INDEXED,
            is_current=True,
            search_index_name='idx',
        )
        row = applicability.propose(
            document=document,
            kind='exact_machine',
            actor=self.proposer,
            basis='commissioning record',
            target_machine_id=self.machine.pk,
            target_serial=self.machine.serial,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        applicability.verify(row.pk, actor=self.verifier)
        return document


class GateTests(ComparisonGateTestCase):
    """Six checks; a known-insufficient record is never used anyway."""

    def test_reference_parsing(self):
        self.assertEqual(explicit_work_order_pk('did WO-000041 follow it?'), 41)
        self.assertIsNone(explicit_work_order_pk('did the last job follow it?'))

    def test_structured_route_preferred_with_version_rows(self):
        selection = evaluate_comparison_gate(
            self.actor, query='did the last corrective follow the procedure?'
        )
        candidate = selection.candidate
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.route, 'structured')
        self.assertEqual(candidate.work_order_id, self.structured_wo.pk)
        self.assertFalse(candidate.drift)
        self.assertEqual(candidate.steps['population_count'], 1)
        kinds = {pin['kind'] for pin in selection.document_pins}
        self.assertEqual(kinds, {'procedure_revision'})
        version_keys = [key for key, _v in selection.version_rows]
        self.assertIn(f'work_order:{self.structured_wo.pk}', version_keys)
        self.assertTrue(any(key.startswith('step:') for key in version_keys))

    def test_explicit_reference_selects_that_work_order(self):
        reference_query = f'compare {self.structured_wo.reference} to the manual'
        selection = evaluate_comparison_gate(self.actor, query=reference_query)
        self.assertTrue(selection.explicit_reference)
        self.assertEqual(selection.candidate.work_order_id, self.structured_wo.pk)

    def test_candidate_loop_skips_the_insufficient_and_names_why(self):
        # Newest by completion, but with neither a structured application
        # nor a verified manual: the rule's first candidate is skipped WITH
        # its reason, and the next one serves — never "used anyway".
        newest = self._manual_wo(completed=datetime.datetime(2026, 3, 1, 8, 0))
        selection = evaluate_comparison_gate(
            self.actor, query='did the latest repair follow the procedure?'
        )
        self.assertIsNotNone(selection.candidate)
        self.assertEqual(selection.candidate.work_order_id, self.structured_wo.pk)
        self.assertIn(
            (newest.pk, 'no_procedure_or_verified_manual'), selection.skipped
        )

    def test_manual_route_requires_historical_effectiveness(self):
        manual_wo = self._manual_wo()
        self._verified_document(
            effective_from=datetime.date(2026, 1, 1),
            effective_to=datetime.date(2026, 12, 31),
        )
        selection = evaluate_comparison_gate(
            self.actor, query=f'compare {manual_wo.reference} against the manual'
        )
        candidate = selection.candidate
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.route, 'verified_manual')
        self.assertEqual(selection.document_pins[0]['content_hash'], 'b' * 64)

    def test_stale_effective_window_is_gate_unmet(self):
        manual_wo = self._manual_wo()
        self._verified_document(
            effective_from=datetime.date(2020, 1, 1),
            effective_to=datetime.date(2025, 12, 31),
        )
        selection = evaluate_comparison_gate(
            self.actor, query=f'compare {manual_wo.reference} against the manual'
        )
        self.assertIsNone(selection.candidate)
        self.assertIn('no_procedure_or_verified_manual', selection.missing_facets)

    def test_frequency_premise_is_an_honest_unmet_facet(self):
        selection = evaluate_comparison_gate(
            self.actor, query='is maintenance done every three months as required?'
        )
        self.assertIsNone(selection.candidate)
        self.assertEqual(
            selection.missing_facets, ('complete_frequency_coverage',)
        )
