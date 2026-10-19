"""P18: the overview lists recorded AIMMS digital work and nothing else."""

import datetime
import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from InvenTree.unit_test import InvenTreeAPITestCase
from repair.models import RepairPacket, RepairPacketEvent, RepairPacketGenerationRun
from tasks.closeout_models import (
    CloseoutCapture,
    CloseoutCaptureRevision,
    CloseoutEffect,
    CloseoutProposal,
)
from tasks.models import WorkOrder
from tasks.workorder_models import WorkOrderCloseout, WorkOrderEvent

SCOPE = 'tasks.scope.single_site_scope_resolver'


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=SCOPE)
class HandledActionsTest(InvenTreeAPITestCase):
    """``handled_actions.v1`` mirrors saved records, counts them and writes nothing."""

    roles = 'all'

    def setUp(self):
        """One packet-owned work order with recorded preparation runs."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name='Handled machine', client=get_default_client()
        )
        self.work_order = WorkOrder.objects.create(
            reference='WO-HANDLED-1', title='Handled work', machine=self.machine
        )
        self.other = WorkOrder.objects.create(
            reference='WO-HANDLED-2', title='Other work', machine=self.machine
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Bearing noise',
            criticality='high',
        )
        self.technician = get_user_model().objects.create_user(
            username='handled-tech', first_name='Riley', last_name='Field'
        )

    def overview(self):
        """Read the overview the Work page renders."""
        return self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work_order.pk}),
            expected_code=200,
        ).data['handled_actions']

    def record_run(self, status, **extra):
        """Record one preparation run."""
        return RepairPacketGenerationRun.objects.create(
            packet=self.packet,
            agent_run_id=uuid.uuid4().hex,
            provider='heuristic',
            status=status,
            **extra,
        )

    def test_standalone_work_reports_its_sources_not_an_empty_success(self):
        """Without a packet or closeout surface the response says what was read."""
        self.packet.delete()
        handled = self.overview()
        self.assertEqual(handled['rule'], 'handled_actions.v1')
        self.assertEqual(handled['actions'], [])
        self.assertEqual(
            handled['counts'], {'completed': 0, 'active': 0, 'failed': 0, 'unknown': 0}
        )
        self.assertEqual(
            handled['sources'],
            [
                {'key': 'packet_preparation', 'state': 'not_applicable'},
                {'key': 'closeout_drafting', 'state': 'not_enabled'},
                {'key': 'closeout_follow_up', 'state': 'not_enabled'},
            ],
        )

    def test_packet_runs_and_created_work_are_rows_with_matching_counts(self):
        """Succeeded, running and failed runs keep their own state; no confidence."""
        finished = timezone.now() - datetime.timedelta(hours=2)
        succeeded = self.record_run(
            'succeeded',
            finished_at=finished,
            result_summary={
                'provider': 'heuristic',
                'confidence': 0.91,
                'parts': 3,
                'gates_created': 2,
                'thread_id': 'repair-1',
                'turn_id': 'turn-secret',
            },
        )
        self.record_run(
            'failed', finished_at=finished, error='Traceback: provider down'
        )
        self.record_run('running')
        RepairPacketEvent.objects.create(
            packet=self.packet,
            event_type='work_order_created',
            actor=self.technician,
            metadata={'work_order_id': self.work_order.pk, 'run_id': 'r1'},
        )
        # A packet event naming another work order is not this work's action.
        RepairPacketEvent.objects.create(
            packet=self.packet,
            event_type='work_order_created',
            metadata={'work_order_id': self.other.pk, 'run_id': 'r2'},
        )

        handled = self.overview()
        self.assertEqual(
            handled['counts'], {'completed': 2, 'active': 1, 'failed': 1, 'unknown': 0}
        )
        self.assertEqual(len(handled['actions']), 4)
        prepared = next(
            row
            for row in handled['actions']
            if row['id'] == f'packet-run:{succeeded.pk}'
        )
        self.assertEqual(prepared['kind'], 'packet_prepared')
        self.assertEqual(prepared['state'], 'completed')
        self.assertEqual(prepared['result'], {'parts': 3, 'gates_created': 2})
        self.assertEqual(
            prepared['link'],
            {
                'kind': 'repair_packet',
                'id': self.packet.pk,
                'reference': self.packet.reference,
            },
        )
        failed = next(row for row in handled['actions'] if row['status'] == 'failed')
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['result'])
        self.assertNotIn('Traceback', str(handled))
        self.assertNotIn('turn-secret', str(handled))
        self.assertNotIn('0.91', str(handled))
        created = next(
            row for row in handled['actions'] if row['kind'] == 'work_order_prepared'
        )
        self.assertEqual(created['trigger'], {'requested_by': 'Riley Field'})
        self.assertEqual(created['link'], {'kind': 'work_panel', 'panel': 'parts'})

    @override_settings(
        AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True
    )
    def test_closeout_drafting_and_follow_ups_keep_their_recorded_outcome(self):
        """Effects map pending/unknown/failed/succeeded without upgrading any."""
        self.packet.delete()
        capture = CloseoutCapture.objects.create(
            work_order=self.work_order, created_by=self.technician, status='consumed'
        )
        revision = CloseoutCaptureRevision.objects.create(
            capture=capture,
            revision=1,
            narrative='Replaced the seal and checked for leaks.',
            source_content_hash='a' * 64,
            work_order_version=1,
            created_by=self.technician,
        )
        CloseoutProposal.objects.create(
            capture_revision=revision,
            schema_version=1,
            extractor='rules-v1',
            fields={'action': {}, 'result': {}},
            part_candidates=[{'text': 'seal'}],
            reading_candidates=[],
            content_hash='b' * 64,
            status='reviewed',
        )
        extracting = CloseoutCapture.objects.create(
            work_order=self.other, created_by=self.technician, status='extracting'
        )
        closeout = WorkOrderCloseout.objects.create(
            work_order=self.work_order,
            action='Replaced seal',
            result='No leak',
            verification_summary='Visual check',
            completed_by=self.technician,
            completed_at=timezone.now(),
        )
        for effect_type, status, reference in (
            ('notification', 'succeeded', 'notified:2'),
            ('memory_draft', 'outcome_unknown', ''),
            ('notification', 'retryable', ''),
            ('notification', 'abandoned', ''),
        ):
            CloseoutEffect.objects.create(
                closeout=closeout,
                effect_type=effect_type,
                effect_key=f'closeout:{closeout.pk}:{effect_type}:{status}',
                payload_hash='c' * 64,
                status=status,
                attempts=3 if status == 'abandoned' else 1,
                last_error='Internal executor stack' if status != 'succeeded' else '',
                result_reference=reference,
            )

        handled = self.overview()
        self.assertEqual(
            [source['state'] for source in handled['sources']],
            ['not_applicable', 'read', 'read'],
        )
        self.assertEqual(
            handled['counts'], {'completed': 2, 'active': 1, 'failed': 1, 'unknown': 1}
        )
        kinds = {row['id']: row for row in handled['actions']}
        drafted = next(
            row for row in kinds.values() if row['kind'] == 'account_drafted'
        )
        self.assertEqual(drafted['status'], 'reviewed')
        self.assertEqual(
            drafted['result'],
            {
                'capture_revision': 1,
                'fields': 2,
                'part_candidates': 1,
                'reading_candidates': 0,
            },
        )
        notified = next(
            row
            for row in kinds.values()
            if row['kind'] == 'closeout_notification' and row['state'] == 'completed'
        )
        self.assertEqual(notified['result'], {'notified': 2})
        unknown = next(row for row in kinds.values() if row['state'] == 'unknown')
        self.assertEqual(unknown['kind'], 'learning_drafted')
        self.assertIsNone(unknown['result'])
        abandoned = next(row for row in kinds.values() if row['status'] == 'abandoned')
        self.assertEqual((abandoned['state'], abandoned['attempts']), ('failed', 3))
        self.assertNotIn('Internal executor stack', str(handled))
        # Another work order's extraction is not this work's action.
        self.assertNotIn(f'closeout-capture:{extracting.pk}', kinds)

    def test_opening_the_overview_prepares_nothing(self):
        """Reading the Work page leaves every automation ledger unchanged."""
        self.record_run('succeeded', finished_at=timezone.now())
        before = (
            RepairPacketGenerationRun.objects.count(),
            RepairPacketEvent.objects.count(),
            WorkOrderEvent.objects.count(),
            CloseoutEffect.objects.count(),
            CloseoutProposal.objects.count(),
        )
        self.overview()
        self.overview()
        self.packet.refresh_from_db()
        self.assertEqual(
            before,
            (
                RepairPacketGenerationRun.objects.count(),
                RepairPacketEvent.objects.count(),
                WorkOrderEvent.objects.count(),
                CloseoutEffect.objects.count(),
                CloseoutProposal.objects.count(),
            ),
        )
        self.assertEqual(self.packet.generation_status, 'idle')
