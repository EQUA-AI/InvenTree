"""Integration tests for the governed EQUA demo metrics apply (work packages B/C).

Runs on the disposable local test database only. PostgreSQL-only behaviors
(advisory locks, partial unique claims) carry the honest skip marker.
"""

import datetime as dt
import json
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from tasks.models import WorkOrder, WorkOrderEvent, WorkOrderLifecycle

from aimms_testing import requires_postgres
from assets.demo_metrics import apply_service, cleanup, planner
from assets.demo_metrics.demo_test_support import FIXTURE_PATH, DemoMetricsEnvMixin
from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsObject,
    DemoMetricsReceipt,
    DemoMetricsSession,
)
from assets.health_models import MachineSignalState, SignalQuality


@requires_postgres
class ApplyCohortTest(DemoMetricsEnvMixin, TestCase):
    """One approved plan applies the whole reviewed cohort atomically."""

    def setUp(self):
        """Build the environment and apply the reviewed cohort once."""
        self.build_demo_env()
        self.result = self.apply_demo()

    def test_creates_the_full_reviewed_cohort(self):
        """Apply every planned source, binding, work order and observation."""
        self.assertEqual(self.result.created['sources'], 2)
        self.assertEqual(self.result.created['bindings'], 15)
        self.assertEqual(self.result.created['work_orders'], 7)
        self.assertEqual(self.result.created['controls'], 2)
        self.assertEqual(self.result.created['observations'], 12)
        session = self.session()
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertIsNotNone(session.applied_at)
        self.assertEqual(
            session.ledger_objects.filter(kind=DemoMetricsObject.Kind.BINDING).count(),
            15,
        )
        self.assertEqual(
            session.ledger_objects.filter(
                kind=DemoMetricsObject.Kind.WORK_ORDER
            ).count(),
            9,
        )

    def test_open_work_orders_reach_requested_states_through_legal_edges(self):
        """Reach each requested lifecycle state through legal transitions only."""
        expected = {
            'WO-01': WorkOrderLifecycle.PLANNED,
            'WO-02': WorkOrderLifecycle.READY,
            'WO-03': WorkOrderLifecycle.ON_HOLD,
            'WO-04': WorkOrderLifecycle.VERIFYING,
            'WO-05': WorkOrderLifecycle.IN_PROGRESS,
            'WO-06': WorkOrderLifecycle.PLANNED,
            'WO-07': WorkOrderLifecycle.READY,
        }
        session = self.session()
        for key, state in expected.items():
            row = session.ledger_objects.get(
                kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key=key
            )
            self.assertEqual(row.work_order.lifecycle_status, state)
            self.assertTrue(row.work_order.is_active)
        order = session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key='WO-04'
        ).work_order
        transitions = list(
            WorkOrderEvent.objects
            .filter(work_order=order)
            .order_by('pk')
            .values_list('event_type', flat=True)
        )
        # WO-04 resolves a typed assignee (required by the start readiness
        # gate), so the assign is part of the honest event chain.
        self.assertEqual(
            transitions,
            [
                'CREATED',
                'ASSIGNED',
                'TRANSITION',
                'TRANSITION',
                'TRANSITION',
                'TRANSITION',
            ],
        )

    def test_completed_controls_are_synthetic_imports_with_synchronized_cards(self):
        """Import completed controls with synthetic provenance and terminal cards."""
        from tasks.models import KanbanCard, KanbanColumn

        session = self.session()
        terminal = KanbanColumn.terminal_key() or WorkOrder.STATUS_DONE
        for key in ('WO-08', 'WO-09'):
            row = session.ledger_objects.get(
                kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key=key
            )
            order = row.work_order
            self.assertEqual(order.lifecycle_status, WorkOrderLifecycle.COMPLETED)
            self.assertFalse(order.is_active)
            event = WorkOrderEvent.objects.get(
                work_order=order, event_type='IMPORTED_HISTORY'
            )
            self.assertTrue(event.metadata.get('synthetic'))
            card = KanbanCard.objects.get(
                work_order=order, card_kind=KanbanCard.KIND_WORK_ORDER
            )
            self.assertEqual(card.status, terminal)
            self.assertFalse(card.is_active)
            self.assertIn({'kind': 'card', 'id': card.pk}, row.dependent_refs)

    def test_due_dates_follow_the_application_date_rule(self):
        """Compute due dates with the application date rule in the reporting timezone."""
        session = self.session()
        anchor = session.anchor_at
        if anchor.tzinfo is not None:
            anchor = anchor.replace(tzinfo=None)
        row = session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key='WO-01'
        )
        self.assertEqual(
            row.work_order.due_date, (anchor + dt.timedelta(days=3)).date()
        )

    def test_priorities_use_the_approved_demo_translation(self):
        """Translate suggested priorities with the approved demo mapping."""
        session = self.session()
        expected = {'WO-01': 'medium', 'WO-02': 'high', 'WO-05': 'high', 'WO-07': 'low'}
        for key, priority in expected.items():
            row = session.ledger_objects.get(
                kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key=key
            )
            self.assertEqual(row.work_order.priority, priority)

    def test_observations_are_good_quality_with_namespaced_tags(self):
        """Ingest good-quality observations under uniquely namespaced external tags."""
        states = MachineSignalState.objects.select_related('binding').order_by(
            'binding__external_key'
        )
        self.assertEqual(states.count(), 12)
        for state in states:
            self.assertEqual(state.quality, SignalQuality.GOOD)
            self.assertTrue(
                state.binding.external_key.startswith(
                    f'equa-demo-metrics-v1/{self.mapping.session_key}/'
                )
            )
            self.assertEqual(state.binding.source.freshness_threshold_seconds, 300)

    def test_receipts_cover_every_created_effect(self):
        """Cover every created effect with its durable receipt."""
        session = self.session()
        counts = {
            kind: session.receipts.filter(operation_kind=kind).count()
            for kind in (
                DemoMetricsReceipt.Operation.APPLY_SOURCE,
                DemoMetricsReceipt.Operation.APPLY_BINDING,
                DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
                DemoMetricsReceipt.Operation.APPLY_OBSERVATION,
            )
        }
        self.assertEqual(counts[DemoMetricsReceipt.Operation.APPLY_SOURCE], 2)
        self.assertEqual(counts[DemoMetricsReceipt.Operation.APPLY_BINDING], 15)
        self.assertEqual(counts[DemoMetricsReceipt.Operation.APPLY_WORK_ORDER], 9)
        self.assertEqual(counts[DemoMetricsReceipt.Operation.APPLY_OBSERVATION], 12)

    def test_plan_canonical_body_is_stored_without_its_own_hash(self):
        """Store the canonical plan body without its hash or approval envelope."""
        session = self.session()
        self.assertNotIn('plan_hash', session.plan_body)
        self.assertNotIn('approval', session.plan_body)
        self.assertEqual(session.plan_sha256, self.plan['plan_hash'])
        self.assertEqual(
            session.fixture_canonical_sha256, self.fixture.canonical_sha256
        )

    def test_identical_retry_returns_the_stored_result_without_new_effects(self):
        """Return the stored result on an identical retry, with no new effects."""
        receipts_before = DemoMetricsReceipt.objects.count()
        result = self.apply_demo()
        # Lost-response retry of the identical approved apply: the stored
        # result is reconciled and no second effect set is created.
        self.assertTrue(result.reconciled)
        self.assertEqual(result.created['bindings'], 15)
        self.assertEqual(result.created['work_orders'], 7)
        self.assertEqual(result.created['controls'], 2)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts_before)
        self.assertEqual(DemoMetricsSession.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 9)

    def test_retry_with_changed_approved_inputs_is_a_conflict(self):
        """Conflict on a retry whose approved inputs changed."""
        body = planner.build_plan(
            fixture=self.fixture,
            mapping=self.mapping,
            actor=self.actor,
            session_anchor=timezone.now() + dt.timedelta(minutes=2),
            resolutions=self.resolutions,
            conflicts=[],
        )
        plan = planner.sign_plan(body)
        with self.assertRaises(apply_service.ApplyError) as caught:
            apply_service.apply_session(
                fixture=self.fixture, mapping=self.mapping, plan=plan, actor=self.actor
            )
        self.assertEqual(caught.exception.code, 'SESSION_INPUT_CONFLICT')

    def test_sources_use_the_approved_security_scope(self):
        """Place synthetic sources in the approved security scope, never fixture aliases."""
        from assets.demo_metrics.demo_test_support import SECURITY_SITE_KEY

        sources = list(
            MachineSignalState.objects.values_list(
                'binding__source__site_key', flat=True
            )
        )
        self.assertTrue(sources)
        self.assertEqual(set(sources), {SECURITY_SITE_KEY})
        for state in MachineSignalState.objects.select_related('binding__source'):
            self.assertEqual(state.binding.source.config.get('client_code'), 'internal')
            # The fixture's physical aliases never become the security scope.
            self.assertNotIn(state.binding.source.site_key, {'SITE-A', 'SITE-B'})

    def test_machine_claims_record_verified_ownership_evidence(self):
        """Record only database-verified ownership evidence on machine claims."""
        for membership in self.session().machines.all():
            self.assertEqual(
                membership.ownership_evidence.get('method'), 'managed_demo_part'
            )
            self.assertIn('verified_part_ipns', membership.ownership_evidence)
            self.assertNotIn(
                'verified_synthetic_ownership', membership.ownership_evidence
            )

    def test_a_second_session_cannot_claim_the_same_machines(self):
        """Refuse a second session claiming machines already claimed."""
        from assets.demo_metrics import contract
        from assets.demo_metrics.demo_test_support import build_mapping_dict

        data = build_mapping_dict(
            self.fixture,
            machine_ids={alias: machine.pk for alias, machine in self.machines.items()},
            location_ids={alias: node.pk for alias, node in self.locations.items()},
            session_key='equa-demo-test-2',
            assignee_username=self.actor.username,
        )
        mapping = contract.validate_mapping(data, self.fixture, require_ready=True)
        resolutions, conflicts = planner.resolve_targets(
            self.fixture, mapping, self.actor
        )
        body = planner.build_plan(
            fixture=self.fixture,
            mapping=mapping,
            actor=self.actor,
            session_anchor=timezone.now(),
            resolutions=resolutions,
            conflicts=conflicts,
        )
        plan = planner.sign_plan(body)
        # The plan-time conflict check (ALREADY_CLAIMED) or, if raced past it,
        # the unique machine claim must both refuse the second session.
        with self.assertRaises((apply_service.ApplyError, planner.PlanError)) as caught:
            apply_service.apply_session(
                fixture=self.fixture, mapping=mapping, plan=plan, actor=self.actor
            )
        self.assertIn(caught.exception.code, {'MACHINE_CLAIMED', 'PLAN_CONFLICTS'})
        self.assertFalse(
            DemoMetricsSession.objects.filter(session_key='equa-demo-test-2').exists()
        )


@requires_postgres
class ApplyIdentityGateTest(DemoMetricsEnvMixin, TestCase):
    """Target/image/schema/actor checks run before any effect."""

    def setUp(self):
        """Build the disposable demo environment."""
        self.build_demo_env()

    def test_unattested_code_identity_is_refused_before_effects(self):
        """Refuse unattested code identity before any effect."""
        import os

        from assets.demo_metrics import fingerprint

        os.environ.pop(fingerprint.CODE_IDENTITY_ENV, None)
        os.environ.pop(fingerprint.IMAGE_IDENTITY_ENV, None)
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'CODE_IDENTITY_UNVERIFIED')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_mismatched_code_identity_is_refused_before_effects(self):
        """Refuse code identity that does not match the approved mapping."""
        import os

        from assets.demo_metrics import fingerprint

        os.environ[fingerprint.CODE_IDENTITY_ENV] = 'someone-elses-commit'
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'CODE_IDENTITY_MISMATCH')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_plan_actor_must_match_the_runtime_actor(self):
        """Refuse a plan created for a different actor than the runtime operator."""
        from django.contrib.auth import get_user_model

        other = get_user_model().objects.create_superuser(
            username='other-operator', email='o@example.com', password='pw'
        )
        body = planner.build_plan(
            fixture=self.fixture,
            mapping=self.mapping,
            actor=other,
            session_anchor=timezone.now(),
            resolutions=self.resolutions,
            conflicts=[],
        )
        plan = planner.sign_plan(body)
        with self.assertRaises(planner.PlanError) as caught:
            apply_service.apply_session(
                fixture=self.fixture, mapping=self.mapping, plan=plan, actor=self.actor
            )
        self.assertEqual(caught.exception.code, 'ACTOR_MISMATCH')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_unresolvable_actor_scope_fails_closed_when_planning(self):
        """Fail closed at planning when the actor scope cannot be resolved."""
        from django.contrib.auth import get_user_model

        plain = get_user_model().objects.create_user(
            username='no-scope-operator', email='n@example.com', password='pw'
        )
        resolutions, conflicts = planner.resolve_targets(
            self.fixture, self.mapping, plain
        )
        codes = {item['code'] for item in conflicts if item['key'] == 'A01'}
        self.assertIn('SCOPE_UNRESOLVED', codes)
        self.assertFalse(resolutions['A01'].in_actor_scope)

    def test_unapproved_history_import_is_refused(self):
        """Refuse a history import the approved plan never authorized."""
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=True)
        self.assertEqual(caught.exception.code, 'HISTORY_NOT_APPROVED')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_history_approval_is_bound_into_the_plan_body(self):
        """Bind the history approval flag into the canonical plan body."""
        from assets.demo_metrics import contract as contract_module

        self.mapping_data['history_import_approved'] = True
        mapping = contract_module.validate_mapping(
            self.mapping_data, self.fixture, require_ready=True
        )
        body = planner.build_plan(
            fixture=self.fixture,
            mapping=mapping,
            actor=self.actor,
            session_anchor=timezone.now(),
            resolutions=self.resolutions,
            conflicts=[],
        )
        self.assertTrue(planner.sign_plan(body)['include_history'])


@requires_postgres
class ReconcileBindingTest(DemoMetricsEnvMixin, TestCase):
    """Retries on one session key bind the immutable execution identity.

    A lost-response retry owes no effects and never adds one: the initial
    ``include_history`` execution choice is persisted with the durable
    execution receipt at apply time. An identical payload is a pure readback
    of already-authorized committed work and reconciles even after the
    freshness/expiry gates would refuse a new execution. A changed payload -
    the ``include_history`` execution flag flipping in either direction - is
    a conflict, never an owed history phase.
    """

    def test_retry_with_changed_include_history_false_to_true_is_a_conflict(self):
        """A changed execution choice never becomes an owed history phase."""
        self.build_demo_env(history_import_approved=True)
        first = self.apply_demo(include_history=False)
        self.assertFalse(first.reconciled)
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 0)

        coverage = self.fixture.data['history']['coverage_intervals']
        downtime = self.fixture.data['history']['downtime_intervals']
        self.assertGreater(len(coverage) + len(downtime), 0)

        receipts = DemoMetricsReceipt.objects.count()
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=True)
        self.assertEqual(caught.exception.code, 'RECEIPT_CONFLICT')
        # The retry changed the immutable execution payload: it is refused,
        # never executed - no history import, no new receipts, no rewrite.
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
        self.assertEqual(DemoMetricsSession.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 9)

    def test_retry_with_changed_include_history_true_to_false_is_a_conflict(self):
        """Flipping the bound choice the other way conflicts and preserves rows."""
        self.build_demo_env(history_import_approved=True)
        self.apply_demo(include_history=True)
        coverage_count = DemoMetricsCoverageInterval.objects.count()
        downtime_count = DemoMetricsDowntimeInterval.objects.count()
        self.assertGreater(coverage_count + downtime_count, 0)
        receipts = DemoMetricsReceipt.objects.count()

        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=False)
        self.assertEqual(caught.exception.code, 'RECEIPT_CONFLICT')
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), coverage_count)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), downtime_count)
        self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
        self.assertEqual(WorkOrder.objects.count(), 9)

    def test_unapproved_history_retry_is_refused_without_new_effects(self):
        """Requesting history the plan never approved is refused loudly."""
        self.build_demo_env(history_import_approved=False)
        self.apply_demo(include_history=False)
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(include_history=True)
        self.assertEqual(caught.exception.code, 'HISTORY_NOT_APPROVED')
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 0)

    def test_identical_retry_reconciles_past_the_freshness_and_expiry_gates(self):
        """Pure readback issues no effects and needs no current authority."""
        self.build_demo_env()
        self.apply_demo()
        receipts = DemoMetricsReceipt.objects.count()
        late_anchor = timezone.now() + dt.timedelta(
            seconds=self.fixture.stale_after_seconds + 60
        )
        past_expiry = dt.datetime(2030, 6, 1, tzinfo=dt.timezone.utc)
        for retry_now in (late_anchor, past_expiry):
            with self.subTest(now=retry_now):
                retried = self.apply_demo(now=retry_now)
                self.assertTrue(retried.reconciled)
                self.assertEqual(retried.created['bindings'], 15)
                self.assertEqual(retried.created['work_orders'], 7)
                self.assertEqual(DemoMetricsReceipt.objects.count(), receipts)
                self.assertEqual(DemoMetricsSession.objects.count(), 1)
                self.assertEqual(WorkOrder.objects.count(), 9)

    def test_changed_include_history_conflicts_regardless_of_the_time_gates(self):
        """The immutable binding is timeless: a changed payload always conflicts."""
        self.build_demo_env(history_import_approved=True)
        self.apply_demo(include_history=False)
        late = timezone.now() + dt.timedelta(
            seconds=self.fixture.stale_after_seconds + 60
        )
        past_expiry = dt.datetime(2030, 6, 1, tzinfo=dt.timezone.utc)
        for retry_now in (late, past_expiry):
            with self.subTest(now=retry_now):
                with self.assertRaises(apply_service.ApplyError) as caught:
                    self.apply_demo(include_history=True, now=retry_now)
                self.assertEqual(caught.exception.code, 'RECEIPT_CONFLICT')
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 0)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 0)
        self.assertEqual(WorkOrder.objects.count(), 9)


@requires_postgres
class OwnershipEvidenceEnforcementTest(DemoMetricsEnvMixin, TestCase):
    """Ownership evidence is verified from the database under lock, not trusted."""

    def setUp(self):
        """Build the disposable demo environment."""
        self.build_demo_env()

    def test_unverifiable_ownership_is_refused_under_the_machine_lock(self):
        """Refuse unverifiable ownership under the machine row lock."""
        from assets.models import MachinePart

        MachinePart.objects.filter(machine=self.machines['A01']).delete()
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'OWNERSHIP_UNVERIFIED')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)
        self.assertEqual(WorkOrder.objects.count(), 0)


class EffectIsolationTest(TestCase):
    """Synthetic sessions cannot dispatch tasks or send notifications/emails."""

    def test_dispatch_guards_are_wired_and_noop_outside_the_window(self):
        """Block task dispatch inside the synthetic window and leave it unchanged outside."""
        from assets.demo_metrics.effects import ExternalEffectBlocked, synthetic_effects
        from InvenTree.tasks import bulk_offload_task, offload_task

        # Outside the window nothing changes for real user workflows.
        self.assertFalse(offload_task('nonexistent.task.path'))

        with synthetic_effects():
            with self.assertRaises(ExternalEffectBlocked):
                offload_task('nonexistent.task.path')
            with self.assertRaises(ExternalEffectBlocked):
                offload_task('nonexistent.task.path', force_sync=True)
            with self.assertRaises(ExternalEffectBlocked):
                bulk_offload_task('nonexistent.task.path', [((), {})])

    def test_batched_queued_dispatch_is_refused_inside_the_window(self):
        """Refuse batched queued dispatch inside the synthetic window."""
        from assets.demo_metrics.effects import ExternalEffectBlocked, synthetic_effects
        from InvenTree.tasks import batch_offload_tasks, offload_task

        with synthetic_effects():
            with self.assertRaises(ExternalEffectBlocked):
                with batch_offload_tasks():
                    offload_task('nonexistent.task.path')

    def test_notification_and_email_consumers_are_guarded(self):
        """Existing data-import suppression covers notifications and email.

        Inside the seed window the stock consumers *return* quietly via their
        existing ``isImportingData()`` suppression — no exception, no rows, no
        email — exactly as for a ``loaddata`` import.
        """
        from django.core import mail

        from assets.demo_metrics.effects import synthetic_effects
        from common.models import NotificationEntry, NotificationMessage
        from common.notifications import trigger_notification
        from InvenTree.helpers_email import send_email

        with synthetic_effects():
            self.assertIsNone(trigger_notification(None, 'test.category'))
            result = send_email('subject', 'body', ['someone@example.com'])
            self.assertFalse(result[0])

        # Nothing was dispatched or recorded: no messages, no dedup entries,
        # and nothing reached the real email outbox.
        self.assertEqual(NotificationMessage.objects.count(), 0)
        self.assertEqual(NotificationEntry.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_plugin_and_ai_dispatch_paths_are_classified(self):
        """Classify plugin and AI dispatch paths as blocked effects."""
        from assets.demo_metrics.effects import (
            ExternalEffectBlocked,
            guard_task_dispatch,
            synthetic_effects,
        )

        with synthetic_effects():
            with self.assertRaises(ExternalEffectBlocked):
                guard_task_dispatch('plugin.base.event.events.register_event', 'plugin')
            with self.assertRaises(ExternalEffectBlocked):
                guard_task_dispatch('aichat.services.threads.some_task')

    def test_the_effect_window_is_context_local(self):
        """Keep the effect window context-local to the applying thread."""
        import threading

        from assets.demo_metrics.effects import is_synthetic_context, synthetic_effects

        seen = []
        with synthetic_effects():
            thread = threading.Thread(
                target=lambda: seen.append(is_synthetic_context())
            )
            thread.start()
            thread.join(timeout=10)
            self.assertTrue(is_synthetic_context())
        self.assertEqual(seen, [False])
        self.assertFalse(is_synthetic_context())


@requires_postgres
class ApplyRefusalTest(DemoMetricsEnvMixin, TestCase):
    """Every unmet prerequisite fails before any effect."""

    def setUp(self):
        """Build the disposable demo environment."""
        self.build_demo_env()

    def test_stale_anchor_is_refused_and_nothing_is_written(self):
        """Refuse a stale planning anchor without writing anything."""
        late = timezone.now() + dt.timedelta(
            seconds=self.fixture.stale_after_seconds + 60
        )
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo(now=late)
        self.assertEqual(caught.exception.code, 'STALE_ANCHOR')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_target_fingerprint_mismatch_fails_before_effects(self):
        """Fail before effects when the target fingerprint does not match."""
        from assets.demo_metrics import contract
        from assets.demo_metrics.demo_test_support import build_mapping_dict

        data = build_mapping_dict(
            self.fixture,
            machine_ids={alias: machine.pk for alias, machine in self.machines.items()},
            location_ids={alias: node.pk for alias, node in self.locations.items()},
            session_key='equa-demo-test-1',
            fingerprint='not-this-database',
        )
        mapping = contract.validate_mapping(data, self.fixture, require_ready=True)
        plan = planner.sign_plan(
            planner.build_plan(
                fixture=self.fixture,
                mapping=mapping,
                actor=self.actor,
                session_anchor=timezone.now(),
                resolutions={},
                conflicts=[],
            )
        )
        with self.assertRaises(apply_service.ApplyError) as caught:
            apply_service.apply_session(
                fixture=self.fixture, mapping=mapping, plan=plan, actor=self.actor
            )
        self.assertEqual(caught.exception.code, 'TARGET_MISMATCH')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_plan_with_conflicts_is_refused(self):
        """Refuse a plan that records unresolved conflicts."""
        plan = dict(self.plan)
        plan['effects'] = {
            **plan['effects'],
            'conflict': [{'kind': 'machine', 'key': 'A01', 'code': 'REAL_TELEMETRY'}],
        }
        plan['plan_hash'] = planner.plan_hash(plan)
        from assets.demo_metrics import apply_service as apply_module

        with self.assertRaises(planner.PlanError) as caught:
            apply_module.apply_session(
                fixture=self.fixture, mapping=self.mapping, plan=plan, actor=self.actor
            )
        self.assertEqual(caught.exception.code, 'PLAN_CONFLICTS')

    def test_expired_plan_is_refused(self):
        """Refuse an expired plan before any effect."""
        plan = dict(self.plan)
        plan['expires_at'] = '2020-01-01T00:00:00Z'
        plan['plan_hash'] = planner.plan_hash(plan)
        from assets.demo_metrics import apply_service as apply_module

        with self.assertRaises(planner.PlanError) as caught:
            apply_module.apply_session(
                fixture=self.fixture, mapping=self.mapping, plan=plan, actor=self.actor
            )
        self.assertEqual(caught.exception.code, 'PLAN_EXPIRED')

    def test_actor_without_permission_is_refused(self):
        """Refuse an actor without the required permission."""
        from django.contrib.auth import get_user_model
        from django.core.exceptions import PermissionDenied

        intruder = get_user_model().objects.create_user(
            username='intruder', email='i@example.com', password='pw'
        )
        with self.assertRaises(PermissionDenied):
            apply_service.apply_session(
                fixture=self.fixture,
                mapping=self.mapping,
                plan=self.plan,
                actor=intruder,
            )
        self.assertEqual(DemoMetricsSession.objects.count(), 0)

    def test_partial_observation_batch_rolls_back_everything(self):
        """Roll back the whole apply on a partially accepted observation batch."""
        from machine_health.services import ingestion

        original = ingestion.ingest_readings

        def truncated(source, readings, *, now=None):
            result = original(source, readings[:-1], now=now)
            return result

        with mock.patch.object(ingestion, 'ingest_readings', truncated):
            with self.assertRaises(apply_service.ApplyError) as caught:
                self.apply_demo()
        self.assertEqual(caught.exception.code, 'PARTIAL_BATCH')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(MachineSignalState.objects.count(), 0)

    def test_real_telemetry_on_a_selected_machine_blocks_apply(self):
        """Block apply when a selected machine carries real telemetry bindings."""
        from assets.health_models import MachineSignalBinding

        MachineSignalBinding.objects.create(
            machine=self.machines['A01'],
            source=_real_source(),
            external_key='REAL.TAG',
            display_name='real',
        )
        with self.assertRaises(apply_service.ApplyError) as caught:
            self.apply_demo()
        self.assertEqual(caught.exception.code, 'REAL_TELEMETRY')
        self.assertEqual(DemoMetricsSession.objects.count(), 0)


def _real_source():
    """A non-session source for the real-telemetry conflict test."""
    from assets.health_models import HealthSource, SourceType

    return HealthSource.objects.create(
        name='real-scada', source_type=SourceType.SCADA, freshness_threshold_seconds=900
    )


@requires_postgres
class ReceiptIdentityTest(DemoMetricsEnvMixin, TestCase):
    """Receipts are the durable import identity."""

    def setUp(self):
        """Apply one session and load its receipt ledger."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()

    def test_exact_replay_returns_the_stored_receipt(self):
        """Return the stored receipt for an exact replay of one operation key."""
        stored = DemoMetricsReceipt.objects.get(
            session=self.session,
            operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
            item_key='WO-01',
        )
        replayed, created_replay = apply_service.claim_receipt(
            session=self.session,
            operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
            item_key='WO-01',
            digest=stored.request_hash,
        )
        self.assertFalse(created_replay)
        self.assertEqual(replayed.pk, stored.pk)

        first, created_first = apply_service.claim_receipt(
            session=self.session,
            operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
            item_key='REPLAY-PROBE',
            digest='a' * 64,
        )
        second, created_second = apply_service.claim_receipt(
            session=self.session,
            operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
            item_key='REPLAY-PROBE',
            digest='a' * 64,
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)

    def test_changed_payload_on_the_same_key_is_a_conflict(self):
        """Conflict when the same operation key carries a changed payload."""
        with self.assertRaises(apply_service.ApplyError) as caught:
            apply_service.claim_receipt(
                session=self.session,
                operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
                item_key='WO-01',
                digest='b' * 64,
            )
        self.assertEqual(caught.exception.code, 'RECEIPT_CONFLICT')


@requires_postgres
class OperatorEditPreservationTest(DemoMetricsEnvMixin, TestCase):
    """Operator edits are reported and preserved, never overwritten."""

    def setUp(self):
        """Apply one session to edit operator-visible rows."""
        self.build_demo_env()
        self.apply_demo()

    def test_modified_created_rows_are_retained_by_the_cleanup_plan(self):
        """Retain operator-modified rows in the cleanup plan instead of deleting them."""
        session = self.session()
        order_row = session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.WORK_ORDER, fixture_key='WO-01'
        )
        order_row.work_order.title = 'Operator renamed this'
        order_row.work_order.save(update_fields=['title'])
        binding_row = session.ledger_objects.get(
            kind=DemoMetricsObject.Kind.BINDING, fixture_key='A01/bearing_temperature'
        )
        binding_row.binding.warn_max = 50.0
        binding_row.binding.save(update_fields=['warn_max'])

        plan = cleanup.build_cleanup_plan(session, self.actor)
        retained = {(item['kind'], item['key']) for item in plan['retained_modified']}
        self.assertIn(('work_order', 'WO-01'), retained)
        self.assertIn(('binding', 'A01/bearing_temperature'), retained)
        deletions = {(item['kind'], item['key']) for item in plan['deletions']}
        self.assertNotIn(('work_order', 'WO-01'), deletions)
        self.assertIn(('work_order', 'WO-02'), deletions)


@requires_postgres
class ConcurrentApplyTest(DemoMetricsEnvMixin, TransactionTestCase):
    """Two concurrent applies: exactly one accepted effect set (real threads)."""

    def test_concurrent_apply_processes_create_exactly_one_session(self):
        """Create exactly one effect set across two concurrent applies."""
        import threading

        from assets.demo_metrics import contract
        from assets.demo_metrics.demo_test_support import build_mapping_dict

        self.build_demo_env()
        # Both workers replay the *same* approved artifacts (one anchor, one
        # plan hash): the race is two executions of one approved apply, just
        # like a lost-response retry racing the original.
        data = build_mapping_dict(
            self.fixture,
            machine_ids={alias: machine.pk for alias, machine in self.machines.items()},
            location_ids={alias: node.pk for alias, node in self.locations.items()},
            session_key='equa-demo-race',
            assignee_username=self.actor.username,
        )
        mapping = contract.validate_mapping(data, self.fixture, require_ready=True)
        plan = planner.sign_plan(
            planner.build_plan(
                fixture=self.fixture,
                mapping=mapping,
                actor=self.actor,
                session_anchor=timezone.now(),
                resolutions={},
                conflicts=[],
            )
        )
        results = []
        barrier = threading.Barrier(2)

        def worker():
            from django.db import connection as db_connection

            try:
                barrier.wait(timeout=10)
                result = apply_service.apply_session(
                    fixture=self.fixture, mapping=mapping, plan=plan, actor=self.actor
                )
                results.append('reconciled' if result.reconciled else 'applied')
            except apply_service.ApplyError as exc:
                results.append(exc.code)
            except Exception as exc:
                results.append(type(exc).__name__)
            finally:
                db_connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(sorted(results), ['applied', 'reconciled'])
        self.assertEqual(
            DemoMetricsSession.objects.filter(session_key='equa-demo-race').count(), 1
        )
        session = DemoMetricsSession.objects.get(session_key='equa-demo-race')
        self.assertEqual(session.ledger_objects.count(), 26)
        self.assertEqual(WorkOrder.objects.count(), 9)


class BootstrapClassificationTest(TestCase):
    """Command classifications keep startup free of unrelated writes."""

    def test_plan_and_verify_are_read_only_commands(self):
        """Classify plan and verify as read-only commands."""
        from InvenTree import ready

        for command in ('plan_demo_metrics', 'verify_demo_metrics'):
            with mock.patch('sys.argv', ['manage.py', command]):
                self.assertTrue(ready.isReadOnlyCommand())
                self.assertFalse(ready.canAppAccessDatabase())

    def test_cleanup_is_read_only_without_apply_and_mutating_with_it(self):
        """Classify cleanup as read-only without --apply and mutating with it."""
        from InvenTree import ready

        with mock.patch('sys.argv', ['manage.py', 'cleanup_demo_metrics']):
            self.assertTrue(ready.isReadOnlyCommand())
        with mock.patch('sys.argv', ['manage.py', 'cleanup_demo_metrics', '--apply']):
            self.assertFalse(ready.isReadOnlyDemoMetricsCommand())

    def test_mutating_commands_skip_unrelated_startup_writes(self):
        """Skip unrelated startup writes for mutating demo commands."""
        from InvenTree import ready

        for command in (
            'apply_demo_metrics',
            'replay_demo_metrics',
            'stop_demo_metrics',
        ):
            with mock.patch('sys.argv', ['manage.py', command]):
                self.assertFalse(ready.isReadOnlyCommand())
                self.assertFalse(ready.canAppAccessDatabase())

    def test_unrelated_commands_are_unaffected(self):
        """Leave unrelated commands classified as usual."""
        from InvenTree import ready

        with mock.patch('sys.argv', ['manage.py', 'runserver']):
            self.assertFalse(ready.isReadOnlyCommand())


def _plan_scope_resolver(actor):
    """Deployment-style scope resolver: the internal client's scope only.

    Command actors are loaded fresh from the database and never carry the
    in-memory ``maintenance_scopes`` attribute, so the plan command resolves
    its actor's scope through this deployment-shaped resolver.
    """
    from tasks.scope import MaintenanceScope

    from assets.models import Client

    client = Client.objects.get(name='Internal')
    return {MaintenanceScope(customer_id=None, site_key=None, client_id=client.pk)}


@requires_postgres
@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_plan_scope_resolver)
class PlanCommandReadOnlyTest(DemoMetricsEnvMixin, TestCase):
    """plan_demo_metrics performs no database writes at all."""

    def test_plan_command_issues_no_insert_update_or_delete(self):
        """Issue no INSERT, UPDATE or DELETE while planning."""
        import os
        import tempfile

        self.build_demo_env()
        statements = []

        def capture(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with tempfile.TemporaryDirectory() as folder:
            mapping_path = os.path.join(folder, 'mapping.json')
            plan_path = os.path.join(folder, 'plan.json')
            with open(mapping_path, 'w', encoding='utf-8') as stream:
                json.dump(self.mapping_data, stream)
            with connection.execute_wrapper(capture):
                call_command(
                    'plan_demo_metrics',
                    '--fixture',
                    str(FIXTURE_PATH),
                    '--mapping',
                    mapping_path,
                    '--out',
                    plan_path,
                    '--actor',
                    self.actor.username,
                )
            writes = [
                sql
                for sql in statements
                if sql.strip().split(' ', 1)[0].upper()
                in {'INSERT', 'UPDATE', 'DELETE'}
            ]
            self.assertEqual(writes, [])
            self.assertTrue(os.path.exists(plan_path))


@requires_postgres
class ApplyCommandApprovalTest(DemoMetricsEnvMixin, TestCase):
    """The apply command enforces the approved plan hash."""

    def test_wrong_approval_hash_is_refused_before_effects(self):
        """Refuse a wrong approval hash before any effect."""
        import os
        import tempfile

        from assets.demo_metrics import planner as planner_module

        self.build_demo_env()
        with tempfile.TemporaryDirectory() as folder:
            mapping_path = os.path.join(folder, 'mapping.json')
            plan_path = os.path.join(folder, 'plan.json')
            with open(mapping_path, 'w', encoding='utf-8') as stream:
                json.dump(self.mapping_data, stream)
            planner_module.write_plan(plan_path, self.plan_body)
            with self.assertRaises(CommandError) as caught:
                call_command(
                    'apply_demo_metrics',
                    '--fixture',
                    str(FIXTURE_PATH),
                    '--mapping',
                    mapping_path,
                    '--plan',
                    plan_path,
                    '--approved-plan-sha256',
                    '0' * 64,
                    '--actor',
                    self.actor.username,
                )
        self.assertIn('APPROVAL_MISMATCH', str(caught.exception))
        self.assertEqual(DemoMetricsSession.objects.count(), 0)
