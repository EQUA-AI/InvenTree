"""Actor/revision/scope binding, completed-page evidence and policy fail-closed."""

import hashlib
import os
from dataclasses import replace
from unittest.mock import patch

from django.test import override_settings

from aichat.services.scope_strings import scope_strings
from voice.models import PlaybackState, VoiceSession, VoiceUtterance

from . import services
from .executors import DriftReport, EffectResult, registry
from .models import ActionType, Approval, ApprovalReviewAcknowledgment, ApprovalStatus
from .policy import ACTION_POLICIES, ApprovalPolicyError, require_policy
from .review_evidence import (
    ReviewEvidenceError,
    invalidate,
    record_delivery,
    require_acknowledgment,
    required_sections,
    review_units,
)
from .review_sections import compute_review_hash
from .tests import ApprovalTestBase


def pilot_scopes(actor):
    """Resolve a fixed, local-only client scope for permission-binding tests."""
    return [{'client_id': 1}]


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=pilot_scopes)
class ReviewBindingTests(ApprovalTestBase):
    """No provider is contacted by these service/ledger tests."""

    def setUp(self):
        """Create one revision and an owned voice session."""
        super().setUp()
        self.approval = self._create_approval_obj(assigned_to_user_id=self.user.pk)
        services.open_approval(self.approval.pk, actor=self.user)
        self.approval.refresh_from_db()
        scope_key, scope_hash = scope_strings(self.user)
        self.session = VoiceSession.objects.create(
            owner=self.user,
            thread_id='review-test',
            scope_key=scope_key,
            scope_hash=scope_hash,
            policy_version='test',
        )

    def evidence(self):
        """An acknowledgment asserts focus, not that playback happened."""
        return {
            'revision': self.approval.current_revision_number,
            'review_hash': compute_review_hash(self.approval),
            'sections': required_sections(self.approval),
            'acknowledgment': 'I have reviewed this request',
        }

    def deliver(self, *, state=PlaybackState.DONE):
        """Persist exact review pages and separately simulate playback state."""
        utterances = []
        for unit in review_units(self.approval):
            utterance = VoiceUtterance.objects.create(
                session=self.session,
                utterance_type='prompt',
                policy_version='test',
                spoken_summary=unit['text'],
                spoken_summary_hash=hashlib.sha256(unit['text'].encode()).hexdigest(),
                playback_state=state,
            )
            record_delivery(
                self.approval,
                actor=self.user,
                utterance=utterance,
                unit_ids=[unit['id']],
            )
            utterances.append(utterance)
        return utterances

    def test_flag_off_preserves_screen_but_voice_requires_bound_review(self):
        """Legacy screen timestamps never confer voice authority."""
        services.confirm_viewed(self.approval.pk, actor=self.user)
        self.approval.refresh_from_db()
        require_acknowledgment(self.approval, actor=self.user2, channel='screen')
        with self.assertRaises(ReviewEvidenceError):
            require_acknowledgment(self.approval, actor=self.user, channel='voice')
        self.assertFalse(ApprovalReviewAcknowledgment.objects.exists())

    @override_settings(APPROVAL_REVIEW_REVISION_BOUND=True)
    def test_screen_requires_current_actor_revision_hash_and_sections(self):
        """Each forged or stale evidence dimension is refused."""
        for changes in [
            {},
            {'revision': 99},
            {'revision': True},
            {'review_hash': 'a' * 64},
            {'sections': []},
            {'actor_id': self.user2.pk},
        ]:
            evidence = {**self.evidence(), **changes} if changes else {}
            with (
                self.subTest(changes=changes),
                self.assertRaises(services.ApprovalConflictError),
            ):
                services.confirm_viewed(
                    self.approval.pk, actor=self.user, evidence=evidence
                )
        services.confirm_viewed(
            self.approval.pk, actor=self.user, evidence=self.evidence()
        )
        require_acknowledgment(self.approval, actor=self.user, channel='screen')
        with self.assertRaises(ReviewEvidenceError):
            require_acknowledgment(self.approval, actor=self.user2, channel='screen')

    def test_client_claimed_delivery_and_missing_phrase_are_not_evidence(self):
        """Even all the correct section IDs cannot replace server delivery."""
        with self.assertRaises(services.ApprovalConflictError):
            services.confirm_viewed(
                self.approval.pk,
                actor=self.user,
                channel='voice',
                evidence={
                    **self.evidence(),
                    'delivered_sections': required_sections(self.approval),
                },
            )
        self.deliver()
        with self.assertRaises(services.ApprovalConflictError):
            services.confirm_viewed(
                self.approval.pk,
                actor=self.user,
                channel='voice',
                evidence={**self.evidence(), 'acknowledgment': 'yes'},
            )

    def test_only_all_completed_pages_allow_voice_acknowledgment(self):
        """Pending, playing, failed and canceled pages cannot complete review."""
        utterances = self.deliver(state=PlaybackState.PLAYING)
        for state in [
            PlaybackState.PENDING,
            PlaybackState.PLAYING,
            PlaybackState.FAILED,
            PlaybackState.CANCELED,
        ]:
            VoiceUtterance.objects.filter(pk__in=[u.pk for u in utterances]).update(
                playback_state=state
            )
            with (
                self.subTest(state=state),
                self.assertRaises(services.ApprovalConflictError),
            ):
                services.confirm_viewed(
                    self.approval.pk,
                    actor=self.user,
                    channel='voice',
                    evidence=self.evidence(),
                )
        VoiceUtterance.objects.filter(pk__in=[u.pk for u in utterances]).update(
            playback_state=PlaybackState.DONE
        )
        services.confirm_viewed(
            self.approval.pk, actor=self.user, channel='voice', evidence=self.evidence()
        )
        require_acknowledgment(self.approval, actor=self.user, channel='voice')

    def test_other_actor_or_changed_scope_cannot_reuse_delivery(self):
        """Delivery owner and the current scope are authoritative."""
        self.deliver()
        with self.assertRaises(services.ApprovalConflictError):
            services.confirm_viewed(
                self.approval.pk,
                actor=self.user2,
                channel='voice',
                evidence=self.evidence(),
            )
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: [{'client_id': 2}]
        ):
            with self.assertRaises(services.ApprovalConflictError):
                services.confirm_viewed(
                    self.approval.pk,
                    actor=self.user,
                    channel='voice',
                    evidence=self.evidence(),
                )

    def test_exact_spoken_text_is_required_for_delivery_mapping(self):
        """Arbitrary utterances cannot be relabelled as required review pages."""
        unit = review_units(self.approval)[0]
        utterance = VoiceUtterance.objects.create(
            session=self.session,
            utterance_type='prompt',
            policy_version='test',
            spoken_summary='A different answer',
            spoken_summary_hash='b' * 64,
        )
        with self.assertRaises(ReviewEvidenceError):
            record_delivery(
                self.approval,
                actor=self.user,
                utterance=utterance,
                unit_ids=[unit['id']],
            )
        self.assertFalse(self.approval.review_deliveries.exists())

    @override_settings(APPROVAL_REVIEW_REVISION_BOUND=True)
    def test_revision_invalidates_review_and_legacy_timestamp(self):
        """A changed revision requires a new screen or voice acknowledgment."""
        services.confirm_viewed(
            self.approval.pk, actor=self.user, evidence=self.evidence()
        )
        services.revise(
            self.approval.pk,
            actor=self.user,
            data={
                'expected_revision': 0,
                'payload': {**self.approval.payload, 'changed': True},
            },
        )
        self.approval.refresh_from_db()
        self.assertIsNone(self.approval.viewed_confirmed_at)
        self.assertIsNotNone(self.approval.review_acknowledgments.get().invalidated_at)
        with self.assertRaises(ReviewEvidenceError):
            require_acknowledgment(self.approval, actor=self.user, channel='screen')

    @override_settings(APPROVAL_REVIEW_REVISION_BOUND=True)
    def test_drift_and_precondition_exception_invalidate_without_dispatch(self):
        """Failed checks neither get swallowed nor execute an external action."""
        executor = registry.get(ActionType.PURCHASE_ORDER)
        for result in [DriftReport(True), RuntimeError('Provider check unavailable')]:
            if self.approval.status == ApprovalStatus.CHANGES_REQUESTED:
                services.open_approval(self.approval.pk, actor=self.user)
                self.approval.refresh_from_db()
            services.confirm_viewed(
                self.approval.pk, actor=self.user, evidence=self.evidence()
            )
            with (
                patch.object(
                    executor,
                    'check_preconditions',
                    side_effect=(result if isinstance(result, Exception) else None),
                    return_value=result,
                ),
                patch.object(executor, 'execute') as execute,
            ):
                with self.assertRaises(services.ApprovalConflictError):
                    services.approve(self.approval.pk, actor=self.user)
                execute.assert_not_called()
            self.approval.refresh_from_db()
            self.assertEqual(self.approval.status, ApprovalStatus.CHANGES_REQUESTED)
            self.assertIsNone(self.approval.viewed_confirmed_at)
            self.assertIsNotNone(
                self.approval.review_acknowledgments.get().invalidated_at
            )

    def test_invalidation_requires_new_voice_pages_even_when_hash_unchanged(self):
        """A second acknowledgment cannot recycle audio from a failed review."""
        self.deliver()
        services.confirm_viewed(
            self.approval.pk, actor=self.user, channel='voice', evidence=self.evidence()
        )
        invalidate(self.approval)
        with self.assertRaises(services.ApprovalConflictError):
            services.confirm_viewed(
                self.approval.pk,
                actor=self.user,
                channel='voice',
                evidence=self.evidence(),
            )
        self.deliver()
        services.confirm_viewed(
            self.approval.pk, actor=self.user, channel='voice', evidence=self.evidence()
        )

    @override_settings(APPROVAL_REVIEW_REVISION_BOUND=True)
    def test_scope_change_revokes_screen_acknowledgment(self):
        """Even adding/removing scope after review requires explicit review again."""
        services.confirm_viewed(
            self.approval.pk, actor=self.user, evidence=self.evidence()
        )
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: [{'client_id': 2}]
        ):
            with self.assertRaises(ReviewEvidenceError):
                require_acknowledgment(self.approval, actor=self.user, channel='screen')

    def test_permission_revoked_during_revalidation_blocks_execution(self):
        """The row-locked execution gate re-reads permissions, not cached bits."""
        services.confirm_viewed(self.approval.pk, actor=self.user)

        def revoke(*args):
            self.user.user_permissions.clear()
            return DriftReport(False)

        executor = registry.get(ActionType.PURCHASE_ORDER)
        with (
            patch.object(executor, 'check_preconditions', side_effect=revoke),
            patch.object(executor, 'execute') as execute,
        ):
            with self.assertRaises(services.ApprovalForbiddenError):
                services.approve(self.approval.pk, actor=self.user)
            execute.assert_not_called()

    def test_revision_change_during_revalidation_blocks_execution(self):
        """The locked gate compares the whole content, not only the FSM state."""
        services.confirm_viewed(self.approval.pk, actor=self.user)

        def change(*args):
            Approval.objects.filter(pk=self.approval.pk).update(
                payload={'changed': True}
            )
            return DriftReport(False)

        executor = registry.get(ActionType.PURCHASE_ORDER)
        with (
            patch.object(executor, 'check_preconditions', side_effect=change),
            patch.object(executor, 'execute') as execute,
        ):
            with self.assertRaises(services.ApprovalConflictError):
                services.approve(self.approval.pk, actor=self.user)
            execute.assert_not_called()

    @override_settings(APPROVAL_REVIEW_REVISION_BOUND=True)
    def test_matching_screen_review_calls_recording_executor_once(self):
        """A reviewed screen action retains service idempotency without I/O."""
        services.confirm_viewed(
            self.approval.pk, actor=self.user, evidence=self.evidence()
        )
        executor = registry.get(ActionType.PURCHASE_ORDER)
        with patch.object(
            executor, 'execute', return_value=EffectResult(True, 'test-effect')
        ) as execute:
            services.approve(self.approval.pk, actor=self.user)
            services.approve(self.approval.pk, actor=self.user)
            execute.assert_called_once()

    def test_policy_is_exhaustive_and_missing_row_or_step_up_fails_closed(self):
        """Unknown actions and future governance cannot silently use defaults."""
        self.assertEqual(set(ACTION_POLICIES), set(ActionType.values))
        original = ACTION_POLICIES[ActionType.PURCHASE_ORDER]
        for policy in [
            None,
            replace(original, second_approver=True),
            replace(original, step_up='mfa'),
        ]:
            with patch.dict(ACTION_POLICIES, {ActionType.PURCHASE_ORDER: policy}):
                with self.assertRaises(ApprovalPolicyError):
                    require_policy(self.approval, actor=self.user, channel='screen')

    def test_tier_two_pilot_requires_server_named_actor_and_request(self):
        """Neither a VOICE-TEST label nor a client route assertion qualifies."""
        with (
            patch(
                'approvals.review_sections.voice_eligibility', return_value=(True, None)
            ),
            patch.dict(os.environ, {'CONTAINER_APP_NAME': 'aimms-experimental'}),
        ):
            with self.assertRaises(ApprovalPolicyError):
                require_policy(self.approval, actor=self.user, channel='voice')
            with override_settings(
                APPROVAL_VOICE_PILOT_ACTOR_IDS=[self.user.pk],
                APPROVAL_VOICE_PILOT_REQUEST_IDS=[str(self.approval.pk)],
            ):
                require_policy(self.approval, actor=self.user, channel='voice')
                with self.assertRaises(ApprovalPolicyError):
                    require_policy(self.approval, actor=self.user2, channel='voice')

    def test_tier_three_stays_screen_only(self):
        """Pilot allowlists cannot override the tier-three screen requirement."""
        self.approval.risk_tier = 3
        with self.assertRaises(ApprovalPolicyError):
            require_policy(self.approval, actor=self.user, channel='voice')

    def test_lowered_risk_cannot_bypass_voice_review_or_route_policy(self):
        """Stored tier-one data cannot turn an external effect into a bare yes."""
        self.approval.risk_tier = 1
        with self.assertRaises(ReviewEvidenceError):
            require_acknowledgment(self.approval, actor=self.user, channel='voice')
        with patch(
            'approvals.review_sections.voice_eligibility', return_value=(True, None)
        ):
            with self.assertRaises(ApprovalPolicyError):
                require_policy(self.approval, actor=self.user, channel='voice')

    def test_self_approval_policy_hook_fails_closed_on_unresolved_requester(self):
        """A future separation-of-duties rule needs known requester identity."""
        original = ACTION_POLICIES[ActionType.PURCHASE_ORDER]
        require_policy(self.approval, actor=self.user, channel='screen')
        with patch.dict(
            ACTION_POLICIES,
            {ActionType.PURCHASE_ORDER: replace(original, self_approval_allowed=False)},
        ):
            with self.assertRaises(ApprovalPolicyError):
                require_policy(self.approval, actor=self.user, channel='screen')
            self.approval.revisions.filter(revision_number=0).update(
                created_by_user=self.user
            )
            with self.assertRaises(ApprovalPolicyError):
                require_policy(self.approval, actor=self.user, channel='screen')
            require_policy(self.approval, actor=self.user2, channel='screen')

    def test_long_email_body_requires_every_page(self):
        """Finishing most of a long section does not satisfy the whole section."""
        self.approval.action_type = ActionType.EMAIL
        self.approval.payload = {
            'body': 'Required long message content. ' * 100,
            'to': ['lokesh@equa.work'],
        }
        self.approval.save(update_fields=['action_type', 'payload'])
        utterances = self.deliver()
        self.assertGreater(len(utterances), len(required_sections(self.approval)))
        incomplete = next(
            u for u in utterances if 'Full message, part 2' in u.spoken_summary
        )
        incomplete.playback_state = PlaybackState.CANCELED
        incomplete.save(update_fields=['playback_state'])
        with self.assertRaises(services.ApprovalConflictError):
            services.confirm_viewed(
                self.approval.pk,
                actor=self.user,
                channel='voice',
                evidence=self.evidence(),
            )
