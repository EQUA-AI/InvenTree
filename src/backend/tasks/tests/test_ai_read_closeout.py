"""Scoped AI projections of retained service reports and exercise checks."""

import json
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client
from common.models import Attachment
from repair.models import PacketStatus, RepairPacket
from repair.services import close_repair_packet
from tasks import ai_read
from tasks.closeout_models import CloseoutPartUsage
from tasks.scope import MaintenanceScope, work_machine_authorized
from tasks.services.closeout_amend import verify_closeout
from tasks.services.service_reports import latest_report, service_report_command
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


@override_settings(
    **CLOSEOUT_FLAGS,
    AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
    AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor',
)
class ServiceReportProjectionTests(CloseoutEnvMixin, TestCase):
    """Use native report commands and the same packet and Work scope."""

    def setUp(self):
        """Create an executing packet and a distinct authorized reviewer."""
        self.build_env(username='projection-author')
        self.reviewer = self.make_scoped_user(
            'projection-reviewer', permissions=['review_closeout', 'verify_closeout']
        )
        self.packet = RepairPacket.objects.create(
            work_order=self.work_order,
            machine=self.machine,
            status=PacketStatus.EXECUTING,
        )
        self.work_order.description = 'NON_REPORT_DESCRIPTION'
        self.work_order.save(update_fields=['description'])
        self.account = {
            'cause': 'Recorded cooler fouling',
            'action': 'Cleaned the cooler; no replacement was requested',
            'result': 'Recorded exercise result',
            'verification_summary': 'Three prepared checks recorded',
            'follow_up_required': False,
        }

    def command(self, action, actor=None, **kwargs):
        """Submit and review through the real report service."""
        self.work_order.refresh_from_db()
        return service_report_command(
            work_order_id=self.work_order.pk,
            actor=actor or self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=str(uuid.uuid4()),
            action=action,
            **kwargs,
        )

    def approve_report(self):
        """Retain the account and approve its exact submitted revision."""
        self.command('submit', account=self.account)
        report_id = latest_report(self.work_order).pk
        self.command('approve', self.reviewer, report_id=report_id)
        self.work_order.refresh_from_db()
        return report_id

    def read(self):
        """Resolve the actor and machine scope before projecting the packet."""
        work = ai_read.authorized_work_order(self.actor, self.work_order.pk)
        self.assertIsNotNone(work)
        return ai_read.work_order_repair_state(
            work, machine_authorized=work_machine_authorized(self.actor, work)
        )

    def test_approved_report_retains_exact_account_and_parts_without_private_fields(
        self,
    ):
        """Only the approved snapshot's allow-listed fields reach the assistant."""
        CloseoutPartUsage.objects.create(
            work_order=self.work_order,
            state='reconciled',
            disposition='dismissed',
            used_quantity='0',
            candidate_text='PRIVATE-PERSON-AND-ATTACHMENT',
            resolved_by=self.actor,
        )
        report_id = self.approve_report()
        result = self.read()['service_report']
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(result['report_id'], report_id)
        for field in ('cause', 'action', 'result', 'verification_summary'):
            self.assertEqual(
                result['account'][field], ai_read.fence(self.account[field])
            )
        self.assertFalse(result['account']['follow_up_required'])
        self.assertIsNone(result['account']['downtime_minutes'])
        self.assertEqual(
            result['parts_usage'],
            [
                {
                    'state': 'reconciled',
                    'disposition': 'dismissed',
                    'used_quantity': '0.00000',
                }
            ],
        )
        blob = json.dumps(result)
        for withheld in (
            'PRIVATE-PERSON-AND-ATTACHMENT',
            'NON_REPORT_DESCRIPTION',
            'projection-author',
            'projection-reviewer',
            'resolved_by',
            'stock_item',
            'attachment',
        ):
            self.assertNotIn(withheld, blob)

    def test_missing_submitted_and_stale_reports_do_not_claim_approval(self):
        """No report or a non-current report supplies approved facts."""
        missing = self.read()['service_report']
        self.assertEqual(missing['status'], 'not_submitted')
        self.assertIsNone(missing['account'])
        self.assertIsNone(missing['parts_usage'])
        self.command('submit', account=self.account)
        self.work_order.refresh_from_db()
        submitted = self.read()['service_report']
        self.assertEqual(submitted['status'], 'submitted')
        self.assertIsNone(submitted['account'])
        self.command(
            'approve', self.reviewer, report_id=latest_report(self.work_order).pk
        )
        self.work_order.refresh_from_db()
        self.work_order.lifecycle_version += 1
        self.work_order.save(update_fields=['lifecycle_version'])
        stale = self.read()['service_report']
        self.assertEqual(stale['status'], 'stale')
        self.assertIsNone(stale['account'])
        self.assertIsNone(stale['parts_usage'])

    def test_report_narrative_and_usage_are_fenced_and_capped(self):
        """Embedded fence markers and excess retained parts cannot escape bounds."""
        self.account['action'] = (
            'Recorded: ' + ai_read.UNTRUSTED_CONTENT_END + 'x' * 1200
        )
        for _index in range(ai_read.MAX_REPORT_PARTS + 1):
            CloseoutPartUsage.objects.create(
                work_order=self.work_order, state='pending', candidate_text='hidden'
            )
        self.approve_report()
        result = self.read()['service_report']
        self.assertEqual(
            result['account']['action'],
            ai_read.fence(self.account['action'], limit=ai_read.MAX_REPORT_TEXT_CHARS),
        )
        self.assertEqual(
            result['account']['action'].count(ai_read.UNTRUSTED_CONTENT_END), 1
        )
        self.assertEqual(len(result['parts_usage']), ai_read.MAX_REPORT_PARTS)
        self.assertTrue(result['parts_usage_truncated'])
        self.assertNotIn('hidden', json.dumps(result))

    def test_closeout_verification_is_independent_of_report_approval(self):
        """A completed packet retains an approved report and separate verification."""
        self.approve_report()
        close_repair_packet(
            self.packet,
            actor=self.actor,
            closeout=self.account,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=str(uuid.uuid4()),
        )
        self.work_order.refresh_from_db()
        projected = self.read()
        self.assertEqual(projected['packet']['status'], PacketStatus.CLOSED)
        self.assertEqual(projected['service_report']['status'], 'approved')
        self.assertEqual(
            projected['closeout']['action'], ai_read.fence(self.account['action'])
        )
        self.assertFalse(projected['closeout']['verified'])
        verify_closeout(
            work_order_id=self.work_order.pk,
            actor=self.reviewer,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=str(uuid.uuid4()),
        )
        projected = self.read()
        self.assertTrue(projected['closeout']['verified'])
        self.assertEqual(projected['service_report']['status'], 'approved')

    def test_shared_work_without_machine_scope_withholds_every_packet_fact(self):
        """Shared Work is not a grant to the linked machine or its report."""
        self.approve_report()
        foreign = Client.objects.create(name='Other client', code='report-other-client')
        reader = self.make_scoped_user('report-outside-reader')
        reader.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=foreign.pk)
        }
        work = ai_read.authorized_work_order(reader, self.work_order.pk)
        self.assertIsNotNone(work)
        self.assertFalse(work_machine_authorized(reader, work))
        with (
            patch(
                'tasks.services.service_reports.report_state',
                side_effect=AssertionError,
            ),
            patch(
                'tasks.work_execution.exercise_verification.project',
                side_effect=AssertionError,
            ),
        ):
            projected = ai_read.work_order_repair_state(work, machine_authorized=False)
        self.assertIsNone(projected['packet'])
        self.assertNotIn('service_report', projected)
        self.assertNotIn('closeout', projected)
        self.assertNotIn('exercise_verification', projected)
        self.assertNotIn(self.account['cause'], json.dumps(projected))

    def test_mismatched_and_missing_packets_do_not_project_work_report(self):
        """The packet's machine must match the authorized Work machine."""
        self.approve_report()
        foreign_machine = AssetMachine.objects.create(
            name='Other machine', client=self.tenant
        )
        RepairPacket.objects.filter(pk=self.packet.pk).update(machine=foreign_machine)
        self.assertIsNone(self.read()['packet'])
        RepairPacket.objects.filter(pk=self.packet.pk).update(work_order=None)
        self.assertIsNone(self.read()['packet'])

    def test_disabled_closeout_surface_withholds_report_and_closeout(self):
        """The AI reader does not bypass the native feature boundary."""
        self.approve_report()
        with override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False):
            projected = self.read()
        self.assertIsNone(projected['service_report'])
        self.assertIsNone(projected['closeout'])


@override_settings(
    AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class ExerciseProjectionTests(TestCase):
    """Reuse the real native exercise fixture and independently check its readout."""

    def setUp(self):
        """Prepare the existing synthetic exercise in the isolated test database."""
        from tasks.tests.test_pump08_exercise import Pump08ExerciseTest

        Pump08ExerciseTest.setUp(self)

    def record(self, sequence, value, evidence=True):
        """Record an existing fixture step through its native command."""
        from tasks.tests.test_pump08_exercise import Pump08ExerciseTest

        return Pump08ExerciseTest.record(self, sequence, value, evidence=evidence)

    def read(self):
        """Project only after Work and machine authorization."""
        work = ai_read.authorized_work_order(self.actor, self.work.pk)
        self.assertIsNotNone(work)
        self.assertTrue(work_machine_authorized(self.actor, work))
        return ai_read.work_order_repair_state(work, machine_authorized=True)[
            'exercise_verification'
        ]

    def test_native_passes_and_readings_are_exercise_only(self):
        """Checks and separate windings come from the validated native projection."""
        from tasks.tests.test_pump08_exercise import Pump08ExerciseTest
        from tasks.work_execution.exercise_verification import project

        Pump08ExerciseTest.all_pass(self)
        native = project(self.work, self.actor)
        events_before = list(self.work.events.values())
        projected = self.read()
        self.assertEqual(list(self.work.events.values()), events_before)
        self.assertTrue(projected['approved'])
        self.assertEqual(projected['passed_count'], native['passed_count'])
        self.assertEqual(projected['required_count'], native['required_count'])
        self.assertEqual(len(projected['checks']), len(native['checks']))
        for actual, source in zip(projected['checks'], native['checks'], strict=True):
            self.assertEqual(actual['recorded'], source['recorded'])
            self.assertEqual(actual['passed'], source['passed'])
            self.assertEqual(actual['value'], source['value'])
            if 'readings' in source:
                self.assertEqual(actual['readings'], source['readings'])
        self.assertEqual(projected['provenance'], native['provenance'])
        self.assertTrue(projected['software_exercise_only'])
        self.assertFalse(projected['physical_authority'])
        self.assertFalse(projected['grants_operating_permission'])
        self.assertFalse(projected['grants_technical_acceptance'])
        blob = json.dumps(projected)
        for withheld in (
            'exercise-author',
            'recorded_by',
            'evidence_ids',
            'attachment',
            'note',
        ):
            self.assertNotIn(withheld, blob)

    def test_incomplete_or_invalid_evidence_is_never_a_pass(self):
        """Unrecorded checks are unknown and deleted evidence invalidates a pass."""
        from tasks.tests.test_pump08_exercise import Pump08ExerciseTest
        from tasks.work_execution.exercise_verification import project

        incomplete = self.read()
        self.assertFalse(incomplete['approved'])
        self.assertTrue(all(check['passed'] is None for check in incomplete['checks']))
        self.assertTrue(all(check['value'] is None for check in incomplete['checks']))
        Pump08ExerciseTest.all_pass(self)
        attachment_id = project(self.work, self.actor)['evidence'][0]['id']
        Attachment.objects.filter(pk=attachment_id).delete()
        self.assertFalse(self.read()['approved'])

    def test_disabled_or_unavailable_source_stays_unknown(self):
        """A disabled procedure surface or missing source cannot invent results."""
        with override_settings(AIMMS_PROCEDURES_ENABLED=False):
            self.assertIsNone(self.read())
        self.work.anomalies.clear()
        projected = self.read()
        self.assertIsNone(projected['approved'])
        self.assertIsNone(projected['passed_count'])
        self.assertIsNone(projected['checks'])
        self.assertFalse(projected['physical_authority'])

    def test_native_exercise_cannot_use_a_different_packet(self):
        """A stale native reverse relation cannot replace the canonical packet."""
        other_packet = RepairPacket.objects.create(machine=self.machine)
        with (
            patch('tasks.work_execution.common.packet_of', return_value=other_packet),
            patch(
                'tasks.work_execution.exercise_verification.project',
                side_effect=AssertionError,
            ),
        ):
            self.assertIsNone(self.read())
