"""Lifecycle tests for replay, stop and cleanup (work packages F/H)."""

import datetime as dt
import tempfile
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import WorkOrder, WorkOrderEvent
from tasks.scope import MaintenanceScope

from aimms_testing import requires_postgres
from assets.demo_metrics import apply_service, cleanup, cli, replay
from assets.demo_metrics.demo_test_support import DemoMetricsEnvMixin
from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsObject,
    DemoMetricsSession,
)
from assets.health_models import MachineSignalState


@requires_postgres
class ReplayFeedTest(DemoMetricsEnvMixin, TestCase):
    """Bounded replay: allowed aliases only, receipts, real timestamps."""

    def setUp(self):
        """Apply the demo and load the replayed session row."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()

    def state(self, alias, signal):
        """Return the live signal state for one alias/signal pair."""
        return MachineSignalState.objects.get(
            binding__external_key=f'equa-demo-metrics-v1/{self.session.session_key}/{alias}/{signal}'
        )

    def test_replay_emits_new_samples_only_for_allowed_aliases(self):
        """Emit new samples only for the replay-authorized aliases."""
        before = self.state('A01', 'bearing_temperature').observed_at
        later = before + dt.timedelta(seconds=60)
        result = replay.replay_batch(
            self.session, self.actor, batch_key='batch-1', now=later
        )
        self.assertEqual(result['accepted'], 9)
        self.assertEqual(result['aliases'], ['A01', 'A02', 'B01'])
        after = self.state('A01', 'bearing_temperature').observed_at
        self.assertEqual(after, replay._aware(later))
        # B02 stays never seen; B03 stays unconfigured.
        self.assertFalse(
            MachineSignalState.objects.filter(
                binding__external_key__contains='/B02/'
            ).exists()
        )
        self.assertFalse(
            self.session.machines.get(alias='B03').machine.signal_bindings.exists()
        )

    def test_exact_replay_returns_stored_receipts_without_new_effects(self):
        """Return stored receipts without new effects on exact replay."""
        # Exact replay: same batch key and the same canonical payload — the
        # emitted timestamp is part of the payload identity.
        now = timezone.now()
        first = replay.replay_batch(
            self.session, self.actor, batch_key='batch-1', now=now
        )
        observed = self.state('A01', 'bearing_temperature').observed_at
        second = replay.replay_batch(
            self.session, self.actor, batch_key='batch-1', now=now
        )
        self.assertEqual(first['accepted'], 9)
        self.assertEqual(second['accepted'], 0)
        self.assertEqual(second['replayed'], 9)
        self.assertEqual(self.state('A01', 'bearing_temperature').observed_at, observed)

    def test_changed_payload_on_the_same_batch_key_is_a_conflict(self):
        """Raise a conflict when a batch key carries a changed payload."""
        now = timezone.now()
        replay.replay_batch(self.session, self.actor, batch_key='batch-1', now=now)
        with self.assertRaises(apply_service.ApplyError) as caught:
            replay.replay_batch(
                self.session,
                self.actor,
                batch_key='batch-1',
                now=now,
                values={('A01', 'bearing_temperature'): 1.0},
            )
        self.assertEqual(caught.exception.code, 'RECEIPT_CONFLICT')

    def test_changed_timestamp_on_the_same_batch_key_is_a_conflict(self):
        """Raise a conflict when a batch key carries a changed timestamp."""
        # The timestamp is part of the canonical payload identity: the same
        # key with a different emitted timestamp is a changed payload.
        now = timezone.now()
        replay.replay_batch(self.session, self.actor, batch_key='batch-1', now=now)
        with self.assertRaises(apply_service.ApplyError) as caught:
            replay.replay_batch(
                self.session,
                self.actor,
                batch_key='batch-1',
                now=now + dt.timedelta(hours=1),
            )
        self.assertEqual(caught.exception.code, 'RECEIPT_CONFLICT')

    def test_repeated_start_does_not_establish_a_second_feed(self):
        """Refuse to establish a second feed on repeated start."""
        first = replay.start_feed(self.session, self.actor)
        second = replay.start_feed(self.session, self.actor)
        self.assertTrue(first['started'])
        self.assertTrue(second['already_started'])
        self.assertEqual(
            self.session.receipts.filter(item_key=replay.FEED_CLAIM_KEY).count(), 1
        )

    def test_feed_claim_is_bound_to_the_starting_operator(self):
        """Bind the feed claim to the operator who started it."""
        other = get_user_model().objects.create_superuser(
            username='other-feed-operator', email='f@example.com', password='pw'
        )
        # The second operator holds current scope over the session machines:
        # the FEED_CONFLICT contract is for genuinely authorized operators —
        # a scope-denied actor uniformly receives ACTOR_SCOPE instead.
        other.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_tenant.pk
            )
        }
        replay.start_feed(self.session, self.actor)
        with self.assertRaises(replay.ReplayError) as caught:
            replay.start_feed(self.session, other)
        self.assertEqual(caught.exception.code, 'FEED_CONFLICT')

    def test_run_replay_never_runs_a_second_feed(self):
        """Refuse to run a second feed loop after the first run."""
        clock = {'now': timezone.now()}
        sleeps = []

        def now_fn():
            clock['now'] += dt.timedelta(seconds=5)
            return clock['now']

        def sleep_fn(seconds):
            sleeps.append(seconds)

        first = replay.run_replay(
            self.session,
            self.actor,
            interval_seconds=10,
            max_duration_seconds=35,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        self.assertGreaterEqual(first['batches'], 1)
        receipts_after_first = self.session.receipts.count()
        second = replay.run_replay(
            self.session,
            self.actor,
            interval_seconds=10,
            max_duration_seconds=35,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        self.assertTrue(second['already_started'])
        self.assertEqual(second['batches'], 0)
        self.assertEqual(self.session.receipts.count(), receipts_after_first)

    def test_stop_revokes_replay_and_serializes_with_batches(self):
        """Revoke replay after stop and persist the stopped status."""
        replay.stop_session(self.session, self.actor)
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'SESSION_STOPPED')
        self.assertEqual(
            DemoMetricsSession.objects.get(pk=self.session.pk).status,
            DemoMetricsSession.Status.STOPPED,
        )

    def test_expired_session_refuses_replay(self):
        """Refuse replay once the session expiry has passed."""
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        session.expires_at = timezone.now() - dt.timedelta(minutes=1)
        session.save(update_fields=['expires_at'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')

    def test_run_replay_is_bounded_by_duration_and_interval(self):
        """Bound the feed loop by duration, interval and batch count."""
        clock = {'now': timezone.now()}
        sleeps = []

        def now_fn():
            clock['now'] += dt.timedelta(seconds=5)
            return clock['now']

        def sleep_fn(seconds):
            sleeps.append(seconds)

        result = replay.run_replay(
            self.session,
            self.actor,
            interval_seconds=10,
            max_duration_seconds=35,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        self.assertGreaterEqual(result['batches'], 1)
        self.assertLessEqual(result['batches'], 5)
        self.assertEqual(len(sleeps), result['batches'])


@requires_postgres
class ReplayAuthorizationTest(DemoMetricsEnvMixin, TestCase):
    """Every replay protocol entry point authorizes its actor."""

    def setUp(self):
        """Apply the demo and load the session under test."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()

    def test_unauthenticated_actor_cannot_touch_the_feed(self):
        """Refuse every feed entry point for unauthenticated actors."""
        for action in (
            lambda: replay.start_feed(self.session, AnonymousUser()),
            lambda: replay.replay_batch(
                self.session, AnonymousUser(), batch_key='batch-1'
            ),
            lambda: replay.stop_session(self.session, AnonymousUser()),
            lambda: replay.run_replay(self.session, AnonymousUser()),
        ):
            with self.assertRaises(replay.ReplayError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'ACTOR_UNAUTHORIZED')

    def test_unpermitted_actor_cannot_touch_the_feed(self):
        """Refuse feed actions for actors without the planning permission."""
        intruder = get_user_model().objects.create_user(
            username='replay-intruder', email='r@example.com', password='pw'
        )
        for action in (
            lambda: replay.start_feed(self.session, intruder),
            lambda: replay.replay_batch(self.session, intruder, batch_key='batch-1'),
            lambda: replay.stop_session(self.session, intruder),
        ):
            with self.assertRaises(replay.ReplayError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'ACTOR_UNAUTHORIZED')


@requires_postgres
class ReplayDriftAndBoundsTest(DemoMetricsEnvMixin, TestCase):
    """Batches re-verify ownership/config/placement under lock and stay bounded."""

    def setUp(self):
        """Apply the demo and load the session under test."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()

    def test_placement_drift_blocks_the_batch(self):
        """Block the batch when the machine placement drifted."""
        machine = self.machines['A01']
        machine.physical_location = self.locations['A-LINE']
        machine.save(update_fields=['physical_location'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'PLACEMENT_CHANGED')

    def test_client_drift_blocks_the_batch(self):
        """Block the batch when the machine client drifted."""
        from assets.models import Client

        other = Client.objects.create(name='Other Tenant', code='other-tenant')
        # The operator keeps current scope over both the old and the new
        # client, so the dedicated drift contract is asserted here — the
        # scope boundary cannot mask the drift disclosure for an authorized
        # operator (lost scope is covered separately below).
        self.actor.maintenance_scopes = set(self.actor.maintenance_scopes) | {
            MaintenanceScope(customer_id=None, site_key=None, client_id=other.pk)
        }
        membership = self.session.machines.get(alias='A01')
        machine = membership.machine
        machine.client = other
        machine.save(update_fields=['client'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'CLIENT_CHANGED')

    def test_client_drift_without_current_scope_denies_actor_scope(self):
        """Lost scope denies ACTOR_SCOPE before any drift disclosure."""
        from assets.models import Client

        other = Client.objects.create(name='Other Tenant', code='other-tenant')
        membership = self.session.machines.get(alias='A01')
        machine = membership.machine
        machine.client = other
        machine.save(update_fields=['client'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')

    def test_lost_scope_denies_the_batch_before_feedable_and_config_drift(self):
        """ACTOR_SCOPE precedes expiry and config drift for a lost scope."""
        self.actor.maintenance_scopes = set()
        # Expired session: no status write attempt, no SESSION_EXPIRED.
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        session.expires_at = timezone.now() - dt.timedelta(minutes=1)
        session.save(update_fields=['expires_at'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        # Config drift is not disclosed to a denied actor either.
        session.expires_at = timezone.now() + dt.timedelta(hours=1)
        session.save(update_fields=['expires_at'])
        state = self.state_for('A01', 'bearing_temperature')
        state.binding.external_key = 'operator.retuned.tag'  # codespell:ignore retuned
        state.binding.save(update_fields=['external_key'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')

    def test_ownership_drift_blocks_the_batch(self):
        """Block the batch when synthetic ownership is no longer proven."""
        from assets.models import MachinePart

        MachinePart.objects.filter(machine=self.machines['A01']).delete()
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'OWNERSHIP_DRIFT')

    def test_configuration_drift_blocks_the_batch(self):
        """Block the batch when session-owned configuration drifted."""
        state = self.state_for('A01', 'bearing_temperature')
        state.binding.external_key = 'operator.retuned.tag'  # codespell:ignore retuned
        state.binding.save(update_fields=['external_key'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'CONFIG_DRIFT')

    def state_for(self, alias, signal):
        """Return the live signal state for one session alias/signal."""
        return MachineSignalState.objects.get(
            binding__external_key=f'equa-demo-metrics-v1/{self.session.session_key}/{alias}/{signal}'
        )

    def test_bad_batch_identity_is_refused(self):
        """Refuse batch identities outside the bounded slug vocabulary."""
        for bad_key in ('', 'bad key!', 'x' * 200):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.replay_batch(self.session, self.actor, batch_key=bad_key)
            self.assertEqual(caught.exception.code, 'BAD_BATCH')

    def test_bad_batch_values_are_refused(self):
        """Refuse non-finite, non-numeric or unknown batch values."""
        cases = [
            {('A01', 'no_such_signal'): 1.0},
            {('A01', 'bearing_temperature'): float('nan')},
            {('A01', 'bearing_temperature'): float('inf')},
            {('A01', 'bearing_temperature'): 'hot'},
        ]
        for values in cases:
            with self.assertRaises(replay.ReplayError) as caught:
                replay.replay_batch(
                    self.session, self.actor, batch_key='batch-1', values=values
                )
            self.assertEqual(caught.exception.code, 'BAD_BATCH')

    def test_run_replay_bounds_are_enforced(self):
        """Reject interval/duration bounds outside the allowed range."""
        for interval, duration in ((-1, 10), (10, 0), (10, 7200), (7200, 10)):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.run_replay(
                    self.session,
                    self.actor,
                    interval_seconds=interval,
                    max_duration_seconds=duration,
                )
            self.assertEqual(caught.exception.code, 'BAD_BOUNDS')


def _test_scope_resolver(actor):
    """Deployment-style scope resolver: the internal client's scope only."""
    from assets.models import Client

    client = Client.objects.get(name='Internal')
    return {MaintenanceScope(customer_id=None, site_key=None, client_id=client.pk)}


@requires_postgres
@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_test_scope_resolver)
class VerifyCommandTest(DemoMetricsEnvMixin, TestCase):
    """verify_demo_metrics authorizes first and fails acceptance loudly."""

    def setUp(self):
        """Apply the demo and load the session to verify."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()

    def run_verify(self, username, session_key=None):
        """Run the verify_demo_metrics command and return its output."""
        import io

        from django.core.management import call_command

        stream = io.StringIO()
        call_command(
            'verify_demo_metrics',
            '--session',
            session_key or self.session.session_key,
            '--actor',
            username,
            stdout=stream,
        )
        return stream.getvalue()

    def test_authorized_verification_reports_counts_and_bounded_receipts(self):
        """Report acceptance counts and bounded receipts to authorized actors."""
        import json

        output = self.run_verify(self.actor.username)
        report, _end = json.JSONDecoder().raw_decode(output)
        self.assertTrue(report['acceptance']['ok'])
        # 38 effect receipts plus the durable execution identity receipt.
        self.assertEqual(report['receipt_count'], 39)
        self.assertEqual(report['receipts_returned'], 39)
        self.assertLessEqual(report['receipts_returned'], 200)
        self.assertEqual(report['drift'], [])

    def test_unauthorized_actor_is_refused_before_any_report(self):
        """Refuse unauthorized actors before any verification report."""
        from django.core.management.base import CommandError

        intruder = get_user_model().objects.create_user(
            username='verify-intruder', email='v@example.com', password='pw'
        )
        with self.assertRaises(CommandError) as caught:
            self.run_verify(intruder.username)
        self.assertIn('ACTOR_UNAUTHORIZED', str(caught.exception))

    def test_drifted_records_fail_acceptance_machine_readably(self):
        """Fail acceptance with machine-readable codes on operator drift."""
        from django.core.management.base import CommandError

        row = self.session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key='WO-01'
        )
        row.work_order.title = 'Operator renamed this'
        row.work_order.save(update_fields=['title'])
        with self.assertRaises(CommandError) as caught:
            self.run_verify(self.actor.username)
        self.assertIn('VERIFY_FAILED', str(caught.exception))
        self.assertIn('OPERATOR_DRIFT', str(caught.exception))


@requires_postgres
class CleanupTest(DemoMetricsEnvMixin, TestCase):
    """Ownership-aware cleanup: plan is read-only, apply is hash-gated."""

    def setUp(self):
        """Apply the demo and load the session to clean up."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()

    def test_cleanup_plan_is_read_only_by_default(self):
        """Keep the default cleanup plan read-only and report protected rows."""
        before = self.session.ledger_objects.count()
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        self.assertEqual(self.session.ledger_objects.count(), before)
        self.assertEqual(self.session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertGreater(len(plan['deletions']), 0)
        self.assertTrue(any(item['kind'] == 'session' for item in plan['protected']))
        self.assertTrue(any(item['kind'] == 'receipts' for item in plan['protected']))

    def test_apply_requires_the_exact_cleanup_hash(self):
        """Require the exact approved cleanup hash before deleting anything."""
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, self.actor, approved_cleanup_sha256='0' * 64
            )
        self.assertEqual(caught.exception.code, 'HASH_MISMATCH')
        self.assertEqual(self.session.ledger_objects.count(), 26)

    def test_apply_deletes_created_rows_and_retains_the_tombstone(self):
        """Delete created rows while retaining the tombstone and receipts."""
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.assertEqual(result['deleted']['work_order'], 9)
        self.assertEqual(result['deleted']['binding'], 15)
        self.assertEqual(result['deleted']['source'], 2)
        self.assertEqual(WorkOrder.objects.count(), 0)
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.CLEANED)
        self.assertGreater(session.receipts.count(), 0)
        self.assertFalse(session.machines.filter(claim_active=True).exists())

    def test_referenced_rows_are_never_deleted_and_are_reported(self):
        """Retain and report referenced rows the session does not own."""
        external = WorkOrder.objects.create(
            title='operator job', status='backlog', priority='low'
        )
        DemoMetricsObject.objects.create(
            session=self.session,
            kind=DemoMetricsObject.Kind.WORK_ORDER,
            fixture_key='OPERATOR-REF',
            origin=DemoMetricsObject.Origin.REFERENCED,
            work_order=external,
        )
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        self.assertIn(
            {
                'kind': 'work_order',
                'key': 'OPERATOR-REF',
                'reason': 'referenced_not_owned',
            },
            plan['retained_referenced'],
        )
        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.assertTrue(WorkOrder.objects.filter(pk=external.pk).exists())
        self.assertEqual(
            result['retained_referenced'],
            [
                {
                    'kind': 'work_order',
                    'key': 'OPERATOR-REF',
                    'reason': 'referenced_not_owned',
                }
            ],
        )

    def test_operator_dependent_evidence_preserves_the_work_order(self):
        """Preserve work orders carrying operator-dependent evidence."""
        row = self.session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key='WO-01'
        )
        WorkOrderEvent.objects.create(
            work_order=row.work_order,
            event_type='OPERATOR_NOTE',
            correlation_id=uuid.uuid4(),
            metadata={'note': 'operator annotated this order'},
        )
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        retained = {(item['kind'], item['key']) for item in plan['retained_dependent']}
        self.assertIn(('work_order', 'WO-01'), retained)
        deletions = {(item['kind'], item['key']) for item in plan['deletions']}
        self.assertNotIn(('work_order', 'WO-01'), deletions)
        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.assertEqual(result['deleted']['work_order'], 8)
        self.assertTrue(WorkOrder.objects.filter(pk=row.work_order_id).exists())
        self.assertTrue(
            WorkOrderEvent.objects.filter(
                work_order_id=row.work_order_id, event_type='OPERATOR_NOTE'
            ).exists()
        )

    def test_history_rows_fingerprint_their_live_intervals(self):
        """Fingerprint history rows from their live interval rows."""
        start = dt.datetime(2026, 9, 10, 8, 0)
        machine = self.machines['A03']
        rows = []
        for index in (0, 1):
            interval = DemoMetricsCoverageInterval.objects.create(
                session=self.session,
                event_key=f'coverage/A03/{index:02d}',
                machine=machine,
                start_at=start + dt.timedelta(hours=index),
                end_at=start + dt.timedelta(hours=index + 1),
            )
            row = DemoMetricsObject.objects.create(
                session=self.session,
                kind=DemoMetricsObject.Kind.COVERAGE_INTERVAL,
                fixture_key=interval.event_key,
                origin=DemoMetricsObject.Origin.CREATED,
            )
            row.seed_fingerprint = cleanup.object_fingerprint(row)
            row.last_fingerprint = row.seed_fingerprint
            row.save(update_fields=['seed_fingerprint', 'last_fingerprint'])
            rows.append((row, interval))

        # Drift one interval; the fingerprint is computed from the live row,
        # never read back from the stored seed fingerprint.
        rows[1][1].end_at = start + dt.timedelta(hours=5)
        rows[1][1].save(update_fields=['end_at'])
        self.assertNotEqual(
            cleanup.object_fingerprint(rows[1][0]), rows[1][0].seed_fingerprint
        )

        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        deletions = {(item['kind'], item['key']) for item in plan['deletions']}
        retained = {(item['kind'], item['key']) for item in plan['retained_modified']}
        self.assertIn(('coverage_interval', 'coverage/A03/00'), deletions)
        self.assertIn(('coverage_interval', 'coverage/A03/01'), retained)

        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.assertEqual(result['deleted']['coverage_interval'], 1)
        self.assertTrue(
            DemoMetricsCoverageInterval.objects.filter(
                event_key='coverage/A03/01'
            ).exists()
        )
        self.assertFalse(
            DemoMetricsCoverageInterval.objects.filter(
                event_key='coverage/A03/00'
            ).exists()
        )

    def test_cleanup_requires_an_authorized_authenticated_actor(self):
        """Require an authenticated, authorized actor for cleanup."""
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, AnonymousUser(), approved_cleanup_sha256=plan['plan_hash']
            )
        self.assertEqual(caught.exception.code, 'ACTOR_UNAUTHORIZED')
        intruder = get_user_model().objects.create_user(
            username='cleanup-intruder', email='c@example.com', password='pw'
        )
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, intruder, approved_cleanup_sha256=plan['plan_hash']
            )
        self.assertEqual(caught.exception.code, 'ACTOR_UNAUTHORIZED')
        self.assertEqual(WorkOrder.objects.count(), 9)

    def test_repeat_cleanup_is_safe_and_the_rerun_does_not_recreate(self):
        """Stay safe on repeated cleanup without recreating retired records."""
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        second_plan = cleanup.build_cleanup_plan(self.session, self.actor)
        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=second_plan['plan_hash']
        )
        self.assertTrue(result['already_cleaned'])
        self.assertEqual(WorkOrder.objects.count(), 0)
        # Re-applying the same approved inputs reconciles against the stored
        # result and must not recreate retired records.
        retry = self.apply_demo()
        self.assertTrue(retry.reconciled)
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(DemoMetricsSession.objects.count(), 1)


@requires_postgres
@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_test_scope_resolver)
class CliSessionKeyResolutionTest(DemoMetricsEnvMixin, TestCase):
    """``resolve_session`` fails closed on slugs shared across datasets.

    Session identity is ``(dataset_key, session_key)``: the same slug
    legitimately exists under two datasets, and a silent ``first()`` pick
    could stop or clean up the wrong session. More than one match is a
    machine-readable refusal (``SESSION_AMBIGUOUS``) before any mutation;
    unknown slugs keep ``SESSION_UNKNOWN`` and unique slugs keep resolving.
    The deployment-shaped scope resolver keeps the operator genuinely
    authorized, so the regression proves the refusal — never an incidental
    scope denial standing in for it.
    """

    def setUp(self):
        """Apply the demo session and stage a scratch directory."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()
        self.ledger_before = self.session.ledger_objects.count()
        self.receipts_before = self.session.receipts.count()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def share_slug(self):
        """Persist a second dataset's session sharing the same slug."""
        clone = DemoMetricsSession.objects.get(pk=self.session.pk)
        clone.id = uuid.uuid4()
        clone.dataset_key = 'equa-other-dataset'
        clone.save()
        return clone

    def assert_no_mutation(self, clone):
        """Both sessions, claims, receipts and ledger rows are untouched."""
        for pk in (self.session.pk, clone.pk):
            row = DemoMetricsSession.objects.get(pk=pk)
            self.assertEqual(row.status, DemoMetricsSession.Status.ACTIVE)
            self.assertIsNone(row.stopped_at)
            self.assertIsNone(row.cleaned_at)
        self.assertTrue(self.session.machines.filter(claim_active=True).exists())
        self.assertEqual(self.session.ledger_objects.count(), self.ledger_before)
        self.assertEqual(self.session.receipts.count(), self.receipts_before)

    def test_unique_slug_resolves_and_unknown_slug_stays_session_unknown(self):
        """Unique matches resolve; unknown slugs are ``SESSION_UNKNOWN``."""
        resolved = cli.resolve_session(self.session.session_key)
        self.assertEqual(resolved.pk, self.session.pk)
        with self.assertRaises(cli.CliError) as caught:
            cli.resolve_session('no-such-session')
        self.assertIn('SESSION_UNKNOWN', str(caught.exception))

    def test_ambiguous_slug_refuses_stop_without_any_mutation(self):
        """``stop`` on a shared slug refuses and changes neither session."""
        clone = self.share_slug()
        with self.assertRaises(CommandError) as caught:
            call_command(
                'stop_demo_metrics',
                '--session',
                self.session.session_key,
                '--actor',
                self.actor.username,
            )
        self.assertIn('SESSION_AMBIGUOUS', str(caught.exception))
        self.assert_no_mutation(clone)

    def test_ambiguous_slug_refuses_cleanup_without_any_mutation(self):
        """``cleanup`` plan and apply on a shared slug refuse and write nothing."""
        clone = self.share_slug()
        plan_hash = cleanup.build_cleanup_plan(self.session, self.actor)['plan_hash']
        out_path = self.tmp / 'cleanup-plan.json'
        invocations = (
            (
                '--session',
                self.session.session_key,
                '--actor',
                self.actor.username,
                '--apply',
                '--approved-cleanup-sha256',
                plan_hash,
            ),
            (
                '--session',
                self.session.session_key,
                '--actor',
                self.actor.username,
                '--out',
                str(out_path),
            ),
        )
        for args in invocations:
            with self.assertRaises(CommandError) as caught:
                call_command('cleanup_demo_metrics', *args)
            self.assertIn('SESSION_AMBIGUOUS', str(caught.exception))
        self.assertFalse(out_path.exists())
        self.assertEqual(WorkOrder.objects.count(), 9)
        self.assert_no_mutation(clone)
