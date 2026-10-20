"""P27: a recorded assignment cannot substitute for a labor interval."""

import datetime
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tasks.models import WorkOrder, WorkOrderEvent
from tasks.work_execution.time_assignment import project


class TimeAssignmentProjectionTest(TestCase):
    """Read assignment provenance and explicit unknown interval coverage."""

    def setUp(self):
        """Create a typed current assignee and a recorded assignment event."""
        self.actor = get_user_model().objects.create_user(
            username='assignment-recorder'
        )
        self.work = WorkOrder.objects.create(
            title='Time fixture', assigned_to=self.actor, lifecycle_status='on_hold'
        )
        self.event = WorkOrderEvent.objects.create(
            work_order=self.work,
            event_type='ASSIGNED',
            actor=self.actor,
            reason='Recorded specialist allocation',
            correlation_id=uuid.uuid4(),
            metadata={'assigned_to_id': self.actor.pk, 'previous_assigned_to_id': None},
        )

    def test_elapsed_is_anchored_to_matching_assignment_at_server_read(self):
        """Only the exact assignment event anchors static elapsed wall time."""
        observed = self.event.created_at + datetime.timedelta(minutes=90)
        with patch(
            'tasks.work_execution.time_assignment.timezone.now', return_value=observed
        ):
            value = project(self.work)
        self.assertEqual(value['elapsed_assignment_seconds'], 5400)
        self.assertEqual(value['assignment_reason'], 'Recorded specialist allocation')
        self.assertEqual(value['assignment_recorded_by'], 'assignment-recorder')
        self.assertTrue(value['attributed_intervals']['available'])
        self.assertEqual(value['coverage'], 'unknown')
        self.assertNotIn('waiting_minutes', value)
        self.assertNotIn('labor_minutes', value)

    def test_legacy_assignment_has_no_invented_anchor(self):
        """Creation or actual-start time is not an assignment timestamp."""
        legacy = WorkOrder.objects.create(
            title='Legacy',
            assignee='Recorded legacy label',
            actual_started_at=timezone.now(),
        )
        value = project(legacy)
        self.assertIsNone(value['elapsed_assignment_seconds'])
        self.assertIsNone(value['assignment_event_at'])
        self.assertEqual(value['legacy_assignee'], 'Recorded legacy label')

    def test_mismatched_current_assignee_does_not_borrow_old_event(self):
        """A direct legacy edit cannot reuse someone else's assignment clock."""
        self.work.assigned_to = None
        self.work.save(update_fields=['assigned_to'])
        self.assertIsNone(project(self.work)['elapsed_assignment_seconds'])

    def test_latest_malformed_or_future_event_does_not_fall_back(self):
        """Invalid latest provenance keeps elapsed time unknown."""
        earlier = WorkOrderEvent.objects.create(
            work_order=self.work,
            event_type='ASSIGNED',
            actor=self.actor,
            correlation_id=uuid.uuid4(),
            metadata={'assigned_to_id': self.actor.pk},
        )
        WorkOrderEvent.objects.filter(pk=earlier.pk).update(
            created_at=self.event.created_at - datetime.timedelta(hours=1)
        )
        for target in (True, str(self.actor.pk)):
            with self.subTest(target=target):
                self.event.metadata['assigned_to_id'] = target
                self.event.save(update_fields=['metadata'])
                value = project(self.work)
                self.assertIsNone(value['elapsed_assignment_seconds'])
                self.assertIsNone(value['assignment_event_at'])
        self.event.metadata['assigned_to_id'] = self.actor.pk
        self.event.save(update_fields=['metadata'])
        with patch(
            'tasks.work_execution.time_assignment.timezone.now',
            return_value=self.event.created_at - datetime.timedelta(seconds=1),
        ):
            value = project(self.work)
            self.assertIsNone(value['elapsed_assignment_seconds'])
            self.assertIsNone(value['assignment_event_at'])

    def test_assignment_history_resolves_both_names_in_one_batch(self):
        """Audit targets use recorded native identities without one lookup per event."""
        previous = get_user_model().objects.create_user(
            username='previous-assignee', first_name='Earlier', last_name='Technician'
        )
        self.actor.first_name = 'Current'
        self.actor.last_name = 'Technician'
        self.actor.save(update_fields=['first_name', 'last_name'])
        self.event.metadata['previous_assigned_to_id'] = previous.pk
        self.event.save(update_fields=['metadata'])
        WorkOrderEvent.objects.create(
            work_order=self.work,
            event_type='ASSIGNED',
            actor=self.actor,
            correlation_id=uuid.uuid4(),
            metadata={'assigned_to_id': previous.pk},
        )
        with self.assertNumQueries(4):
            rows = {row['id']: row for row in project(self.work)['events']}
        self.assertEqual(rows[self.event.pk]['assigned_to_name'], 'Current Technician')
        self.assertEqual(
            rows[self.event.pk]['previous_assigned_to_name'], 'Earlier Technician'
        )

    def test_missing_or_malformed_assignment_names_are_not_guessed(self):
        """A bool, text id or deleted target does not name an unrelated person."""
        for target in (True, str(self.actor.pk), self.actor.pk + 100000):
            with self.subTest(target=target):
                self.event.metadata['previous_assigned_to_id'] = target
                self.event.save(update_fields=['metadata'])
                self.assertIsNone(
                    project(self.work)['events'][0]['previous_assigned_to_name']
                )

    def test_blank_reason_does_not_generate_factors_or_handover(self):
        """Assignment and reading a page cannot accept incoming responsibility."""
        self.event.reason = ''
        self.event.save(update_fields=['reason'])
        value = project(self.work)
        self.assertIsNone(value['assignment_reason'])
        for key in ('assignment_factors', 'accepted_handover'):
            self.assertFalse(value[key]['available'])

    def test_read_does_not_create_assignment_or_time_records(self):
        """Repeated reads leave the work and audit events untouched."""
        version = self.work.lifecycle_version
        project(self.work)
        project(self.work)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_version, version)
        self.assertEqual(self.work.events.count(), 1)
        self.assertEqual(self.work.time_intervals.count(), 0)
        self.assertEqual(self.work.downtime_records.count(), 0)
