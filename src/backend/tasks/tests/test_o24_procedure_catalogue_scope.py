"""Shared Work snapshots never grant live procedure catalogue authority."""

import datetime
import json
import uuid
from copy import deepcopy
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from assets.ai_read import authorized_machine
from assets.models import AssetMachine, AssetMaintenanceRecord, Client
from ai.core.analysis import executor
from ai.core.analysis.comparison import evaluate_comparison_gate
from ai.core.analysis.evidence import EvidenceStore, facts_from_maintenance_record_row
from ai.core.analysis.renderer import assign_ordinals, render_answer
from ai.core.analysis.synthesis import deterministic_claims
from ai.core.analysis.validator import CheckOutcome, validate_analysis
from ai.core.entities import build_analysis_entity_manifest
from company.models import Company
from tasks.ai_read import authorized_work_order, work_order_procedure_application
from tasks.ai_analytics import authorized_maintenance_record, get_maintenance_evidence
from tasks.models import WorkOrder
from tasks.procedure_models import (
    Procedure,
    ProcedureRevision,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedure_execution import _application_snapshot, _snapshot_hash


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=None,
    AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
    AIMMS_PROCEDURES_ENABLED=True,
)
class ProcedureCatalogueProjectionScopeTests(TestCase):
    """Exercise native read authority through the complete comparison rail."""

    def setUp(self):
        """Record a real application snapshot, then mutate its foreign catalogue."""
        self.actor = get_user_model().objects.create_user(username='catalogue-reader')
        group = Group.objects.create(name='Catalogue shared Work readers')
        rule = group.rule_sets.get(name='work_order')
        rule.can_view = True
        rule.save()
        self.actor.groups.add(group)
        self.actor.maintenance_scopes = set()
        self.customer = Company.objects.create(name='Foreign catalogue', is_customer=True)
        self.work = WorkOrder.objects.create(
            title='Shared applied procedure',
            customer=self.customer,
            lifecycle_status='completed',
            actual_completed_at=datetime.datetime(2026, 1, 15, 12, 0),
        )
        self.procedure = Procedure.objects.create(
            code='RECORDED-AT-APPLICATION',
            name='Recorded procedure',
            customer=self.customer,
            created_by=self.actor,
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=2,
            status='published',
            work_order_type='corrective',
            content_hash='a' * 64,
            content_version=3,
            created_by=self.actor,
            published_by=self.actor,
            published_at=timezone.now(),
        )
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        self.snapshot = _application_snapshot(self.revision)
        self.application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work,
            revision=self.revision,
            snapshot=deepcopy(self.snapshot),
            snapshot_hash=_snapshot_hash(self.snapshot),
            applied_by=self.actor,
            idempotency_key='catalogue-applied',
        )
        WorkOrderStepExecution.objects.create(
            application=self.application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'title': 'Recorded isolation'},
            status='completed',
            passed=True,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.procedure.code = 'FOREIGN-LIVE-CODE'
        self.procedure.name = 'FOREIGN LIVE NAME'
        self.procedure.save(update_fields=['code', 'name'])
        self.revision.content_hash = 'b' * 64
        self.revision.content_version = 987
        self.revision.save(update_fields=['content_hash', 'content_version'])

    def catalogue_url(self):
        """The direct catalogue reader whose scope every enrichment must reuse."""
        return f'/api/tasks/procedures/{self.procedure.pk}/'

    def selection(self):
        """Run the real comparison gate, including Work and procedure readers."""
        selection = evaluate_comparison_gate(
            self.actor, query=f'did {self.work.reference} follow the procedure?'
        )
        self.assertIsNotNone(selection.candidate)
        return selection

    def assert_saved_pins(self, row):
        """Revision and content identity come from the immutable application."""
        self.assertEqual(row['revision_id'], self.snapshot['revision_id'])
        self.assertEqual(row['revision'], self.snapshot['revision']['revision'])
        self.assertEqual(row['content_hash'], self.snapshot['revision']['content_hash'])
        self.assertEqual(row['content_version'], self.snapshot['revision']['content_version'])
        self.assertEqual(row['snapshot_hash'], self.application.snapshot_hash)
        self.application.refresh_from_db()
        self.assertEqual(self.application.snapshot, self.snapshot)

    def assert_live_withheld(self, row):
        """Neither labels, publication state nor comparison verdict leak live data."""
        self.assertIsNone(row['procedure_code'])
        self.assertIsNone(row['procedure_name'])
        self.assertIsNone(row['revision_status'])
        self.assertEqual(row['drift_status'], 'not_evaluated')
        self.assert_saved_pins(row)

    def assemble(self):
        """Build facts, model input, manifest, entities, renderer and persistence."""
        store = EvidenceStore()
        run = SimpleNamespace(query_plan=None)
        executor._retrieve_comparison(
            self.actor,
            store,
            scope=None,
            query=f'did {self.work.reference} follow the procedure?',
            run=run,
        )
        facets, claims = deterministic_claims('manual_wo_comparison', store)
        rendered = render_answer(
            claims, store, ordinals=assign_ordinals(claims, store, default_as_of='2026-01-15')
        )
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
            reauthorize=lambda: executor._reauthorize(self.actor, store),
        )
        self.assertIs(verdict.outcome, CheckOutcome.PASS, verdict.codes())
        return store, run, rendered, entities

    def test_comparison_reader_withholds_foreign_live_catalogue_and_keeps_snapshot(self):
        """Authorizing shared Work is insufficient for its current procedure."""
        self.assertEqual(self.api.get(self.catalogue_url()).status_code, 404)
        candidate = self.selection().candidate
        self.assert_live_withheld(candidate.application)
        self.assertFalse(candidate.drift)
        self.revision.status = 'archived'
        self.revision.save(update_fields=['status'])
        self.procedure.active = False
        self.procedure.save(update_fields=['active'])
        self.assert_live_withheld(self.selection().candidate.application)

    def test_direct_projection_without_actor_keeps_snapshot_and_withholds_live_data(self):
        """An omitted actor cannot silently inherit the enclosing Work authority."""
        self.assert_live_withheld(work_order_procedure_application(self.work))

    def test_full_analysis_rail_never_copies_foreign_catalogue_into_any_projection(self):
        """Facts, synthesis, text, entities, manifest and persistence stay scoped."""
        self.assertEqual(self.api.get(self.catalogue_url()).status_code, 404)
        store, run, rendered, entities = self.assemble()
        combined = '\n'.join([
            json.dumps(store.synthesis_view(), default=str),
            str(run.query_plan),
            str(rendered),
            str(entities),
            json.dumps(store.persistence_specs(), default=str),
        ])
        for secret in ('FOREIGN-LIVE-CODE', 'FOREIGN LIVE NAME', 'b' * 64):
            self.assertNotIn(secret, combined)
        self.assertEqual(run.query_plan.document_pins[0]['content_hash'], 'a' * 64)
        self.assertIn('1 of 1 steps are documented as performed', rendered.detailed_response)
        self.assertNotIn('snapshot has drifted', rendered.detailed_response)
        self.assertTrue(executor._reauthorize(self.actor, store))

    def test_live_labels_recheck_customer_scope_without_replacing_saved_revision(self):
        """Positive direct-reader access is revoked afresh, including site-only scope."""
        self.actor.maintenance_scopes = {MaintenanceScope(self.customer.pk, None)}
        self.assertEqual(self.api.get(self.catalogue_url()).status_code, 200)
        row = self.selection().candidate.application
        self.assertEqual(row['procedure_code'], 'FOREIGN-LIVE-CODE')
        self.assertIn('FOREIGN LIVE NAME', row['procedure_name'])
        self.assertEqual(row['revision_status'], 'published')
        self.assert_saved_pins(row)
        for scopes in (set(), {MaintenanceScope(self.customer.pk, 'site-only')}):
            with self.subTest(scopes=scopes):
                self.actor.maintenance_scopes = scopes
                self.assertEqual(self.api.get(self.catalogue_url()).status_code, 404)
                self.assert_live_withheld(self.selection().candidate.application)

    def test_emission_reauthorizes_live_catalogue_after_customer_grant_loss(self):
        """C13 must reject assembled live labels after their catalogue grant is lost."""
        self.actor.maintenance_scopes = {MaintenanceScope(self.customer.pk, None)}
        store, _, rendered, _ = self.assemble()
        self.assertIn('FOREIGN-LIVE-CODE', rendered.detailed_response)
        self.assertTrue(executor._reauthorize(self.actor, store))
        self.actor.maintenance_scopes = set()
        self.assertIsNotNone(authorized_work_order(self.actor, self.work.pk))
        self.assertFalse(executor._reauthorize(self.actor, store))

    def test_emission_reauthorizes_live_catalogue_after_procedure_customer_changes(self):
        """C13 reloads the procedure boundary rather than trusting a cached grant."""
        self.actor.maintenance_scopes = {MaintenanceScope(self.customer.pk, None)}
        store, _, _, _ = self.assemble()
        self.procedure.customer = Company.objects.create(name='Reassigned catalogue')
        self.procedure.save(update_fields=['customer'])
        self.assertIsNotNone(authorized_work_order(self.actor, self.work.pk))
        self.assertFalse(executor._reauthorize(self.actor, store))

    def test_legacy_snapshot_missing_pins_does_not_fall_back_to_foreign_live_revision(self):
        """Missing recorded metadata stays unknown instead of reading the catalogue."""
        self.application.snapshot = {'steps': [{'title': 'Legacy saved instruction'}]}
        self.application.save(update_fields=['snapshot'])
        row = self.selection().candidate.application
        self.assertIsNone(row['revision'])
        self.assertIsNone(row['content_version'])
        self.assertIsNone(row['content_hash'])
        self.assertIsNone(row['procedure_code'])
        self.assertIsNone(row['revision_status'])

    def test_legacy_snapshot_full_analysis_reports_missing_revision_truthfully(self):
        """A legacy saved snapshot stays usable without substituting live pins."""
        legacy_snapshot = {'steps': [{'title': 'Legacy saved instruction'}]}
        self.application.snapshot = legacy_snapshot
        self.application.snapshot_hash = _snapshot_hash(legacy_snapshot)
        self.application.save(update_fields=['snapshot', 'snapshot_hash'])
        self.assertEqual(self.api.get(self.catalogue_url()).status_code, 404)
        store, run, rendered, entities = self.assemble()
        self.assertIn('revision not recorded', rendered.detailed_response)
        self.assertNotIn('revision None', rendered.detailed_response)
        self.assertNotIn('snapshot has drifted', rendered.detailed_response)
        self.assertEqual(run.query_plan.document_pins[0]['content_hash'], '')
        combined = '\n'.join([
            json.dumps(store.synthesis_view(), default=str),
            str(run.query_plan),
            str(rendered),
            str(entities),
            json.dumps(store.persistence_specs(), default=str),
        ])
        for secret in ('FOREIGN-LIVE-CODE', 'FOREIGN LIVE NAME', 'b' * 64):
            self.assertNotIn(secret, combined)
        self.assertTrue(executor._reauthorize(self.actor, store))
        self.procedure.code = 'LATER-FOREIGN-LIVE-CODE'
        self.procedure.save(update_fields=['code'])
        self.revision.status = 'archived'
        self.revision.save(update_fields=['status'])
        self.assertTrue(executor._reauthorize(self.actor, store))
        self.application.refresh_from_db()
        self.assertEqual(self.application.snapshot, legacy_snapshot)
        self.assertEqual(self.application.snapshot_hash, _snapshot_hash(legacy_snapshot))


    @override_settings(AIMMS_MACHINE_AI_READ_ENABLED=True)
    def test_shared_foreign_machine_work_analysis_does_not_grant_machine_evidence(self):
        """Work identity remains readable while actual machine facts need scope."""
        client = Client.objects.create(name='Foreign analysis plant', code='foreign-analysis')
        machine = AssetMachine.objects.create(
            name='Shared foreign analysis machine',
            client=client,
            location='Foreign private analysis bay',
        )
        self.work.machine = machine
        self.work.save(update_fields=['machine'])
        record = AssetMaintenanceRecord.objects.create(
            machine=machine,
            work_order=self.work,
            date=datetime.date(2026, 1, 15),
            summary='Foreign physical maintenance history',
        )
        self.assertIsNotNone(authorized_work_order(self.actor, self.work.pk))
        self.assertIsNone(authorized_machine(self.actor, machine.pk))
        self.assertIsNone(authorized_maintenance_record(self.actor, record.pk))
        store, _, rendered, _ = self.assemble()
        self.assertIn('1 of 1 steps are documented as performed', rendered.detailed_response)
        self.assertNotIn('FOREIGN-LIVE-CODE', rendered.detailed_response)
        self.assertTrue(executor._reauthorize(self.actor, store))

        # Add a real, separately authorized physical-history projection, then
        # revoke its client grant without changing the shared Work authority.
        self.actor.maintenance_scopes = {MaintenanceScope(None, None, client.pk)}
        self.assertIsNotNone(authorized_machine(self.actor, machine.pk))
        bundle = get_maintenance_evidence(self.actor, self.work.pk)
        self.assertIsNotNone(bundle['maintenance_record'])
        facts_from_maintenance_record_row(
            store,
            bundle['maintenance_record'],
            retrieval_id='ret_separate_machine_history',
            as_of=timezone.now().isoformat(),
            source_revision=record.updated_at.isoformat(),
        )
        self.assertTrue(executor._reauthorize(self.actor, store))
        self.actor.maintenance_scopes = set()
        self.assertIsNotNone(authorized_work_order(self.actor, self.work.pk))
        self.assertIsNone(authorized_machine(self.actor, machine.pk))
        self.assertIsNone(authorized_maintenance_record(self.actor, record.pk))
        self.assertFalse(executor._reauthorize(self.actor, store))
