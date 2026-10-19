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
        self.actor = get_user_model().objects.create_user(username='assignment-recorder')
        self.work = WorkOrder.objects.create(title='Time fixture', assigned_to=self.actor, lifecycle_status='on_hold')
        self.event = WorkOrderEvent.objects.create(work_order=self.work, event_type='ASSIGNED', actor=self.actor, reason='Recorded specialist allocation', correlation_id=uuid.uuid4(), metadata={'assigned_to_id': self.actor.pk, 'previous_assigned_to_id': None})

    def test_elapsed_is_anchored_to_matching_assignment_at_server_read(self):
        """Only the exact assignment event anchors static elapsed wall time."""
        observed = self.event.created_at + datetime.timedelta(minutes=90)
        with patch('tasks.work_execution.time_assignment.timezone.now', return_value=observed):
            value = project(self.work)
        self.assertEqual(value['elapsed_assignment_seconds'], 5400)
        self.assertEqual(value['assignment_reason'], 'Recorded specialist allocation')
        self.assertEqual(value['assignment_recorded_by'], 'assignment-recorder')
        self.assertFalse(value['attributed_intervals']['available'])
        self.assertEqual(value['coverage'], 'unknown')
        self.assertNotIn('waiting_minutes', value)
        self.assertNotIn('labor_minutes', value)

    def test_legacy_assignment_has_no_invented_anchor(self):
        """Creation or actual-start time is not an assignment timestamp."""
        legacy = WorkOrder.objects.create(title='Legacy', assignee='Recorded legacy label', actual_started_at=timezone.now())
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
        for target in (True, str(self.actor.pk)):
            self.event.metadata['assigned_to_id'] = target
            self.event.save(update_fields=['metadata'])
            self.assertIsNone(project(self.work)['elapsed_assignment_seconds'])
        self.event.metadata['assigned_to_id'] = self.actor.pk
        self.event.save(update_fields=['metadata'])
        with patch('tasks.work_execution.time_assignment.timezone.now', return_value=self.event.created_at - datetime.timedelta(seconds=1)):
            self.assertIsNone(project(self.work)['elapsed_assignment_seconds'])

    def test_blank_reason_does_not_generate_factors_or_handover(self):
        """Assignment and reading a page cannot accept incoming responsibility."""
        self.event.reason = ''
        self.event.save(update_fields=['reason'])
        value = project(self.work)
        self.assertIsNone(value['assignment_reason'])
        for key in ('assignment_factors', 'accepted_handover', 'correction'):
            self.assertFalse(value[key]['available'])

    def test_read_does_not_create_assignment_or_time_records(self):
        """Repeated reads leave the work and audit events untouched."""
        version = self.work.lifecycle_version
        project(self.work)
        project(self.work)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_version, version)
        self.assertEqual(self.work.events.count(), 1)
