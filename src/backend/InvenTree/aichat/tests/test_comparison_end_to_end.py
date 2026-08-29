"""S9 WP-C13: the comparison rail end-to-end over REAL fixtures.

No seams: the real gate, the real readers, the real status derivation,
the real renderer and the FULL validator (including the real C13
reauthorization closure) over a database work order with a structured
procedure execution and an explicit deviation.
"""

import datetime
import unittest
import uuid
from types import SimpleNamespace

from django.apps import apps

if not apps.is_installed('assets'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client
from ai.core.analysis import executor as executor_module
from ai.core.analysis.evidence import EvidenceStore
from ai.core.analysis.renderer import assign_ordinals, render_answer
from ai.core.analysis.synthesis import deterministic_claims
from ai.core.analysis.validator import CheckOutcome, validate_analysis
from ai.core.entities import build_analysis_entity_manifest
from tasks.models import WorkOrder
from tasks.procedure_models import (
    Procedure,
    ProcedureRevision,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.scope import MaintenanceScope
from tasks.workorder_models import WorkOrderDeviation

READ_FLAGS = {
    'AIMMS_MAINTENANCE_AI_READ_ENABLED': True,
    'AIMMS_MACHINE_AI_READ_ENABLED': True,
}


@override_settings(**READ_FLAGS)
class ComparisonEndToEndTests(TestCase):
    """One structured execution, one deviation, one validated answer."""

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        users = get_user_model().objects
        cls.actor = users.create_superuser(
            username=f'e2e-actor-{suffix}', email='e2e@example.com', password='pw'
        )
        cls.author = users.create_user(username=f'e2e-author-{suffix}')
        cls.tenant = Client.objects.create(
            name=f'E2E Plant {suffix}', code=f'e2e-{suffix}'
        )
        cls.machine = AssetMachine.objects.create(
            name=f'E2E HX {suffix}', client=cls.tenant, serial=f'E2E-{suffix}'
        )
        cls.work_order = WorkOrder.objects.create(
            title='E2E corrective',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine,
            lifecycle_status='completed',
            actual_completed_at=datetime.datetime(2026, 1, 15, 12, 0),
        )
        procedure = Procedure.objects.create(
            code=f'E2E-{suffix}', name='E2E procedure', created_by=cls.author
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=2,
            work_order_type='corrective',
            content_hash='c' * 64,
            created_by=cls.author,
        )
        cls.application = WorkOrderProcedureApplication.objects.create(
            work_order=cls.work_order,
            revision=revision,
            snapshot={'title': 'E2E procedure'},
            snapshot_hash='d' * 64,
            applied_by=cls.author,
            idempotency_key=f'e2e-{suffix}',
        )
        cls.deviating_step = uuid.uuid4()
        WorkOrderStepExecution.objects.create(
            application=cls.application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'title': 'Isolate'},
            status='completed',
            passed=True,
        )
        WorkOrderStepExecution.objects.create(
            application=cls.application,
            step_key=cls.deviating_step,
            sequence=2,
            step_snapshot={'title': 'Replace fuse'},
            status='completed',
            passed=True,
        )
        WorkOrderStepExecution.objects.create(
            application=cls.application,
            step_key=uuid.uuid4(),
            sequence=3,
            step_snapshot={'title': 'Verify output'},
            status='pending',
        )
        WorkOrderDeviation.objects.create(
            work_order=cls.work_order,
            category='step_not_applicable',
            application_key=str(cls.application.pk),
            step_key=str(cls.deviating_step),
            reason='Different fuse holder fitted',
        )

    def setUp(self):
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }

    def test_full_rail_produces_a_validated_comparison(self):
        store = EvidenceStore()
        run_stub = SimpleNamespace(query_plan=None)
        executor_module._retrieve_comparison(
            self.actor,
            store,
            scope=None,
            query=f'did {self.work_order.reference} follow the procedure?',
            run=run_stub,
        )

        # The manifest pinned the exact operand versions and the revision.
        manifest = run_stub.query_plan
        self.assertEqual(manifest.plan['route'], 'structured')
        self.assertEqual(manifest.document_pins[0]['content_hash'], 'c' * 64)
        self.assertGreaterEqual(manifest.operand_count, 4)  # wo + app + 3 steps

        facets, claims = deterministic_claims('manual_wo_comparison', store)
        ordinals = assign_ordinals(claims, store, default_as_of='2026-08-29')
        rendered = render_answer(claims, store, ordinals=ordinals)
        entities = build_analysis_entity_manifest(claims, store, None)
        verdict = validate_analysis(
            claims=claims,
            facets=facets,
            store=store,
            rendered=rendered,
            entities=entities,
            scope=None,
            ledger_retrieval_ids=store.retrieval_ids(),
            ledger_chunk_ids=None,
            emitted_events=(),
            reauthorize=lambda: executor_module._reauthorize(self.actor, store),
        )
        self.assertIs(verdict.outcome, CheckOutcome.PASS, verdict.codes())

        text = rendered.detailed_response
        self.assertIn('1 of 3 steps are documented as performed', text)
        self.assertIn('- step 2: documented_deviation', text)
        self.assertIn('- step 3: not_recorded', text)
        self.assertIn('Absence of a record is not noncompliance', text)
        self.assertIn('Compliance verdicts are not produced', text)

        specs = store.persistence_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(
            specs[0]['members'][0][2], str(self.work_order.pk)
        )
        self.assertEqual(specs[0]['snapshot_hash'], manifest.operand_hash)

    def test_gate_unmet_end_to_end_names_the_facets(self):
        from ai.core.analysis.snapshot import AnalysisRetrievalIncomplete

        bare = WorkOrder.objects.create(
            title='No structured record',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
            machine=self.machine,
            lifecycle_status='completed',
            actual_completed_at=datetime.datetime(2026, 2, 1, 9, 0),
        )
        store = EvidenceStore()
        with self.assertRaises(AnalysisRetrievalIncomplete) as caught:
            executor_module._retrieve_comparison(
                self.actor,
                store,
                scope=None,
                query=f'did {bare.reference} follow the manual?',
                run=SimpleNamespace(query_plan=None),
            )
        self.assertEqual(caught.exception.code, 'comparison_gate_unmet')
        self.assertIn(
            'no_procedure_or_verified_manual', caught.exception.facets
        )
