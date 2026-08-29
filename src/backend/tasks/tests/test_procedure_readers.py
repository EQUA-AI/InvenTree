"""S9 WP-C10: procedure-execution projections — what was recorded, never who."""

from __future__ import annotations

import json
import unittest
import uuid

from django.apps import apps

if not apps.is_installed('assets'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks import ai_read
from tasks.models import WorkOrder
from tasks.procedure_models import (
    Procedure,
    ProcedureRevision,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.workorder_models import WorkOrderDeviation

HIDDEN_TECH = 'hidden-technician-name'
HIDDEN_READING = 'HIDDEN-READING-42'


class ProcedureReaderTestCase(TestCase):
    """One work order executed against a pinned revision, with a deviation."""

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        users = get_user_model().objects
        cls.author = users.create_user(username=f'proc-author-{suffix}')
        cls.technician = users.create_user(username=HIDDEN_TECH)

        cls.work_order = WorkOrder.objects.create(
            title='Quarterly inverter service',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        cls.procedure = Procedure.objects.create(
            code=f'PROC-{suffix}',
            name='Inverter service procedure',
            created_by=cls.author,
        )
        cls.revision = ProcedureRevision.objects.create(
            procedure=cls.procedure,
            revision=3,
            work_order_type='preventive',
            content_hash='c' * 64,
            content_version=3,
            created_by=cls.author,
        )
        cls.application = WorkOrderProcedureApplication.objects.create(
            work_order=cls.work_order,
            revision=cls.revision,
            sequence=1,
            primary=True,
            snapshot={'title': 'Inverter service procedure', 'steps': []},
            snapshot_hash='d' * 64,
            applied_by=cls.author,
            idempotency_key=f'apply-{suffix}',
        )
        cls.secondary = WorkOrderProcedureApplication.objects.create(
            work_order=cls.work_order,
            revision=cls.revision,
            sequence=2,
            primary=False,
            snapshot={},
            snapshot_hash='e' * 64,
            applied_by=cls.author,
            idempotency_key=f'apply-2-{suffix}',
        )
        cls.step_done = WorkOrderStepExecution.objects.create(
            application=cls.application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'title': 'Isolate and lock out'},
            status='completed',
            passed=True,
            value={'reading': HIDDEN_READING},
            note='Torque checked at spec',
            completed_by=cls.technician,
            completed_at=None,
        )
        cls.step_open = WorkOrderStepExecution.objects.create(
            application=cls.application,
            step_key=uuid.uuid4(),
            sequence=2,
            step_snapshot={'title': 'Replace DC fuse'},
            status='pending',
        )
        cls.deviation = WorkOrderDeviation.objects.create(
            work_order=cls.work_order,
            category='step_not_applicable',
            application_key=str(cls.application.pk),
            step_key=str(cls.step_open.step_key),
            expected={'torque_nm': 45},
            actual={'torque_nm': 38},
            reason='Fuse holder cracked; step deferred',
            actor=cls.technician,
        )


class ApplicationProjectionTests(ProcedureReaderTestCase):
    """Stage 1: the pinned revision, byte-anchored."""

    def test_primary_application_projects_the_pin(self):
        row = ai_read.work_order_procedure_application(self.work_order)
        self.assertEqual(row['application_id'], self.application.pk)
        self.assertTrue(row['primary'])
        self.assertEqual(row['content_hash'], 'c' * 64)
        self.assertEqual(row['snapshot_hash'], 'd' * 64)
        self.assertEqual(row['drift_status'], 'current')
        self.assertEqual(row['revision'], 3)
        self.assertEqual(row['step_count'], 2)
        self.assertIn(ai_read.UNTRUSTED_CONTENT_BEGIN, row['procedure_name'])

    def test_no_application_is_none_not_a_guess(self):
        bare = WorkOrder.objects.create(
            title='No procedure here',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
        )
        self.assertIsNone(ai_read.work_order_procedure_application(bare))


class StepExecutionProjectionTests(ProcedureReaderTestCase):
    """Stage 2: every step, statuses verbatim, identities and readings out."""

    def test_complete_population_ordered_and_fenced(self):
        result = ai_read.work_order_step_executions(self.application)
        self.assertTrue(result['complete_population'])
        self.assertEqual(result['population_count'], 2)
        first, second = result['steps']
        self.assertEqual(first['sequence'], 1)
        self.assertEqual(first['status'], 'completed')
        self.assertTrue(first['passed'])
        self.assertFalse(first['completed'])  # no completed_at recorded
        self.assertIn(ai_read.UNTRUSTED_CONTENT_BEGIN, first['note'])
        self.assertEqual(second['status'], 'pending')
        self.assertIsNone(second['note'])

    def test_identities_and_readings_never_appear(self):
        blob = json.dumps(ai_read.work_order_step_executions(self.application))
        self.assertNotIn(HIDDEN_TECH, blob)
        self.assertNotIn(HIDDEN_READING, blob)


class DeviationProjectionTests(ProcedureReaderTestCase):
    """Stage 3: explicit deviations with honest coverage."""

    def test_deviations_project_coordinates_and_fenced_text(self):
        result = ai_read.work_order_deviations(self.work_order)
        self.assertEqual(result['population_count'], 1)
        self.assertTrue(result['complete_population'])
        entry = result['deviations'][0]
        self.assertEqual(entry['category'], 'step_not_applicable')
        self.assertEqual(entry['application_key'], str(self.application.pk))
        self.assertEqual(entry['step_key'], str(self.step_open.step_key))
        self.assertIn(ai_read.UNTRUSTED_CONTENT_BEGIN, entry['reason'])
        self.assertIn('torque_nm', entry['expected'])
        self.assertFalse(entry['approved'])

    def test_actor_identity_never_appears(self):
        blob = json.dumps(ai_read.work_order_deviations(self.work_order))
        self.assertNotIn(HIDDEN_TECH, blob)

    def test_exclusions_are_documented(self):
        for key in (
            'WorkOrderStepExecution.completed_by',
            'WorkOrderStepExecution.value',
            'WorkOrderDeviation.actor',
            'WorkOrderDeviation.approval',
        ):
            self.assertIn(key, ai_read.EXCLUDED_FIELDS)
