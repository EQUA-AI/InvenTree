"""Governed software-exercise checks cannot manufacture physical authority."""

import copy
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from approvals import services as approvals
from approvals.review_sections import compute_review_hash
from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine, Client, get_default_client
from assets.registry_models import AssetComponent, DictionaryPoint
from common.models import Attachment
from company.models import Company
from machine_health.services.test_alarm_work import trigger_with_work, work_plan
from part.models import Part
from repair.pump08_exercise import STEP_DEFINITIONS
from tasks.exercise_verification_api import attach_prepared_evidence
from tasks.authority_models import OperationalAuthorityRecord
from tasks.models import Procedure, ProcedureRevision, WorkOrder, WorkOrderEvent
from tasks.scope import ScopeError, scoped_procedures
from tasks.services.procedure_execution import StepValidationError, _snapshot_hash, apply_procedure_revision, complete_step, mark_step_not_applicable, reopen_step
from tasks.services.procedures import next_draft_revision, request_review
from tasks.services.pump08_procedure import prepare_procedure
from tasks.work_execution.exercise_verification import project


@override_settings(AIMMS_PROCEDURES_ENABLED=True, AIMMS_TEST_ALARMS_ENABLED=True, AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver')
class Pump08ExerciseTest(TestCase):
    """Actual native trigger, compound create, publication, apply and step commands."""

    def setUp(self):
        """Build isolated native workflow fixtures."""
        tenant = get_default_client()
        self.actor = get_user_model().objects.create_superuser(username='exercise-author', password='pw')
        self.reviewer = get_user_model().objects.create_superuser(username='exercise-reviewer', password='pw')
        self.station = AssetMachine.objects.create(client=tenant, asset_type='pumphouse', source_key='PH_3', source_namespace='exercise-fixture', source_entity_uuid=uuid.uuid4(), name='Cedar Creek Final Effluent Pump Station')
        self.machine = AssetMachine.objects.create(client=tenant, asset_type='pump', source_key='P8', parent=self.station, name='Cedar Creek Final Effluent Pump Station / Pump 08')
        motor = Part.objects.create(name='Exercise motor', component=True)
        self.component = AssetComponent.objects.create(machine=self.machine, part=motor, code='PS-MOTOR:1', name='Electric Motor')
        point = DictionaryPoint.objects.create(station=self.station, machine=self.machine, component=self.component, path='/exercise/P8/W6', raw_tag='W6', display_name='Motor Winding Temperature 6', source_hash='exercise', data_type='number', status='approved')
        source = HealthSource.objects.create(name='Exercise source', source_type='scada')
        self.binding = MachineSignalBinding.objects.create(machine=self.machine, source=source, dictionary_point=point, external_key=point.path, display_name=point.display_name, unit='°C', normal_max=124.9, warn_max=125, critical_max=145)
        MachineSignalState.objects.create(binding=self.binding, value={'value': 44.0, 'unit': '°C'}, observed_at=timezone.now(), quality='good')
        plan = work_plan(actor=self.actor, machine=self.machine, binding_id=self.binding.pk)
        self.source, receipt = trigger_with_work(actor=self.actor, machine=self.machine,
            binding_id=self.binding.pk, component_id=self.component.pk,
            idempotency_key=str(uuid.uuid4()), test_value=159.5,
            plan_token=plan['plan_token'], assigned_to_id=self.actor.pk)
        self.work = WorkOrder.objects.get(pk=receipt['work_order_id'])
        self.procedure, self.revision, _ = prepare_procedure(self.machine, self.actor)
        approval = request_review(self.revision, self.reviewer, self.revision.content_version)
        approvals.open_approval(approval.pk, actor=self.reviewer)
        approvals.confirm_viewed(approval.pk, actor=self.reviewer)
        approval.refresh_from_db()
        decision = approvals.approve(approval.pk, actor=self.reviewer, data={'revision': approval.current_revision_number, 'review_hash': compute_review_hash(approval)})
        self.assertEqual(decision.status, 200)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, 'published')
        self.application = apply_procedure_revision(work_order_id=self.work.pk, revision_id=self.revision.pk, actor=self.actor, expected_version=self.work.lifecycle_version, idempotency_key=uuid.uuid4().hex)
        attach_prepared_evidence(self.work.pk, self.actor, uuid.uuid4().hex)
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def record(self, sequence, value, evidence=True):
        """Record one versioned native step with owned evidence."""
        state = project(self.work, self.actor)
        check = state['checks'][sequence - 1]
        return complete_step(
            work_order_id=self.work.pk, application_id=self.application.pk,
            step_key=check['step_key'], actor=self.actor, expected_version=check['version'],
            idempotency_key=uuid.uuid4().hex, value={'number' if sequence == 2 else 'boolean': value},
            note=STEP_DEFINITIONS[sequence - 1]['note'],
            evidence_ids=check['suggested_evidence_ids'] if evidence else [],
        )

    def all_pass(self):
        """Record all three specified prepared exercise values."""
        for sequence, value in [(1, True), (2, 96.0), (3, True)]: self.record(sequence, value)

    def test_three_native_passes_are_exercise_only(self):
        """Verify three native passes are exercise only."""
        before = list(MachineSignalState.objects.values())
        self.all_pass()
        result = project(self.work, self.actor)
        self.assertTrue(result['approved'], result)
        self.assertEqual(result['passed_count'], 3)
        self.assertFalse(result['grants_operating_permission'])
        self.assertFalse(result['grants_technical_acceptance'])
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, 'draft')
        self.assertEqual(before, list(MachineSignalState.objects.values()))
        self.assertFalse(OperationalAuthorityRecord.objects.filter(work_order=self.work).exists())

    def test_numeric_boundaries_and_prepared_evidence_agreement(self):
        """Verify numeric boundaries and prepared evidence agreement."""
        self.record(1, True)
        self.record(3, True)
        for value, passed in [(96.0, True), (100.0, True), (124.9, True), (125, False), (129, False)]:
            with self.subTest(value=value):
                recorded = self.record(2, value)
                self.assertEqual(recorded.passed, passed)
                self.assertEqual(project(self.work, self.actor)['approved'], value == 96.0)

    def test_false_fails_and_retest_recovers(self):
        """Verify false fails and retest recovers."""
        self.all_pass()
        self.record(1, False)
        self.assertFalse(project(self.work, self.actor)['approved'])
        self.record(1, True)
        self.assertTrue(project(self.work, self.actor)['approved'])
        self.assertTrue(self.work.events.filter(event_type='STEP_FAILED').exists())

    def test_missing_or_foreign_evidence_never_passes(self):
        """Verify missing or foreign evidence never passes."""
        with self.assertRaises(StepValidationError): self.record(1, True, evidence=False)
        check = project(self.work, self.actor)['checks'][0]
        foreign = Attachment.objects.create(model_type='kanbancard', model_id=self.work.pk + 1000, link='https://example.com/evidence')
        with self.assertRaises(StepValidationError):
            complete_step(work_order_id=self.work.pk, application_id=self.application.pk, step_key=check['step_key'], actor=self.actor, expected_version=check['version'], idempotency_key=uuid.uuid4().hex, value={'boolean': True}, evidence_ids=[foreign.pk])
        self.assertFalse(project(self.work, self.actor)['approved'])

    def test_deleted_evidence_invalidates_a_prior_pass(self):
        """Verify deleted evidence invalidates a prior pass."""
        self.all_pass()
        attachment_id = project(self.work, self.actor)['evidence'][0]['id']
        Attachment.objects.filter(pk=attachment_id).delete()
        self.assertFalse(project(self.work, self.actor)['approved'])

    def test_reopen_and_not_applicable_never_count(self):
        """Verify reopen and not applicable never count."""
        self.all_pass()
        check = project(self.work, self.actor)['checks'][0]
        reopen_step(work_order_id=self.work.pk, application_id=self.application.pk, step_key=check['step_key'], actor=self.actor, expected_version=check['version'], idempotency_key=uuid.uuid4().hex, reason='Exercise retest')
        self.assertFalse(project(self.work, self.actor)['approved'])
        check = project(self.work, self.actor)['checks'][0]
        mark_step_not_applicable(work_order_id=self.work.pk, application_id=self.application.pk, step_key=check['step_key'], actor=self.actor, expected_version=check['version'], idempotency_key=uuid.uuid4().hex, reason='Not a pass', evidence_ids=check['suggested_evidence_ids'])
        self.assertFalse(project(self.work, self.actor)['approved'])

    def test_snapshot_drift_and_missing_actor_fail_closed(self):
        """Verify snapshot drift and missing actor fail closed."""
        self.all_pass()
        execution = self.application.step_executions.first()
        execution.completed_by = None
        execution.save(update_fields=['completed_by'])
        self.assertFalse(project(self.work, self.actor)['approved'])
        execution.completed_by = self.actor
        execution.save(update_fields=['completed_by'])
        self.application.drift_status = 'changed'
        self.application.save(update_fields=['drift_status'])
        self.assertFalse(project(self.work, self.actor)['approved'])

    def test_unbounded_or_changed_snapshot_does_not_pass(self):
        """Verify unbounded or changed snapshot does not pass."""
        self.all_pass()
        execution = self.application.step_executions.get(sequence=2)
        execution.step_snapshot = {**execution.step_snapshot, 'max_value': None}
        execution.save(update_fields=['step_snapshot'])
        self.assertFalse(project(self.work, self.actor)['approved'])

    def test_superseded_revision_does_not_pass(self):
        """Verify superseded revision does not pass."""
        self.all_pass()
        self.revision.status = 'superseded'
        self.revision.save(update_fields=['status'])
        self.assertFalse(project(self.work, self.actor)['approved'])

    def test_nonfinite_native_values_are_validation_errors(self):
        """Verify nonfinite native values are validation errors."""
        for value in ['NaN', 'Infinity', '-Infinity']:
            with self.subTest(value=value), self.assertRaises(StepValidationError): self.record(2, value)

    def test_read_is_pure_and_refresh_keeps_actor_time(self):
        """Verify read is pure and refresh keeps actor time."""
        self.all_pass()
        before = list(WorkOrderEvent.objects.filter(work_order=self.work).values())
        response = self.api.get(f'/api/tasks/work-orders/{self.work.pk}/exercise-verification/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['approved'])
        self.assertTrue(all(check['recorded_at'] and check['recorded_by'] for check in response.data['checks']))
        self.assertEqual(before, list(WorkOrderEvent.objects.filter(work_order=self.work).values()))

    def test_evidence_idempotency_conflict_is_http_409(self):
        """A reused native command key is a definite refusal, not a server error."""
        command = self.work.commands.exclude(command='exercise_evidence').first()
        response = self.api.post(
            f'/api/tasks/work-orders/{self.work.pk}/exercise-verification/evidence/',
            {'idempotency_key': command.idempotency_key}, format='json',
        )
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'IDEMPOTENCY_CONFLICT')

    def test_disabling_procedures_cannot_bypass_marked_exercise(self):
        """A deployment flag cannot turn an incomplete controlled case into a pass."""
        from tasks.services.readiness import evaluate_work_order_readiness

        with override_settings(AIMMS_PROCEDURES_ENABLED=False):
            result = evaluate_work_order_readiness(self.work, action='complete', actor=self.actor)
        self.assertIn('EXERCISE_VERIFICATION_INCOMPLETE', [row.code for row in result.blockers])

    def test_same_client_can_read_but_unowned_legacy_catalogue_stays_hidden(self):
        """Verify same client can read but unowned legacy catalogue stays hidden."""
        hidden = Procedure.objects.create(code='UNOWNED-EX', name='No scope', created_by=self.actor)
        ids = set(scoped_procedures(self.actor).values_list('pk', flat=True))
        self.assertIn(self.procedure.pk, ids)
        self.assertNotIn(hidden.pk, ids)

    def test_foreign_client_cannot_author_or_apply(self):
        """Verify foreign client cannot author or apply."""
        foreign = Client.objects.create(code='foreign-procedure', name='Foreign procedure')
        procedure = Procedure.objects.create(code='FOREIGN-P', name='Foreign', client=foreign, created_by=self.actor)
        with self.assertRaises(ScopeError): next_draft_revision(procedure, self.actor)

    def test_client_customer_mixed_ownership_is_rejected(self):
        """Verify client customer mixed ownership is rejected."""
        customer = Company.objects.create(name='Unrelated customer', is_customer=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Procedure.objects.create(code='MIXED-P', name='Mixed', client=self.machine.client, customer=customer, created_by=self.actor)

    def test_feature_off_hides_verdict_and_evidence(self):
        """Verify feature off hides verdict and evidence."""
        with override_settings(AIMMS_PROCEDURES_ENABLED=False):
            self.assertEqual(self.api.get(f'/api/tasks/work-orders/{self.work.pk}/exercise-verification/').status_code, 404)
            self.assertEqual(self.api.post(f'/api/tasks/work-orders/{self.work.pk}/exercise-verification/evidence/', {'idempotency_key': uuid.uuid4().hex}, format='json').status_code, 404)

    def test_client_owned_procedure_can_be_authored_through_http(self):
        """Explicit client identity works without inventing a sales customer."""
        response = self.api.post('/api/tasks/procedures/', {
            'code': 'CLIENT-API-EX', 'name': 'Plant-owned procedure',
            'client': self.machine.client_id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['customer'])

    def test_not_applicable_does_not_bypass_this_exercise_closeout(self):
        """Case-specific completion gating never treats a disposition as a pass."""
        from tasks.services.readiness import evaluate_work_order_readiness

        self.all_pass()
        check = project(self.work, self.actor)['checks'][0]
        mark_step_not_applicable(work_order_id=self.work.pk, application_id=self.application.pk,
            step_key=check['step_key'], actor=self.actor, expected_version=check['version'],
            idempotency_key=uuid.uuid4().hex, reason='Not a passed test', evidence_ids=check['suggested_evidence_ids'])
        readiness = evaluate_work_order_readiness(self.work, action='complete', actor=self.actor)
        self.assertIn('EXERCISE_VERIFICATION_INCOMPLETE', [item.code for item in readiness.blockers])
