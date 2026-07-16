"""Effect-ledger tests: leases, retries, unknown outcomes, executors."""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.closeout_models import (
    CloseoutEffect,
    CloseoutEffectStatus,
    CloseoutLearningDraft,
    new_effect_key,
)
from tasks.services import closeout_effects
from tasks.services.closeout import complete_work_order
from tasks.services.closeout_effects import (
    EffectNotRetryable,
    EffectOutcomeUnknown,
    abandon_effect,
    execute_pending_effects,
    release_expired_leases,
    resolve_unknown_outcome,
    retry_effect,
    sweep_closeout_effects,
)
from tasks.tests.closeout_fixtures import (
    CLOSEOUT_FLAGS,
    VALID_CLOSEOUT,
    CloseoutEnvMixin,
)

EFFECT_FLAGS = dict(CLOSEOUT_FLAGS, AIMMS_CLOSEOUT_EFFECTS_ENABLED=True)


@override_settings(**EFFECT_FLAGS)
class EffectLedgerTest(CloseoutEnvMixin, TestCase):
    """Ledger state machine with a controllable fake executor."""

    def setUp(self):
        self.build_env(username='effects-user')
        complete_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='complete-fx',
            closeout=VALID_CLOSEOUT,
        )
        self.closeout = self.work_order.structured_closeout
        self.calls = []
        self._original = dict(closeout_effects.EFFECT_EXECUTORS)
        closeout_effects.EFFECT_EXECUTORS['test_effect'] = self._executor
        self.behavior = lambda effect: 'ok'

    def tearDown(self):
        closeout_effects.EFFECT_EXECUTORS.clear()
        closeout_effects.EFFECT_EXECUTORS.update(self._original)

    def _executor(self, effect):
        self.calls.append(effect.effect_key)
        return self.behavior(effect)

    def make_effect(self, key='k1'):
        return CloseoutEffect.objects.create(
            closeout=self.closeout,
            effect_type='test_effect',
            effect_key=f'closeout:{self.closeout.pk}:test:{key}',
            payload_hash=self.closeout.content_hash,
        )

    def test_success_path(self):
        effect = self.make_effect()
        processed = execute_pending_effects()
        self.assertGreaterEqual(processed, 1)
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.SUCCEEDED)
        self.assertEqual(effect.result_reference, 'ok')
        self.assertEqual(effect.attempts, 1)
        self.assertIsNotNone(effect.resolved_at)

    def test_execution_is_idempotent_once_succeeded(self):
        self.make_effect()
        execute_pending_effects()
        execute_pending_effects()
        self.assertEqual(len(self.calls), 1)

    def test_retryable_failure_backs_off_then_respects_due_time(self):
        effect = self.make_effect()

        def boom(_effect):
            raise RuntimeError('provider 500')

        self.behavior = boom
        execute_pending_effects()
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.RETRYABLE)
        self.assertEqual(effect.attempts, 1)
        self.assertGreater(effect.next_retry_at, timezone.now())
        # Not due yet: another sweep must not touch it.
        execute_pending_effects()
        self.assertEqual(len(self.calls), 1)
        # Due: it runs again.
        CloseoutEffect.objects.filter(pk=effect.pk).update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        self.behavior = lambda effect: 'recovered'
        execute_pending_effects()
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.SUCCEEDED)

    @override_settings(AIMMS_CLOSEOUT_EFFECT_RETRY_LIMIT=2)
    def test_definitive_failure_at_retry_limit(self):
        effect = self.make_effect()

        def boom(_effect):
            raise RuntimeError('always down')

        self.behavior = boom
        for _round in range(2):
            CloseoutEffect.objects.filter(pk=effect.pk).update(next_retry_at=None)
            execute_pending_effects()
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.FAILED)
        self.assertIn('always down', effect.last_error)

    def test_unknown_outcome_stops_automatic_replay(self):
        effect = self.make_effect()

        def ambiguous(_effect):
            raise EffectOutcomeUnknown('timeout after possible acceptance')

        self.behavior = ambiguous
        execute_pending_effects()
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.OUTCOME_UNKNOWN)
        self.assertIsNotNone(effect.reconciliation_due_at)
        # Sweeps never blind-replay an ambiguous dispatch.
        sweep_closeout_effects()
        self.assertEqual(len(self.calls), 1)
        # Manual retry is also refused.
        with self.assertRaises(EffectNotRetryable):
            retry_effect(effect_id=effect.pk, actor=self.actor)

    def test_unknown_outcome_reconciles_with_evidence(self):
        effect = self.make_effect()
        self.behavior = lambda e: (_ for _ in ()).throw(
            EffectOutcomeUnknown('maybe')
        )
        execute_pending_effects()
        resolved = resolve_unknown_outcome(
            effect_id=effect.pk,
            actor=self.actor,
            succeeded=True,
            evidence='provider receipt 123',
        )
        self.assertEqual(resolved.status, CloseoutEffectStatus.SUCCEEDED)

    def test_expired_lease_recovers_to_retryable(self):
        effect = self.make_effect()
        CloseoutEffect.objects.filter(pk=effect.pk).update(
            status=CloseoutEffectStatus.LEASED,
            lease_owner='dead-worker',
            lease_expires_at=timezone.now() - timedelta(minutes=10),
        )
        recovered = release_expired_leases()
        self.assertEqual(recovered, 1)
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.RETRYABLE)
        sweep_closeout_effects()
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.SUCCEEDED)

    def test_manual_retry_and_abandon_rules(self):
        effect = self.make_effect()
        CloseoutEffect.objects.filter(pk=effect.pk).update(
            status=CloseoutEffectStatus.FAILED
        )
        retried = retry_effect(effect_id=effect.pk, actor=self.actor)
        self.assertEqual(retried.status, CloseoutEffectStatus.PENDING)
        with self.assertRaises(EffectNotRetryable):
            abandon_effect(effect_id=effect.pk, actor=self.actor, reason='')
        abandoned = abandon_effect(
            effect_id=effect.pk, actor=self.actor, reason='target retired'
        )
        self.assertEqual(abandoned.status, CloseoutEffectStatus.ABANDONED)
        with self.assertRaises(EffectNotRetryable):
            retry_effect(effect_id=effect.pk, actor=self.actor)

    def test_retry_requires_authority(self):
        effect = self.make_effect()
        CloseoutEffect.objects.filter(pk=effect.pk).update(
            status=CloseoutEffectStatus.FAILED
        )
        technician = self.make_scoped_user(
            'effects-tech', permissions=['capture_closeout']
        )
        with self.assertRaises(PermissionDenied):
            retry_effect(effect_id=effect.pk, actor=technician)

    @override_settings(AIMMS_CLOSEOUT_EFFECTS_ENABLED=False)
    def test_disabled_executors_preserve_rows(self):
        effect = self.make_effect()
        self.assertEqual(execute_pending_effects(), 0)
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.PENDING)

    def test_failed_effect_never_touches_the_completion(self):
        effect = self.make_effect()

        def boom(_effect):
            raise RuntimeError('down')

        self.behavior = boom
        execute_pending_effects()
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, 'completed')
        self.assertIsNotNone(self.work_order.structured_closeout)
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.RETRYABLE)


@override_settings(**EFFECT_FLAGS)
class RealExecutorTest(CloseoutEnvMixin, TestCase):
    """The two shipped executors: notification and governed learning draft."""

    def setUp(self):
        self.build_env(username='real-fx')
        complete_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='complete-real-fx',
            closeout=VALID_CLOSEOUT,
        )
        self.closeout = self.work_order.structured_closeout

    def test_notification_executor_targets_workorder_people(self):
        with mock.patch('common.notifications.trigger_notification') as trigger:
            execute_pending_effects()
        trigger.assert_called_once()
        _args, kwargs = trigger.call_args
        self.assertIn(self.actor, kwargs['targets'])
        effect = CloseoutEffect.objects.get(effect_type='notification')
        self.assertEqual(effect.status, CloseoutEffectStatus.SUCCEEDED)

    def test_memory_draft_executor_creates_governed_draft_once(self):
        effect = CloseoutEffect.objects.create(
            closeout=self.closeout,
            effect_type='memory_draft',
            effect_key=new_effect_key(self.closeout.pk, 'memory_draft'),
            payload_hash=self.closeout.content_hash,
        )
        with mock.patch('common.notifications.trigger_notification'):
            execute_pending_effects()
        draft = CloseoutLearningDraft.objects.get(closeout=self.closeout)
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(draft.payload['action'], VALID_CLOSEOUT['action'])
        self.assertEqual(draft.provenance['closeout_id'], self.closeout.pk)
        effect.refresh_from_db()
        self.assertEqual(effect.status, CloseoutEffectStatus.SUCCEEDED)
        # Replaying the effect key never duplicates the draft.
        CloseoutEffect.objects.filter(pk=effect.pk).update(
            status=CloseoutEffectStatus.PENDING
        )
        with mock.patch('common.notifications.trigger_notification'):
            execute_pending_effects()
        self.assertEqual(CloseoutLearningDraft.objects.count(), 1)

    def test_deterministic_keys_collapse_duplicates(self):
        key = new_effect_key(self.closeout.pk, 'memory_draft')
        CloseoutEffect.objects.get_or_create(
            effect_key=key,
            defaults={
                'closeout': self.closeout,
                'effect_type': 'memory_draft',
                'payload_hash': self.closeout.content_hash,
            },
        )
        CloseoutEffect.objects.get_or_create(
            effect_key=key,
            defaults={
                'closeout': self.closeout,
                'effect_type': 'memory_draft',
                'payload_hash': self.closeout.content_hash,
            },
        )
        self.assertEqual(
            CloseoutEffect.objects.filter(effect_key=key).count(), 1
        )
