"""Focused reconciliation semantics for the EQUA demo metrics apply.

A lost-response retry of one approved apply is a pure readback of committed
work, bound to an immutable execution identity. These tests pin the reviewed
behavior: the ``include_history`` execution choice conflicts on change in
either direction and never becomes an owed history phase, disclosure
re-authorizes the *current* actor's scope over every retained membership,
stopped/cleaned sessions are never written to or recreated, missing or
partial history evidence fails closed instead of being guessed from interval
rows, and an identical committed readback survives the freshness/expiry
gates with the identity, runtime and scope checks retained.
"""

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

from aimms_testing import requires_postgres
from assets.demo_metrics import apply_service, cleanup, replay
from assets.demo_metrics.demo_test_support import DemoMetricsEnvMixin
from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsObject,
    DemoMetricsReceipt,
    DemoMetricsSession,
)

PAST_EXPIRY = dt.datetime(2030, 6, 1, tzinfo=dt.timezone.utc)


@requires_postgres
class ExecutionIdentityBindingTest(DemoMetricsEnvMixin, TestCase):
    """The initial execution choice is durable receipt evidence, immutable."""

    def setUp(self):
        """Build the disposable demo environment."""
        self.build_demo_env(history_import_approved=True)

    def test_execution_choice_is_persisted_with_the_durable_receipt_evidence(self):
        """Persist the include_history choice in the durable execution receipt."""
        self.apply_demo(include_history=False)
        session = self.session()
        stored = session.receipts.get(
            operation_kind=apply_service.EXECUTION_OPERATION_KIND,
            item_key=apply_service.EXECUTION_ITEM_KEY,
        )
        self.assertEqual(
            stored.request_hash,
            apply_service._execution_digest(
                self.fixture, self.mapping, self.plan, False
            ),
        )
        # The evidence is the receipt, never a guess from interval rows.
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 0)


@requires_postgres
class ReconcileScopeTest(DemoMetricsEnvMixin, TestCase):
    """Readback re-authorizes the current actor scope over retained memberships."""

    def setUp(self):
        """Apply one committed session to read back."""
        self.build_demo_env()
        self.apply_demo()

    def test_revoked_scope_refuses_readback_before_any_disclosure(self):
        """Refuse readback when the actor's scope was revoked after the apply."""
        receipts = DemoMetricsReceipt.objects.count()
        self.actor.maintenance_scopes = set()
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'SCOPE_REVOKED')
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
        self.assertEqual(DemoMetricsSession.objects.count(), 1)

    def test_identity_and_role_alone_do_not_disclose_retained_memberships(self):
        """Refuse a same-identity superuser whose scope moved to another client."""
        from assets.models import Client

        other, _created = Client.objects.get_or_create(
            name='Other Tenant', defaults={'code': 'other'}
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=other.pk)
        }
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'SCOPE_REVOKED')

    def test_stale_memberships_are_still_scope_checked(self):
        """Re-authorize released (claim inactive) memberships too, not just active ones."""
        for membership in self.session().machines.all():
            membership.claim_active = False
            membership.save(update_fields=['claim_active'])
        self.actor.maintenance_scopes = set()
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'SCOPE_REVOKED')


@requires_postgres
class ReconcileLifecycleTest(DemoMetricsEnvMixin, TestCase):
    """Stopped/cleaned replays perform no writes and never recreate anything."""

    def test_stopped_session_retry_is_pure_readback_without_writes(self):
        """Read back a stopped session without writing or re-importing anything."""
        self.build_demo_env(history_import_approved=True)
        self.apply_demo(include_history=True)
        session = self.session()
        replay.stop_session(session, self.actor)
        coverage_count = DemoMetricsCoverageInterval.objects.count()
        downtime_count = DemoMetricsDowntimeInterval.objects.count()
        receipts = DemoMetricsReceipt.objects.count()
        self.assertGreater(coverage_count + downtime_count, 0)

        result = self.apply_demo(include_history=True)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.created['coverage_intervals'], coverage_count)
        self.assertEqual(result.created['downtime_intervals'], downtime_count)
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), coverage_count)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), downtime_count)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
        self.assertEqual(self.session().status, DemoMetricsSession.Status.STOPPED)

    def test_cleaned_session_retry_recreates_nothing(self):
        """Never recreate, add or remove rows around a receipted cleanup."""
        self.build_demo_env(history_import_approved=True)
        self.apply_demo(include_history=True)
        session = self.session()
        plan = cleanup.build_cleanup_plan(session, self.actor)
        cleanup.apply_cleanup(
            session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        # Whatever the approved cleanup deleted or retained is the committed
        # state; a retry must reproduce it exactly, never re-import anything.
        coverage_count = DemoMetricsCoverageInterval.objects.count()
        downtime_count = DemoMetricsDowntimeInterval.objects.count()
        ledger_count = self.session().ledger_objects.count()
        receipts = DemoMetricsReceipt.objects.count()

        result = self.apply_demo(include_history=True)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.created['coverage_intervals'], coverage_count)
        self.assertEqual(result.created['downtime_intervals'], downtime_count)
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), coverage_count)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), downtime_count)
        self.assertEqual(self.session().ledger_objects.count(), ledger_count)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
        self.assertEqual(self.session().status, DemoMetricsSession.Status.CLEANED)
        self.assertEqual(WorkOrder.objects.count(), 0)


@requires_postgres
class ReconcileHistoryEvidenceTest(DemoMetricsEnvMixin, TestCase):
    """The bound choice - never interval rows - is the history completion proof."""

    def setUp(self):
        """Build an environment whose approved plan authorizes history."""
        self.build_demo_env(history_import_approved=True)

    def test_missing_history_evidence_fails_closed_and_never_reimports(self):
        """Fail closed when the executed phase's rows are missing entirely."""
        self.apply_demo(include_history=True)
        session = self.session()
        DemoMetricsDowntimeInterval.objects.filter(session=session).delete()
        DemoMetricsCoverageInterval.objects.filter(session=session).delete()
        session.ledger_objects.filter(
            kind__in=[
                DemoMetricsObject.Kind.COVERAGE_INTERVAL,
                DemoMetricsObject.Kind.DOWNTIME_INTERVAL,
            ]
        ).delete()
        receipts = DemoMetricsReceipt.objects.count()

        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=True)
        self.assertEqual(caught.exception.code, 'HISTORY_EVIDENCE_INCOMPLETE')
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)

    def test_partial_history_evidence_fails_closed(self):
        """Fail closed on partial interval evidence instead of guessing completion."""
        self.apply_demo(include_history=True)
        session = self.session()
        first = (
            DemoMetricsCoverageInterval.objects
            .filter(session=session)
            .order_by('event_key')
            .first()
        )
        event_key = first.event_key
        first.delete()
        session.ledger_objects.filter(
            kind=DemoMetricsObject.Kind.COVERAGE_INTERVAL, fixture_key=event_key
        ).delete()
        coverage_after_loss = DemoMetricsCoverageInterval.objects.count()
        receipts = DemoMetricsReceipt.objects.count()

        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=True)
        self.assertEqual(caught.exception.code, 'HISTORY_EVIDENCE_INCOMPLETE')
        self.assertEqual(
            DemoMetricsCoverageInterval.objects.count(), coverage_after_loss
        )
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)

    def test_history_rows_the_choice_never_executed_fail_closed(self):
        """Refuse rows that contradict a never-executed history choice."""
        self.apply_demo(include_history=False)
        session = self.session()
        DemoMetricsCoverageInterval.objects.create(
            session=session,
            event_key='XTRA-UNEXECUTED',
            machine=self.machines['A01'],
            start_at=timezone.now(),
            end_at=timezone.now() + dt.timedelta(hours=1),
        )
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=False)
        self.assertEqual(caught.exception.code, 'HISTORY_EVIDENCE_UNEXPECTED')

    def test_missing_execution_identity_fails_closed(self):
        """Refuse disclosure when the durable execution identity is gone."""
        self.apply_demo(include_history=False)
        session = self.session()
        session.receipts.filter(
            operation_kind=apply_service.EXECUTION_OPERATION_KIND
        ).delete()
        receipts = DemoMetricsReceipt.objects.count()
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=False)
        self.assertEqual(caught.exception.code, 'EXECUTION_EVIDENCE_MISSING')
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)


@requires_postgres
class ReconcileExpiredReadbackTest(DemoMetricsEnvMixin, TestCase):
    """Identical committed readback survives the time gates, checks retained."""

    def test_lost_response_retry_after_expiry_returns_the_committed_result(self):
        """Return the stored result after expiry for an identical payload."""
        self.build_demo_env(history_import_approved=True)
        self.apply_demo(include_history=True)
        receipts = DemoMetricsReceipt.objects.count()
        coverage = self.fixture.data['history']['coverage_intervals']
        downtime = self.fixture.data['history']['downtime_intervals']

        result = self.apply_demo(include_history=True, now=PAST_EXPIRY)
        self.assertTrue(result.reconciled)
        self.assertEqual(result.created['coverage_intervals'], len(coverage))
        self.assertEqual(result.created['downtime_intervals'], len(downtime))
        self.assertEqual(result.created['bindings'], 15)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
        self.assertEqual(DemoMetricsSession.objects.count(), 1)

    def test_scope_is_still_reauthorized_after_expiry(self):
        """Keep the scope check when the time gates would no longer fire."""
        self.build_demo_env()
        self.apply_demo()
        self.actor.maintenance_scopes = set()
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(now=PAST_EXPIRY)
        self.assertEqual(caught.exception.code, 'SCOPE_REVOKED')


@requires_postgres
class ReconcileStoredResultContractTest(DemoMetricsEnvMixin, TestCase):
    """The identical retry returns the stored result exactly as committed."""

    def setUp(self):
        """Apply one committed session to read back."""
        self.build_demo_env()
        self.result = self.apply_demo()

    def test_apply_result_receipts_cover_the_whole_apply_execution(self):
        """Count every durable receipt the apply committed, execution included."""
        session = self.session()
        # Right after the apply, every receipt belongs to that apply - one
        # per effect plus the durable execution receipt binding the payload.
        self.assertEqual(self.result.receipts, session.receipts.count())

    def test_identical_retry_reports_the_stored_receipt_count(self):
        """Report the stored receipt count even after later lifecycle receipts."""
        initial = self.result.receipts
        session = self.session()
        replay.stop_session(session, self.actor)
        # The stop writes its own receipt; the stored apply summary must not
        # absorb it (nor ever omit the apply's own execution receipt).
        self.assertGreater(session.receipts.count(), initial)

        result = self.apply_demo()
        self.assertTrue(result.reconciled)
        self.assertEqual(result.receipts, initial)

    def test_work_control_split_is_provenance_not_live_work_order_state(self):
        """Keep the stored work/control split when live work-order state moves."""
        session = self.session()
        row = session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key='WO-01'
        )
        # Operator archive of an open work order: the documented live-state
        # soft-delete. It must not rewrite the stored work/control split.
        row.work_order.is_active = False
        row.work_order.save(update_fields=['is_active', 'updated_at'])

        result = self.apply_demo()
        self.assertTrue(result.reconciled)
        self.assertEqual(result.created['work_orders'], 7)
        self.assertEqual(result.created['controls'], 2)
