"""Executor completeness, exactly-once dispatch and honest recovery outcomes."""

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from company.models import Company

from . import services
from .execution import preflight, reconcile_execution
from .executors import EffectResult, PurchaseOrderExecutor, registry
from .models import (
    ActionType,
    Approval,
    ApprovalExecution,
    ApprovalStatus,
    ExecutedEffect,
)
from .testing import RecordingPurchaseExecutor
from .tests import ApprovalTestBase


class ApprovalExecutionTests(ApprovalTestBase):
    """Recording executors are local test doubles, never live configuration."""

    def setUp(self):
        """Open a request with legacy screen acknowledgment for execution tests."""
        super().setUp()
        self.approval = self._create_approval_obj()
        services.open_approval(self.approval.pk, actor=self.user)
        services.confirm_viewed(self.approval.pk, actor=self.user)
        self.approval.refresh_from_db()

    def test_placeholder_is_failed_before_effect_and_never_dispatched(self):
        """Registration by itself is not permission to claim an effect."""
        placeholder = PurchaseOrderExecutor()
        with (
            registry.replace_for_tests(placeholder),
            patch.object(placeholder, 'execute') as execute,
        ):
            result = services.approve(self.approval.pk, actor=self.user)
            execute.assert_not_called()
        self.assertEqual(result.data['status'], ApprovalStatus.FAILED)
        self.assertEqual(self.approval.executions.get().state, 'failed_before_effect')
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    def test_every_placeholder_direct_call_is_unsuccessful(self):
        """All six legacy placeholders stop manufacturing stub receipts."""
        from .executors import (
            EmailExecutor,
            NotificationExecutor,
            SalesOrderExecutor,
            StockUpdateExecutor,
            WorkflowExecutor,
        )

        for executor in [
            EmailExecutor(),
            PurchaseOrderExecutor(),
            SalesOrderExecutor(),
            StockUpdateExecutor(),
            WorkflowExecutor(),
            NotificationExecutor(),
        ]:
            with self.subTest(action=executor.action_type):
                result = executor.execute({}, 'recording-key')
                self.assertFalse(result.success)
                self.assertIsNone(result.effect_ref)
                self.assertEqual(result.outcome, 'failed_before_effect')

    def test_validation_failure_never_dispatches(self):
        """An invalid payload gets a durable failed-before-effect result."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with (
            patch.object(executor, 'validate', return_value=['Missing reviewed lines']),
            patch.object(executor, 'execute') as execute,
        ):
            result = services.approve(self.approval.pk, actor=self.user)
            execute.assert_not_called()
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )

    def test_operation_is_persisted_before_call_and_reviewer_is_authoritative(self):
        """The provider seam can observe its durable key before any effect."""
        self.approval.payload['actor_id'] = self.user2.pk
        self.approval.payload['requested_by_id'] = self.user2.pk
        self.approval.save(update_fields=['payload'])
        executor = registry.get(ActionType.PURCHASE_ORDER)

        def execute(payload, key):
            operation = ApprovalExecution.objects.get(pk=key)
            self.assertEqual(operation.state, 'submitting')
            self.assertEqual(operation.actor_id, self.user.pk)
            self.assertEqual(payload['actor_id'], self.user.pk)
            self.assertEqual(payload['requested_by_id'], self.user.pk)
            return EffectResult(True, 'provider-recorded-1')

        with patch.object(executor, 'execute', side_effect=execute) as call:
            services.approve(self.approval.pk, actor=self.user)
            services.approve(self.approval.pk, actor=self.user)
            call.assert_called_once()
        self.assertEqual(self.approval.executions.get().state, 'succeeded')
        self.assertEqual(
            ExecutedEffect.objects.filter(approval=self.approval).count(), 1
        )

    def test_timeout_is_unknown_and_second_approve_does_not_resend(self):
        """Possible dispatch cannot be presented as failed or safe to retry."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(
            executor, 'execute', side_effect=TimeoutError('response lost')
        ) as call:
            result = services.approve(self.approval.pk, actor=self.user)
            replay = services.approve(self.approval.pk, actor=self.user)
            call.assert_called_once()
        self.assertEqual(result.data['status'], ApprovalStatus.EXECUTING)
        self.assertEqual(result.data['execution_result']['execution_state'], 'unknown')
        self.assertEqual(
            replay.data['execution_result']['operation_id'],
            result.data['execution_result']['operation_id'],
        )
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    def test_unspecified_failed_result_is_unknown(self):
        """A generic success=false is not proof that the provider did nothing."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(
            executor,
            'execute',
            return_value=EffectResult(False, error_message='Response unavailable'),
        ):
            services.approve(self.approval.pk, actor=self.user)
        self.assertEqual(self.approval.executions.get().state, 'unknown')

    def test_explicit_pre_provider_failure_is_terminal_without_effect(self):
        """Only the executor's proven pre-dispatch failure can report no effect."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(
            executor,
            'execute',
            return_value=EffectResult(
                False,
                error_message='Provider client setup failed',
                outcome='failed_before_effect',
            ),
        ):
            result = services.approve(self.approval.pk, actor=self.user)
        self.assertEqual(result.data['status'], ApprovalStatus.FAILED)
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    def test_partial_stays_partial_until_authoritative_reconciliation(self):
        """A missing later provider response does not erase known partial state."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(
            executor,
            'execute',
            return_value=EffectResult(
                False, result_payload={'accepted_lines': 1}, outcome='partial'
            ),
        ):
            services.approve(self.approval.pk, actor=self.user)
        with patch.object(executor, 'execute') as execute:
            reconcile_execution(self.approval.pk)
            execute.assert_not_called()
        self.assertEqual(self.approval.executions.get().state, 'partial')
        self.assertEqual(
            self.approval.executions.get().result['payload']['accepted_lines'], 1
        )
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    def test_unknown_reconciles_to_verified_success_without_dispatch(self):
        """Provider lookup may prove success but must never resend the request."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(executor, 'execute', side_effect=TimeoutError):
            services.approve(self.approval.pk, actor=self.user)
        with (
            patch.object(executor, 'execute') as execute,
            patch.object(
                executor,
                'reconcile',
                return_value=EffectResult(True, 'authoritative-provider-id'),
            ),
        ):
            result = reconcile_execution(self.approval.pk)
            execute.assert_not_called()
        self.assertEqual(result.status, ApprovalStatus.SUCCEEDED)
        self.assertEqual(
            self.approval.executed_effects.get().effect_ref, 'authoritative-provider-id'
        )

    def test_stub_shaped_success_is_unverified_not_sent(self):
        """A supposedly real executor cannot smuggle an old placeholder receipt."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(
            executor,
            'execute',
            return_value=EffectResult(True, 'stub-po-123', {'stub': True}),
        ):
            result = services.approve(self.approval.pk, actor=self.user)
        self.assertEqual(result.data['execution_result']['execution_state'], 'unknown')
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    def test_database_effect_and_receipt_commit_together(self):
        """A pure database command records its authoritative result atomically."""
        executor = RecordingPurchaseExecutor()
        executor.atomic_execution = True

        def execute(payload, key):
            company = Company.objects.create(name='VOICE-TEST atomic receipt')
            return EffectResult(
                True, f'company-{company.pk}', {'company_id': company.pk}
            )

        with (
            registry.replace_for_tests(executor),
            patch.object(executor, 'execute', side_effect=execute),
        ):
            services.approve(self.approval.pk, actor=self.user)
        self.assertTrue(
            Company.objects.filter(name='VOICE-TEST atomic receipt').exists()
        )
        self.assertEqual(self.approval.executions.get().state, 'succeeded')

    def test_database_partial_writes_roll_back_before_failure_receipt(self):
        """Database-only exceptions roll back partial writes and report that fact."""
        executor = RecordingPurchaseExecutor()
        executor.atomic_execution = True

        def execute(payload, key):
            Company.objects.create(name='VOICE-TEST must roll back')
            raise RuntimeError('Injected failure after one database write')

        with (
            registry.replace_for_tests(executor),
            patch.object(executor, 'execute', side_effect=execute),
        ):
            result = services.approve(self.approval.pk, actor=self.user)
        self.assertFalse(
            Company.objects.filter(name='VOICE-TEST must roll back').exists()
        )
        self.assertEqual(
            result.data['execution_result']['execution_state'], 'failed_before_effect'
        )
        self.assertFalse(ExecutedEffect.objects.filter(approval=self.approval).exists())

    @override_settings(
        APPROVAL_QUEUE_ENABLED=True, APPROVAL_EXECUTION_STUCK_THRESHOLD_SECONDS=1
    )
    def test_background_sweep_keeps_unknown_unknown(self):
        """Elapsed time is not proof of a failed external effect."""
        from .tasks import reconcile_approvals

        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(executor, 'execute', side_effect=TimeoutError):
            services.approve(self.approval.pk, actor=self.user)
        Approval.objects.filter(pk=self.approval.pk).update(
            updated_at=timezone.now() - timedelta(hours=1)
        )
        with patch.object(executor, 'execute') as execute:
            reconcile_approvals()
            execute.assert_not_called()
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.status, ApprovalStatus.EXECUTING)
        self.assertEqual(self.approval.executions.get().state, 'unknown')

    def test_historical_stub_receipt_is_untouched(self):
        """Legacy audit treatment is not silently rewritten by reconciliation."""
        self.approval.status = ApprovalStatus.SUCCEEDED
        self.approval.execution_result = {'stub': True}
        self.approval.save(update_fields=['status', 'execution_result'])
        effect = ExecutedEffect.objects.create(
            idempotency_key='historic-stub',
            approval=self.approval,
            effect_type='purchase_order',
            effect_ref='stub-po-old',
        )
        reconcile_execution(self.approval.pk)
        effect.refresh_from_db()
        self.approval.refresh_from_db()
        self.assertEqual(effect.effect_ref, 'stub-po-old')
        self.assertEqual(self.approval.execution_result, {'stub': True})

    def test_malformed_email_payload_is_failed_before_effect(self):
        """Legacy malformed JSON cannot crash the pre-provider safety check."""
        self.approval.action_type = ActionType.EMAIL
        self.approval.payload = ['invalid']
        result = preflight(registry.get(ActionType.EMAIL), self.approval)
        self.assertEqual(result.outcome, 'failed_before_effect')

    def test_invalid_provider_result_is_unknown(self):
        """An absent result is not a confirmed failure or a success receipt."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(executor, 'execute', return_value=None):
            services.approve(self.approval.pk, actor=self.user)
        self.assertEqual(self.approval.executions.get().state, 'unknown')

    def test_database_partial_result_rolls_back(self):
        """A returned partial result rolls back just like a raised exception."""
        executor = RecordingPurchaseExecutor()
        executor.atomic_execution = True

        def execute(payload, key):
            Company.objects.create(name='VOICE-TEST partial rollback')
            return EffectResult(False, outcome='partial')

        with (
            registry.replace_for_tests(executor),
            patch.object(executor, 'execute', side_effect=execute),
        ):
            services.approve(self.approval.pk, actor=self.user)
        self.assertFalse(
            Company.objects.filter(name='VOICE-TEST partial rollback').exists()
        )
        self.assertEqual(self.approval.executions.get().state, 'failed_before_effect')

    @override_settings(
        APPROVAL_RETENTION_PURGE_ENABLED=True, APPROVAL_RETENTION_DAYS=90
    )
    def test_retention_preserves_evidence_without_blocking_legacy_purge(self):
        """Protected dispatch audits cannot abort the whole retention batch."""
        from .tasks import purge_expired_approvals

        services.approve(self.approval.pk, actor=self.user)
        legacy = self._create_approval_obj(idempotency_key='retention-legacy')
        Approval.objects.filter(pk__in=[self.approval.pk, legacy.pk]).update(
            status=ApprovalStatus.SUCCEEDED,
            resolved_at=timezone.now() - timedelta(days=91),
        )
        purge_expired_approvals()
        self.assertTrue(Approval.objects.filter(pk=self.approval.pk).exists())
        self.assertEqual(self.approval.executions.get().state, 'succeeded')
        self.assertFalse(Approval.objects.filter(pk=legacy.pk).exists())
