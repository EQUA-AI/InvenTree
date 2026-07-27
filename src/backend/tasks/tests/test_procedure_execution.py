"""Tests for procedure application and step-execution services."""

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from company.models import Company
from tasks.models import (
    WorkOrder,
    Procedure,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStep,
    ProcedureStepType,
    StepExecutionStatus,
    WorkOrderDeviation,
    WorkOrderLifecycle,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedure_execution import (
    HoldPointBlocked,
    RequiredStepError,
    apply_procedure_revision,
    complete_step,
    mark_step_not_applicable,
    reopen_step,
)
from tasks.services.readiness import (
    STEP_FAILED,
    STEP_REQUIRED,
    evaluate_work_order_readiness,
)


class ProcedureExecutionServiceTest(TestCase):
    """Exercise immutable application and ordered execution commands."""

    def setUp(self):
        self.customer = Company.objects.create(name='ME3 Customer', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='me3-supervisor', email='me3@example.com', password='test-password'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = WorkOrder.objects.create(
            title='ME3 work order', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            assigned_to=self.actor, work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.procedure = Procedure.objects.create(
            code='ME3-PM', name='ME3 procedure', customer=self.customer,
            created_by=self.actor,
        )
        self.revision = self.make_revision(1, ProcedureRevisionStatus.PUBLISHED)
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])

    def make_revision(self, number, status=ProcedureRevisionStatus.DRAFT):
        values = {
            'procedure': self.procedure, 'revision': number,
            'status': status, 'work_order_type': WorkOrderType.PREVENTIVE,
            'created_by': self.actor,
        }
        if status == ProcedureRevisionStatus.PUBLISHED:
            values.update(published_by=self.actor, published_at=timezone.now())
        return ProcedureRevision.objects.create(**values)

    def add_step(self, revision=None, **overrides):
        revision = revision or self.revision
        values = {
            'revision': revision, 'sequence': revision.steps.count() + 1,
            'step_type': ProcedureStepType.INSTRUCTION,
            'title': 'Inspect', 'instruction': 'Inspect the asset.',
            'required': True,
        }
        values.update(overrides)
        return ProcedureStep.objects.create(**values)

    def apply(self, key='apply-me3'):
        return apply_procedure_revision(
            work_order_id=self.work_order.pk, revision_id=self.revision.pk,
            actor=self.actor, expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_apply_creates_once_and_exact_replay_does_not_duplicate(self):
        self.add_step()
        self.add_step(step_type=ProcedureStepType.VERIFICATION, title='Verify')

        application = self.apply()
        replay = self.apply()

        self.assertEqual(replay.pk, application.pk)
        self.assertEqual(WorkOrderProcedureApplication.objects.count(), 1)
        self.assertEqual(WorkOrderStepExecution.objects.count(), 2)
        self.assertEqual(application.step_executions.count(), 2)
        self.assertEqual(self.work_order.events.get().event_type, 'PROCEDURE_APPLIED')
        self.assertEqual(self.work_order.commands.count(), 1)

    def test_newer_revision_does_not_mutate_existing_snapshots(self):
        self.add_step(instruction='Original immutable instruction')
        application = self.apply()
        original_snapshot = json.dumps(application.snapshot, sort_keys=True)
        original_steps = [
            json.dumps(item.step_snapshot, sort_keys=True)
            for item in application.step_executions.order_by('sequence')
        ]

        self.revision.status = ProcedureRevisionStatus.SUPERSEDED
        self.revision.save(update_fields=['status'])
        newer = self.make_revision(2, ProcedureRevisionStatus.PUBLISHED)
        self.add_step(revision=newer, instruction='New and different instruction')
        self.procedure.current_revision = newer
        self.procedure.save(update_fields=['current_revision'])

        application.refresh_from_db()
        self.assertEqual(
            json.dumps(application.snapshot, sort_keys=True), original_snapshot
        )
        self.assertEqual(
            [json.dumps(item.step_snapshot, sort_keys=True)
             for item in application.step_executions.order_by('sequence')],
            original_steps,
        )

    def test_failed_measurement_blocks_subsequent_hard_hold(self):
        measurement = self.add_step(
            step_type=ProcedureStepType.MEASUREMENT, value_type='number',
            min_value=Decimal('10'), max_value=Decimal('20'), title='Measure',
        )
        hold = self.add_step(
            step_type=ProcedureStepType.HOLD_POINT, title='Hard hold',
            evidence_policy={'release_policy': 'hard'}, value_type='boolean',
        )
        application = self.apply()

        result = complete_step(
            work_order_id=self.work_order.pk, application_id=application.pk,
            step_key=measurement.key, actor=self.actor, expected_version=1,
            idempotency_key='measurement-fail', value={'number': '25'}, passed=True,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.status, StepExecutionStatus.FAILED)

        with self.assertRaises(HoldPointBlocked):
            complete_step(
                work_order_id=self.work_order.pk, application_id=application.pk,
                step_key=hold.key, actor=self.actor, expected_version=1,
                idempotency_key='blocked-hold', value={'boolean': True}, passed=True,
            )

    def test_required_skip_needs_disposition_and_creates_deviation(self):
        step = self.add_step()
        application = self.apply()

        with self.assertRaises(RequiredStepError):
            complete_step(
                work_order_id=self.work_order.pk, application_id=application.pk,
                step_key=step.key, actor=self.actor, expected_version=1,
                idempotency_key='silent-skip',
            )

        result = mark_step_not_applicable(
            work_order_id=self.work_order.pk, application_id=application.pk,
            step_key=step.key, actor=self.actor, expected_version=1,
            idempotency_key='explicit-disposition', reason='Asset option is absent',
        )
        self.assertEqual(result.status, StepExecutionStatus.NOT_APPLICABLE)
        deviation = WorkOrderDeviation.objects.get(work_order=self.work_order)
        self.assertEqual(deviation.reason, 'Asset option is absent')
        self.assertEqual(deviation.step_key, str(step.key))

    def test_reopen_appends_history_and_bumps_version(self):
        step = self.add_step(value_type='boolean')
        application = self.apply()
        completed = complete_step(
            work_order_id=self.work_order.pk, application_id=application.pk,
            step_key=step.key, actor=self.actor, expected_version=1,
            idempotency_key='complete-before-reopen', value={'boolean': True},
        )

        reopened = reopen_step(
            work_order_id=self.work_order.pk, application_id=application.pk,
            step_key=step.key, actor=self.actor, expected_version=completed.version,
            idempotency_key='reopen', reason='Repeat after calibration',
        )
        self.assertEqual(reopened.status, StepExecutionStatus.PENDING)
        self.assertEqual(reopened.version, 3)
        self.assertTrue(
            self.work_order.events.filter(event_type='STEP_REOPENED').exists()
        )

    def test_apply_does_not_change_lifecycle_or_allocate_stock(self):
        self.add_step()
        lifecycle = self.work_order.lifecycle_status
        version = self.work_order.lifecycle_version

        self.apply()

        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, lifecycle)
        self.assertEqual(self.work_order.lifecycle_version, version)
        self.assertFalse(self.work_order.work_order_parts.exists())

    def test_readiness_blocks_incomplete_required_steps(self):
        self.add_step()
        self.apply()
        self.work_order.lifecycle_status = WorkOrderLifecycle.VERIFYING
        self.work_order.save(update_fields=['lifecycle_status'])

        readiness = evaluate_work_order_readiness(
            self.work_order, action='complete', actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
        )
        self.assertIn(STEP_REQUIRED, {item.code for item in readiness.blockers})

    def test_readiness_without_application_has_no_step_blocker(self):
        self.work_order.lifecycle_status = WorkOrderLifecycle.VERIFYING
        self.work_order.save(update_fields=['lifecycle_status'])

        readiness = evaluate_work_order_readiness(
            self.work_order, action='complete', actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
        )
        codes = {item.code for item in readiness.blockers}
        self.assertNotIn(STEP_REQUIRED, codes)
        self.assertNotIn(STEP_FAILED, codes)
