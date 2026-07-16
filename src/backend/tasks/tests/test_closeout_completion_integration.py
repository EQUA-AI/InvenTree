"""Completion-boundary integration: capture consumption, blockers, effects."""

from unittest import mock

from django.test import TestCase, override_settings

from tasks.closeout_models import (
    CloseoutCapture,
    CloseoutCaptureStatus,
    CloseoutEffect,
    CloseoutEffectStatus,
)
from tasks.models import WorkOrderCloseout, WorkOrderLifecycle
from tasks.services.closeout import complete_work_order
from tasks.services.closeout_capture import (
    CaptureError,
    DecisionRequired,
    create_capture,
    record_decisions,
)
from tasks.services.closeout_reconcile import record_reading
from tasks.services.job_kit_custody import issue_allocation
from tasks.services.readiness import evaluate_work_order_readiness
from tasks.services.work_orders import ReadinessBlocked
from tasks.tests.closeout_fixtures import (
    CLOSEOUT_FLAGS,
    VALID_CLOSEOUT,
    CloseoutEnvMixin,
)


@override_settings(**CLOSEOUT_FLAGS)
class CompletionWithCaptureTest(CloseoutEnvMixin, TestCase):
    """The wizard drives the one existing completion transaction."""

    def setUp(self):
        self.build_env(username='complete-cap')

    def reviewed_capture(self, payload=None):
        payload = payload or VALID_CLOSEOUT
        created = create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative='Replaced clogged filter; flow verified at 20 GPM.',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='cap-cc',
        )
        capture_id = created.metadata['capture_id']
        record_decisions(
            work_order_id=self.work_order.pk,
            capture_id=capture_id,
            actor=self.actor,
            decisions=[
                {
                    'field_path': name,
                    'decision': 'edited',
                    'final_value': payload[name],
                }
                for name in ('action', 'result', 'verification_summary')
            ],
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='dec-cc',
        )
        return capture_id

    def complete(self, key='complete-1', capture_id=None, closeout=None):
        return complete_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
            closeout=closeout or VALID_CLOSEOUT,
            capture_id=capture_id,
        )

    def test_completion_consumes_reviewed_capture_atomically(self):
        capture_id = self.reviewed_capture()
        result = self.complete(capture_id=capture_id)
        self.assertEqual(result.lifecycle_status, WorkOrderLifecycle.COMPLETED)
        capture = CloseoutCapture.objects.get(pk=capture_id)
        self.assertEqual(capture.status, CloseoutCaptureStatus.CONSUMED)
        closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)
        self.assertEqual(capture.completed_closeout_id, closeout.pk)
        self.assertEqual(closeout.source_capture.pk, capture.pk)

    def test_completion_creates_effect_intents_in_transaction(self):
        capture_id = self.reviewed_capture()
        self.complete(capture_id=capture_id)
        closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)
        effects = CloseoutEffect.objects.filter(closeout=closeout)
        self.assertEqual(effects.count(), 1)
        effect = effects.get()
        self.assertEqual(effect.effect_type, 'notification')
        self.assertEqual(
            effect.effect_key, f'closeout:{closeout.pk}:notification:v1'
        )
        self.assertEqual(effect.status, CloseoutEffectStatus.PENDING)

    @override_settings(AIMMS_CLOSEOUT_LEARNING_ENABLED=True)
    def test_learning_flag_adds_memory_draft_intent(self):
        capture_id = self.reviewed_capture()
        self.complete(capture_id=capture_id)
        closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)
        self.assertEqual(
            set(
                CloseoutEffect.objects.filter(closeout=closeout).values_list(
                    'effect_type', flat=True
                )
            ),
            {'notification', 'memory_draft'},
        )

    def test_replay_returns_identical_receipt_without_duplicates(self):
        capture_id = self.reviewed_capture()
        first = self.complete(key='same', capture_id=capture_id)
        replay = self.complete(key='same', capture_id=capture_id)
        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)
        self.assertEqual(CloseoutEffect.objects.count(), 1)

    def test_unreviewed_capture_blocks_completion_via_readiness(self):
        create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative='still typing...',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='cap-open',
        )
        with self.assertRaises(ReadinessBlocked) as caught:
            self.complete()
        codes = [blocker.code for blocker in caught.exception.readiness.blockers]
        self.assertIn('CLOSEOUT_REQUIRED', codes)

    def test_payload_diverging_from_decisions_is_rejected(self):
        capture_id = self.reviewed_capture()
        divergent = dict(VALID_CLOSEOUT, action='Something else entirely')
        with self.assertRaises(DecisionRequired):
            self.complete(capture_id=capture_id, closeout=divergent)
        capture = CloseoutCapture.objects.get(pk=capture_id)
        self.assertEqual(capture.status, CloseoutCaptureStatus.REVIEWED)
        self.assertFalse(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).exists()
        )

    def test_foreign_capture_is_rejected(self):
        with self.assertRaises(CaptureError):
            self.complete(capture_id=987654)

    @override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False)
    def test_capture_id_fails_closed_when_wizard_disabled(self):
        with self.assertRaises(CaptureError):
            self.complete(capture_id=1)

    @override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False)
    def test_manual_contract_unchanged_with_wizard_disabled(self):
        result = self.complete()
        self.assertEqual(result.lifecycle_status, WorkOrderLifecycle.COMPLETED)
        self.assertEqual(CloseoutEffect.objects.count(), 0)

    @override_settings(AIMMS_CLOSEOUT_EFFECTS_ENABLED=True)
    def test_effects_execute_after_commit_never_inside(self):
        capture_id = self.reviewed_capture()
        with mock.patch(
            'common.notifications.trigger_notification'
        ) as trigger:
            with self.captureOnCommitCallbacks(execute=True):
                self.complete(capture_id=capture_id)
                # Inside the transaction the intent exists but has not run.
                effect = CloseoutEffect.objects.get()
                self.assertEqual(effect.status, CloseoutEffectStatus.PENDING)
                trigger.assert_not_called()
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.SUCCEEDED)
        trigger.assert_called_once()


@override_settings(**CLOSEOUT_FLAGS)
class CompletionBlockerTest(CloseoutEnvMixin, TestCase):
    """Each additive readiness code independently blocks completion."""

    def setUp(self):
        self.build_env(username='blocker-user')

    def complete(self, key='blocked-1'):
        return complete_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
            closeout=VALID_CLOSEOUT,
        )

    def blocker_codes(self):
        readiness = evaluate_work_order_readiness(
            self.work_order, action='complete', actor=self.actor
        )
        return [blocker.code for blocker in readiness.blockers], [
            warning.code for warning in readiness.warnings
        ]

    def test_required_unresolved_reading_blocks(self):
        record_reading(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            label='Final pressure',
            raw_text='forty–fifty',
            required=True,
        )
        with self.assertRaises(ReadinessBlocked) as caught:
            self.complete()
        codes = [blocker.code for blocker in caught.exception.readiness.blockers]
        self.assertIn('VERIFICATION_REQUIRED', codes)

    def test_unreconciled_allocation_warns_then_enforces(self):
        self.build_kit_line()
        self.reserve_kit()
        blocking, warnings = self.blocker_codes()
        self.assertNotIn('PART_VARIANCE_UNRESOLVED', blocking)
        self.assertIn('PART_VARIANCE_UNRESOLVED', warnings)
        with override_settings(AIMMS_CLOSEOUT_RECON_ENFORCED=True):
            blocking, _warnings = self.blocker_codes()
            self.assertIn('PART_VARIANCE_UNRESOLVED', blocking)
            with self.assertRaises(ReadinessBlocked):
                self.complete()

    @override_settings(AIMMS_CLOSEOUT_RECON_ENFORCED=True)
    def test_issued_tool_blocks_until_returned(self):
        from tasks.models import JobKitAllocation, ProcedureResourceKind

        line = self.build_kit_line(kind=ProcedureResourceKind.TOOL, quantity='1')
        self.reserve_kit()
        allocation = JobKitAllocation.objects.get(line=line)
        issue_allocation(
            work_order_id=self.work_order.pk,
            allocation_id=allocation.pk,
            actor=self.actor,
        )
        blocking, _warnings = self.blocker_codes()
        self.assertIn('TOOL_RETURN_REQUIRED', blocking)

    def test_flags_off_mean_no_new_blockers(self):
        record_reading(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            label='Final pressure',
            raw_text='forty–fifty',
            required=True,
        )
        with override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False):
            blocking, warnings = self.blocker_codes()
            self.assertEqual(blocking, [])
            self.assertEqual(warnings, [])
