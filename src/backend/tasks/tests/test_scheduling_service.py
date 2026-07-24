"""Service-level tests for the scheduling command service (S5)."""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from assets.models import AssetMachine
from tasks.models import (
    KanbanCard,
    WorkOrderCommand,
    WorkOrderDeletionRecord,
    WorkOrderEvent,
    WorkOrderLifecycle,
)
from tasks.services import scheduling


def _utc(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=dt_timezone.utc)


def _same_instant(stored, expected):
    """Compare ignoring tzinfo: this project sets USE_TZ = not TESTING, so stored
    datetimes are naive under test and aware in production."""
    return (
        stored is not None
        and (stored.year, stored.month, stored.day, stored.hour, stored.minute)
        == (
            expected.year,
            expected.month,
            expected.day,
            expected.hour,
            expected.minute,
        )
    )


class SchedulingServiceTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='sched-actor', email='s@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='Svc Press')

    def _card(self, **kw):
        return KanbanCard.objects.create(
            title=kw.pop('title', 'WO'),
            status='backlog',
            priority='low',
            machine=self.machine,
            **kw,
        )

    # ── create ────────────────────────────────────────────────

    def test_create_makes_a_card_and_records_a_created_event(self):
        result = scheduling.create_work_order(
            actor=self.actor,
            idempotency_key='c1',
            title='New WO',
            machine_id=self.machine.pk,
        )

        card = KanbanCard.objects.get(pk=result.work_order_id)
        self.assertEqual(card.title, 'New WO')
        self.assertEqual(card.machine, self.machine)
        self.assertTrue(
            WorkOrderEvent.objects.filter(
                work_order=card, event_type='CREATED'
            ).exists()
        )

    def test_create_is_idempotent(self):
        first = scheduling.create_work_order(
            actor=self.actor, idempotency_key='c2', title='Once',
            machine_id=self.machine.pk,
        )
        second = scheduling.create_work_order(
            actor=self.actor, idempotency_key='c2', title='Once',
            machine_id=self.machine.pk,
        )

        self.assertEqual(first.work_order_id, second.work_order_id)
        self.assertEqual(KanbanCard.objects.filter(title='Once').count(), 1)

    def test_create_idempotency_conflict_on_different_request(self):
        scheduling.create_work_order(
            actor=self.actor, idempotency_key='c3', title='A',
            machine_id=self.machine.pk,
        )
        with self.assertRaises(scheduling.IdempotencyConflict):
            scheduling.create_work_order(
                actor=self.actor, idempotency_key='c3', title='B',
                machine_id=self.machine.pk,
            )

    # ── schedule (move) ───────────────────────────────────────

    def test_schedule_sets_the_window_and_bumps_version(self):
        card = self._card()
        version = card.lifecycle_version

        result = scheduling.schedule_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=version,
            idempotency_key='s1',
            scheduled_start=_utc(2026, 8, 3, 9),
            scheduled_end=_utc(2026, 8, 3, 13),
        )

        card.refresh_from_db()
        self.assertTrue(_same_instant(card.scheduled_start, _utc(2026, 8, 3, 9)))
        self.assertEqual(card.lifecycle_version, version + 1)
        self.assertEqual(result.lifecycle_version, version + 1)

    def test_schedule_rejects_inverted_window(self):
        card = self._card()
        with self.assertRaises(scheduling.InvalidSchedule):
            scheduling.schedule_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='s2',
                scheduled_start=_utc(2026, 8, 3, 13),
                scheduled_end=_utc(2026, 8, 3, 9),
            )

    def test_schedule_rejects_stale_version(self):
        card = self._card()
        with self.assertRaises(scheduling.StaleVersion):
            scheduling.schedule_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version + 5,
                idempotency_key='s3',
                scheduled_start=_utc(2026, 8, 3, 9),
                scheduled_end=_utc(2026, 8, 3, 13),
            )

    def test_schedule_is_idempotent(self):
        card = self._card()
        args = dict(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='s4',
            scheduled_start=_utc(2026, 8, 3, 9),
            scheduled_end=_utc(2026, 8, 3, 13),
        )
        first = scheduling.schedule_work_order(**args)
        second = scheduling.schedule_work_order(**args)

        self.assertEqual(first.event_id, second.event_id)
        card.refresh_from_db()
        # Version bumped exactly once despite two identical calls.
        self.assertEqual(card.lifecycle_version, 2)

    def test_schedule_refuses_completed_work(self):
        card = self._card(lifecycle_status=WorkOrderLifecycle.COMPLETED)
        with self.assertRaises(scheduling.NotMutable):
            scheduling.schedule_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='s5',
                scheduled_start=_utc(2026, 8, 3, 9),
                scheduled_end=_utc(2026, 8, 3, 13),
            )

    # ── resize ────────────────────────────────────────────────

    def test_resize_sets_duration(self):
        card = self._card()
        scheduling.resize_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='r1',
            estimated_minutes=240,
        )
        card.refresh_from_db()
        self.assertEqual(card.estimated_minutes, 240)

    def test_resize_requires_something_to_change(self):
        card = self._card()
        with self.assertRaises(scheduling.WorkOrderCommandError):
            scheduling.resize_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='r2',
            )

    def test_resize_rejects_negative_duration(self):
        card = self._card()
        with self.assertRaises(scheduling.InvalidSchedule):
            scheduling.resize_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='r3',
                estimated_minutes=-5,
            )

    # ── update plan ───────────────────────────────────────────

    def test_update_plan_changes_allowed_fields(self):
        card = self._card()
        other = AssetMachine.objects.create(name='Other Svc')
        scheduling.update_work_order_plan(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='u1',
            fields={'title': 'Renamed', 'priority': 'high', 'machine_id': other.pk},
        )
        card.refresh_from_db()
        self.assertEqual(card.title, 'Renamed')
        self.assertEqual(card.priority, 'high')
        self.assertEqual(card.machine, other)

    def test_update_plan_ignores_unknown_fields(self):
        card = self._card()
        with self.assertRaises(scheduling.WorkOrderCommandError):
            scheduling.update_work_order_plan(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='u2',
                fields={'lifecycle_status': 'completed'},
            )
        card.refresh_from_db()
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.DRAFT)

    # ── delete (governed) ─────────────────────────────────────

    def test_delete_removes_card_but_keeps_audit(self):
        card = self._card(title='To Delete')
        card_pk = card.pk

        result = scheduling.delete_work_order(
            work_order_id=card_pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='d1',
            reason='obsolete',
        )

        self.assertFalse(KanbanCard.objects.filter(pk=card_pk).exists())
        record = WorkOrderDeletionRecord.objects.get(pk=result.deletion_record_id)
        self.assertEqual(record.work_order_pk, card_pk)
        self.assertEqual(record.title, 'To Delete')
        self.assertEqual(record.reason, 'obsolete')
        self.assertEqual(record.actor, self.actor)
        self.assertEqual(record.snapshot['title'], 'To Delete')

    def test_delete_audit_survives_and_answers_who_and_what(self):
        card = self._card(title='Forensic')
        card_pk = card.pk
        scheduling.delete_work_order(
            work_order_id=card_pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='d2',
            reason='mistake',
        )
        # The machine outlives the card too (SET_NULL, not cascade).
        record = WorkOrderDeletionRecord.objects.get(work_order_pk=card_pk)
        self.assertEqual(record.machine, self.machine)

    def test_delete_rejects_stale_version(self):
        card = self._card()
        with self.assertRaises(scheduling.StaleVersion):
            scheduling.delete_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version + 9,
                idempotency_key='d3',
            )
        self.assertTrue(KanbanCard.objects.filter(pk=card.pk).exists())

    # ── batch ─────────────────────────────────────────────────

    def test_batch_applies_all_operations(self):
        a = self._card(title='A')
        b = self._card(title='B')

        results = scheduling.apply_schedule_batch(
            actor=self.actor,
            idempotency_key='b1',
            operations=[
                {
                    'card_id': a.pk,
                    'expected_version': a.lifecycle_version,
                    'scheduled_start': _utc(2026, 8, 3, 9),
                    'scheduled_end': _utc(2026, 8, 3, 12),
                },
                {
                    'card_id': b.pk,
                    'expected_version': b.lifecycle_version,
                    'scheduled_start': _utc(2026, 8, 4, 9),
                    'scheduled_end': _utc(2026, 8, 4, 12),
                },
            ],
        )

        self.assertEqual(len(results), 2)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertTrue(_same_instant(a.scheduled_start, _utc(2026, 8, 3, 9)))
        self.assertTrue(_same_instant(b.scheduled_start, _utc(2026, 8, 4, 9)))

    def test_batch_is_all_or_nothing(self):
        a = self._card(title='A')
        b = self._card(title='B')

        with self.assertRaises(scheduling.StaleVersion):
            scheduling.apply_schedule_batch(
                actor=self.actor,
                idempotency_key='b2',
                operations=[
                    {
                        'card_id': a.pk,
                        'expected_version': a.lifecycle_version,
                        'scheduled_start': _utc(2026, 8, 3, 9),
                        'scheduled_end': _utc(2026, 8, 3, 12),
                    },
                    {
                        'card_id': b.pk,
                        'expected_version': b.lifecycle_version + 9,  # stale
                        'scheduled_start': _utc(2026, 8, 4, 9),
                        'scheduled_end': _utc(2026, 8, 4, 12),
                    },
                ],
            )

        # First op must have rolled back with the second's failure.
        a.refresh_from_db()
        self.assertIsNone(a.scheduled_start)


class SchedulingDeleteProtectedTest(TestCase):
    """A card referenced by a protected record cannot be hard-deleted."""

    def test_protected_error_becomes_a_clear_command_error(self):
        from tasks.closeout_models import CloseoutCapture

        actor = get_user_model().objects.create_user(
            username='prot-actor', email='p@example.com', password='pw'
        )
        machine = AssetMachine.objects.create(name='Prot Press')
        card = KanbanCard.objects.create(
            title='Protected', status='backlog', priority='low', machine=machine
        )
        # CloseoutCapture.work_order is on_delete=PROTECT.
        CloseoutCapture.objects.create(work_order=card, created_by=actor)

        with self.assertRaises(scheduling.ProtectedWorkOrder):
            scheduling.delete_work_order(
                work_order_id=card.pk,
                actor=actor,
                expected_version=card.lifecycle_version,
                idempotency_key='dp1',
            )

        self.assertTrue(KanbanCard.objects.filter(pk=card.pk).exists())
