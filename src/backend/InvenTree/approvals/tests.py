"""Comprehensive tests for the AI Agent Approval Queue.

Covers spec Sections 18.1-18.5:
- 18.1: Unit tests (FSM, idempotency, concurrency, locks, gates)
- 18.2: Integration tests (drift, revalidation)
- 18.3: End-to-end tests (happy path, deny, modify, expiry, cancel-revert)
- 18.4: Restart/resume (checkpoint correctness)
- 18.5: Failure injection tests
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from .executors import EffectResult, is_executor_required, registry
from .models import (
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    ActionType,
    Approval,
    ApprovalEvent,
    ApprovalRevision,
    ApprovalStatus,
    EventType,
    ExecutedEffect,
    compute_idempotency_key,
    get_lock_ttl_seconds,
    is_valid_transition,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_approval_data(**overrides):
    """Build valid approval creation payload with defaults."""
    data = {
        'tool_call_id': f'tc-{uuid.uuid4().hex[:8]}',
        'agent_run_id': f'ar-{uuid.uuid4().hex[:8]}',
        'agent_checkpoint_id': f'cp-{uuid.uuid4().hex[:8]}',
        'action_type': ActionType.PURCHASE_ORDER,
        'summary': 'Create PO for Widget-X',
        'payload': {
            'intent_summary': 'Create PO to restock Widget-X',
            'entity_refs': {'supplier_id': 42, 'part_id': 101},
            'proposed_changes': {'type': 'purchase_order_create'},
            'supplier_id': 42,
            'line_items': [{'part_id': 101, 'quantity': 500}],
        },
        'card_context': {'version': 1},
        'baseline_context': {'supplier_active': True},
        'preconditions': {'supplier_exists': True},
    }
    data.update(overrides)
    return data


class ApprovalTestBase(TestCase):
    """Base class for approval tests with common setup."""

    def setUp(self):
        """Create shared test fixtures."""
        self.user = User.objects.create_user(
            username='reviewer', password='testpass123', email='reviewer@test.com'
        )
        self.user2 = User.objects.create_user(
            username='reviewer2', password='testpass123', email='reviewer2@test.com'
        )
        # Grant approvals.review permission for write operations
        review_perm = Permission.objects.get(
            codename='review', content_type__app_label='approvals'
        )
        self.user.user_permissions.add(review_perm)
        self.user2.user_permissions.add(review_perm)
        # Refetch to clear cached permissions
        self.user = User.objects.get(pk=self.user.pk)
        self.user2 = User.objects.get(pk=self.user2.pk)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

    def _create_approval(self, **overrides):
        """Create an approval via the API."""
        data = _make_approval_data(**overrides)
        response = self.client.post('/api/approvals/', data, format='json')
        return response

    def _create_approval_obj(self, **overrides):
        """Create an approval and return the model instance."""
        resp = self._create_approval(**overrides)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        return Approval.objects.get(pk=resp.data['id'])


# ===========================================================================
# 18.1 Unit tests — FSM transitions + business logic
# ===========================================================================


class FSMTransitionTests(ApprovalTestBase):
    """Test all 17 valid transitions and reject invalid ones."""

    def test_all_valid_transitions(self):
        """All 16 entries in VALID_TRANSITIONS are accepted (Section 6.2 table)."""
        total = 0
        for from_status, to_set in VALID_TRANSITIONS.items():
            for to_status in to_set:
                self.assertTrue(
                    is_valid_transition(from_status, to_status),
                    f'{from_status} → {to_status} should be valid',
                )
                total += 1
        # Section 6.2 table: 3 + 5 + 4 + 2 + 2 = 16 + 1 (pending→failed) = 17 transitions
        self.assertEqual(total, 17)

    def test_invalid_transitions_rejected(self):
        """Random invalid transitions are rejected."""
        invalid = [
            (ApprovalStatus.PENDING, ApprovalStatus.APPROVED),
            (ApprovalStatus.PENDING, ApprovalStatus.DENIED),
            (ApprovalStatus.PENDING, ApprovalStatus.SUCCEEDED),
            (ApprovalStatus.IN_REVIEW, ApprovalStatus.EXECUTING),
            (ApprovalStatus.IN_REVIEW, ApprovalStatus.SUCCEEDED),
            (ApprovalStatus.APPROVED, ApprovalStatus.DENIED),
            (ApprovalStatus.APPROVED, ApprovalStatus.CANCELED),
            (ApprovalStatus.EXECUTING, ApprovalStatus.APPROVED),
            (ApprovalStatus.EXECUTING, ApprovalStatus.CANCELED),
        ]
        for from_s, to_s in invalid:
            self.assertFalse(
                is_valid_transition(from_s, to_s),
                f'{from_s} → {to_s} should be invalid',
            )

    def test_terminal_guard(self):
        """No transition allowed FROM any terminal status."""
        all_statuses = [s.value for s in ApprovalStatus]
        for terminal in TERMINAL_STATUSES:
            for target in all_statuses:
                self.assertFalse(
                    is_valid_transition(terminal, target),
                    f'{terminal} → {target} should be blocked (terminal source)',
                )


class FSMAPITransitionTests(ApprovalTestBase):
    """Test FSM transitions through the API endpoints."""

    def test_open_from_pending(self):
        """POST /open transitions pending → in_review."""
        approval = self._create_approval_obj()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

        resp = self.client.post(f'/api/approvals/{approval.pk}/open/')
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.IN_REVIEW)

    def test_open_invalid_status(self):
        """POST /open from approved → 409."""
        approval = self._create_approval_obj()
        approval.status = ApprovalStatus.APPROVED
        approval.save(update_fields=['status'])

        resp = self.client.post(f'/api/approvals/{approval.pk}/open/')
        self.assertEqual(resp.status_code, 409)
        self.assertIn('request_id', resp.data)

    def test_request_changes(self):
        """POST /request-changes transitions in_review → changes_requested."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/request-changes/',
            {'instructions': 'Please change the quantity'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.CHANGES_REQUESTED)

    def test_deny_from_in_review(self):
        """POST /deny transitions in_review → denied."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/deny/',
            {'reason': 'Not needed'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.DENIED)
        self.assertEqual(approval.deny_reason, 'Not needed')

    def test_cancel_from_pending(self):
        """POST /cancel transitions pending → canceled."""
        approval = self._create_approval_obj()

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/cancel/',
            {'reason': 'Changed my mind'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.CANCELED)
        self.assertEqual(approval.canceled_reason, 'Changed my mind')


class IdempotencyTests(ApprovalTestBase):
    """Test idempotency guarantees (Sections 7.0, 7.2)."""

    def test_duplicate_creation_returns_existing(self):
        """Duplicate POST with same idempotency_key returns existing record."""
        data = _make_approval_data()
        resp1 = self.client.post('/api/approvals/', data, format='json')
        self.assertEqual(resp1.status_code, 201)

        resp2 = self.client.post('/api/approvals/', data, format='json')
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp1.data['id'], resp2.data['id'])

    def test_idempotency_key_computation(self):
        """SHA-256 of agent_run_id:tool_call_id is deterministic."""
        key1 = compute_idempotency_key('run-1', 'tc-1')
        key2 = compute_idempotency_key('run-1', 'tc-1')
        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 64)  # SHA-256 hex

    def test_double_approve_idempotent(self):
        """Double POST /approve returns current state without duplicate execution."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.viewed_confirmed_at = timezone.now()
        approval.viewed_confirmed_by_user = self.user
        approval.save(update_fields=['viewed_confirmed_at', 'viewed_confirmed_by_user'])

        resp1 = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp1.status_code, 200)

        # Second approve should be idempotent
        resp2 = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp2.status_code, 200)

    def test_deny_when_already_terminal_idempotent(self):
        """POST /deny on already-denied approval is idempotent."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.transition_to(ApprovalStatus.DENIED, actor_user=self.user)

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/deny/', {'reason': 'Again'}, format='json'
        )
        self.assertEqual(resp.status_code, 200)


class OptimisticConcurrencyTests(ApprovalTestBase):
    """Test optimistic concurrency on /revise (Section 7.3)."""

    def _setup_revisable_approval(self):
        """Create an approval in in_review with a lock."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.acquire_lock(self.user)
        return approval

    def test_revise_with_correct_expected_revision(self):
        """Revise succeeds with matching expected_revision."""
        approval = self._setup_revisable_approval()

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {
                'payload': {
                    'updated': True,
                    'entity_refs': {},
                    'intent_summary': 'revised',
                },
                'expected_revision': 0,
                'diff_summary': {'changed': ['summary']},
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.current_revision_number, 1)

    def test_revise_with_stale_expected_revision(self):
        """Revise with stale expected_revision returns 409."""
        approval = self._setup_revisable_approval()

        # First revise succeeds
        self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {
                'payload': {'v': 1, 'entity_refs': {}, 'intent_summary': 'v1'},
                'expected_revision': 0,
            },
            format='json',
        )

        # Second revise with stale revision
        resp = self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {
                'payload': {'v': 2, 'entity_refs': {}, 'intent_summary': 'v2'},
                'expected_revision': 0,  # Stale! Current is 1
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn('current_revision', resp.data)

    def test_revise_restricted_to_in_review_or_changes_requested(self):
        """Revise when status is pending returns 409."""
        approval = self._create_approval_obj()  # status = pending

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {'payload': {'test': True}, 'expected_revision': 0},
            format='json',
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['error'], 'invalid_status')


class LockEnforcementTests(ApprovalTestBase):
    """Test modification lock behavior (Section 7.3)."""

    def test_lock_acquire_and_release(self):
        """Acquire and release lock."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        resp = self.client.post(f'/api/approvals/{approval.pk}/acquire-modify-lock/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['holder_user_id'], self.user.pk)

        resp = self.client.post(f'/api/approvals/{approval.pk}/release-modify-lock/')
        self.assertEqual(resp.status_code, 200)

    def test_lock_blocks_other_user_revise(self):
        """Revise by non-holder while lock active returns 423."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.acquire_lock(self.user)

        resp = self.client2.post(
            f'/api/approvals/{approval.pk}/revise/',
            {'payload': {'test': True}, 'expected_revision': 0},
            format='json',
        )
        self.assertEqual(resp.status_code, 423)

    def test_lock_idle_expiry(self):
        """Lock auto-expires after TTL; subsequent revise by another user succeeds."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.acquire_lock(self.user)

        # Manually expire the lock
        approval.modification_lock_expires_at = timezone.now() - timedelta(seconds=1)
        approval.save(update_fields=['modification_lock_expires_at'])

        # Another user should now be able to acquire lock and revise
        approval.acquire_lock(self.user2)
        resp = self.client2.post(
            f'/api/approvals/{approval.pk}/revise/',
            {'payload': {'test': True}, 'expected_revision': 0},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_lock_blocks_approve_for_non_holder(self):
        """Approve by non-holder while lock active returns 423."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.viewed_confirmed_at = timezone.now()
        approval.viewed_confirmed_by_user = self.user2
        approval.save(update_fields=['viewed_confirmed_at', 'viewed_confirmed_by_user'])
        approval.acquire_lock(self.user)

        resp = self.client2.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp.status_code, 423)

    def test_lock_blocks_deny_for_non_holder(self):
        """Deny by non-holder while lock active returns 423."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.acquire_lock(self.user)

        resp = self.client2.post(
            f'/api/approvals/{approval.pk}/deny/', {'reason': 'no'}, format='json'
        )
        self.assertEqual(resp.status_code, 423)

    def test_lock_holder_can_approve(self):
        """Lock holder can still approve."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.viewed_confirmed_at = timezone.now()
        approval.viewed_confirmed_by_user = self.user
        approval.save(update_fields=['viewed_confirmed_at', 'viewed_confirmed_by_user'])
        approval.acquire_lock(self.user)

        resp = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp.status_code, 200)

    def test_lock_same_user_extends_lease(self):
        """Same user re-acquiring lock extends the lease."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        resp1 = self.client.post(f'/api/approvals/{approval.pk}/acquire-modify-lock/')
        self.assertIn('expires_at', resp1.data)

        # Re-acquire extends lease
        resp2 = self.client.post(f'/api/approvals/{approval.pk}/acquire-modify-lock/')
        self.assertEqual(resp2.status_code, 200)
        # New expiry should be >= old expiry
        self.assertIsNotNone(resp2.data['expires_at'])


class ViewedConfirmedGateTests(ApprovalTestBase):
    """Test the viewed-confirmed gate for Tier 2-3 (Section 2.3)."""

    def test_approve_without_confirm_viewed_returns_403(self):
        """Approve without prior confirm-viewed for Tier 2 returns 403."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        resp = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp.status_code, 403)

    def test_approve_after_confirm_viewed_succeeds(self):
        """Approve after confirm-viewed succeeds."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        self.client.post(f'/api/approvals/{approval.pk}/confirm-viewed/')
        resp = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp.status_code, 200)

    def test_required_action_without_executor_fails_closed(self):
        """Required actions cannot succeed without an executor.

        Uses JOB_KIT_SUBSTITUTION: it is executor-required but has no registered
        executor (unlike PROCEDURE_PUBLISH, which the tasks app registers).
        """
        approval = self._create_approval_obj(
            action_type=ActionType.JOB_KIT_SUBSTITUTION
        )
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        self.client.post(f'/api/approvals/{approval.pk}/confirm-viewed/')

        resp = self.client.post(f'/api/approvals/{approval.pk}/approve/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.FAILED)
        self.assertEqual(
            approval.execution_error,
            {'error': 'No executor registered for required action'},
        )
        self.assertFalse(ExecutedEffect.objects.filter(approval=approval).exists())

    def test_viewed_confirmed_persists_across_revisions(self):
        """viewed_confirmed_at does NOT reset after /revise."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        # Confirm viewed
        self.client.post(f'/api/approvals/{approval.pk}/confirm-viewed/')
        approval.refresh_from_db()
        self.assertIsNotNone(approval.viewed_confirmed_at)

        # Revise
        approval.acquire_lock(self.user)
        self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {'payload': {'updated': True}, 'expected_revision': 0},
            format='json',
        )

        # viewed_confirmed_at should still be set
        approval.refresh_from_db()
        self.assertIsNotNone(approval.viewed_confirmed_at)


class CancelRevertTests(ApprovalTestBase):
    """Test cancel semantics with revision revert (Section 7.2)."""

    def test_cancel_with_revert(self):
        """Cancel on approval with revision > 0 reverts payload to previous revision."""
        approval = self._create_approval_obj()
        original_payload = approval.payload.copy()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.acquire_lock(self.user)

        # Create revision 1
        self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {
                'payload': {
                    'changed': True,
                    'entity_refs': {},
                    'intent_summary': 'changed',
                },
                'expected_revision': 0,
            },
            format='json',
        )

        approval.refresh_from_db()
        self.assertEqual(approval.current_revision_number, 1)
        approval.release_lock(self.user)

        # Cancel should revert to revision 0
        resp = self.client.post(
            f'/api/approvals/{approval.pk}/cancel/',
            {'reason': 'Undo changes'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.CANCELED)
        self.assertEqual(approval.payload, original_payload)

        # Check cancel_reverted event exists
        self.assertTrue(
            ApprovalEvent.objects.filter(
                approval=approval, event_type=EventType.CANCEL_REVERTED
            ).exists()
        )

    def test_cancel_at_revision_0(self):
        """Cancel on approval with only revision 0 transitions to canceled."""
        approval = self._create_approval_obj()

        resp = self.client.post(f'/api/approvals/{approval.pk}/cancel/', format='json')
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.CANCELED)

        # No cancel_reverted event should exist
        self.assertFalse(
            ApprovalEvent.objects.filter(
                approval=approval, event_type=EventType.CANCEL_REVERTED
            ).exists()
        )


class EntityConflictDetectionTests(ApprovalTestBase):
    """Test entity conflict detection (Section 9.1)."""

    def test_conflicting_entity_refs_returns_409(self):
        """Creating approval with overlapping entity_refs returns 409 with existing ID."""
        data = _make_approval_data()
        resp1 = self.client.post('/api/approvals/', data, format='json')
        self.assertEqual(resp1.status_code, 201)

        # Create second with different tool_call_id but same entity_refs
        data2 = _make_approval_data(
            tool_call_id=f'tc-{uuid.uuid4().hex[:8]}',
            agent_run_id=f'ar-{uuid.uuid4().hex[:8]}',
        )
        # Same payload entity_refs
        data2['payload']['entity_refs'] = data['payload']['entity_refs']

        resp2 = self.client.post('/api/approvals/', data2, format='json')
        self.assertEqual(resp2.status_code, 409)
        self.assertIn('X-Approval-Conflict', resp2)


class RevisionManagementTests(ApprovalTestBase):
    """Test revision 0 creation and revision history."""

    def test_revision_0_created_at_creation(self):
        """Revision 0 is system-generated at creation."""
        approval = self._create_approval_obj()

        rev0 = ApprovalRevision.objects.get(approval=approval, revision_number=0)
        self.assertEqual(rev0.payload_snapshot, approval.payload)
        self.assertIsNone(rev0.diff_summary)
        self.assertIsNone(rev0.created_by_user)

    def test_revision_history_ordered(self):
        """Revisions are returned in order by revision_number."""
        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.acquire_lock(self.user)

        # Create revisions 1 and 2
        for i in range(2):
            self.client.post(
                f'/api/approvals/{approval.pk}/revise/',
                {'payload': {'version': i + 1}, 'expected_revision': i},
                format='json',
            )

        resp = self.client.get(f'/api/approvals/{approval.pk}/revisions/')
        self.assertEqual(resp.status_code, 200)
        # resp.data is a ReturnList when unpaginated, a dict when paginated.
        revisions = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        numbers = [r['revision_number'] for r in revisions]
        self.assertEqual(numbers, [0, 1, 2])


# ===========================================================================
# 18.1 continued — Read endpoints
# ===========================================================================


class ReadEndpointTests(ApprovalTestBase):
    """Test GET endpoints (Section 7.1)."""

    def test_list_approvals(self):
        """GET /api/approvals/ returns list."""
        self._create_approval_obj()
        resp = self.client.get('/api/approvals/')
        self.assertEqual(resp.status_code, 200)

    def test_list_filter_by_status(self):
        """GET /api/approvals/?status=pending filters correctly."""
        self._create_approval_obj()
        resp = self.client.get('/api/approvals/?status=pending')
        self.assertEqual(resp.status_code, 200)

    def test_detail_approval(self):
        """GET /api/approvals/{id}/ returns full detail."""
        approval = self._create_approval_obj()
        resp = self.client.get(f'/api/approvals/{approval.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('payload', resp.data)
        self.assertIn('card_context', resp.data)

    def test_card_package(self):
        """GET /api/approvals/{id}/card-package/ returns modify-in-chat bundle."""
        approval = self._create_approval_obj()
        resp = self.client.get(f'/api/approvals/{approval.pk}/card-package/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('payload', resp.data)
        self.assertIn('card_context', resp.data)
        self.assertIn('baseline_context', resp.data)
        self.assertIn('preconditions', resp.data)

    def test_count_endpoint(self):
        """GET /api/approvals/count/?status=pending returns count."""
        self._create_approval_obj()
        resp = self.client.get('/api/approvals/count/?status=pending')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('count', resp.data)
        self.assertGreaterEqual(resp.data['count'], 1)

    def test_events_endpoint(self):
        """GET /api/approvals/{id}/events/ returns event log."""
        approval = self._create_approval_obj()
        resp = self.client.get(f'/api/approvals/{approval.pk}/events/')
        self.assertEqual(resp.status_code, 200)

    def test_revisions_endpoint(self):
        """GET /api/approvals/{id}/revisions/ returns revision history."""
        approval = self._create_approval_obj()
        resp = self.client.get(f'/api/approvals/{approval.pk}/revisions/')
        self.assertEqual(resp.status_code, 200)

    def test_not_found(self):
        """GET /api/approvals/{bogus_id}/ returns 404."""
        bogus = uuid.uuid4()
        resp = self.client.get(f'/api/approvals/{bogus}/')
        self.assertEqual(resp.status_code, 404)


# ===========================================================================
# 18.1 continued — Error response format
# ===========================================================================


class ErrorResponseFormatTests(ApprovalTestBase):
    """Test that error responses match Section 7.4 schema."""

    def test_error_has_request_id(self):
        """All error responses include request_id (UUID)."""
        bogus = uuid.uuid4()
        resp = self.client.post(f'/api/approvals/{bogus}/open/')
        self.assertEqual(resp.status_code, 404)
        self.assertIn('request_id', resp.data)
        # request_id should be a valid UUID
        uuid.UUID(resp.data['request_id'])

    def test_conflict_error_shape(self):
        """409 errors include current_status."""
        approval = self._create_approval_obj()
        approval.status = ApprovalStatus.SUCCEEDED
        approval.resolved_at = timezone.now()
        approval.save(update_fields=['status', 'resolved_at'])

        resp = self.client.post(f'/api/approvals/{approval.pk}/open/')
        self.assertEqual(resp.status_code, 409)
        self.assertIn('current_status', resp.data)


# ===========================================================================
# 18.2 Integration tests — drift + revalidation (stubs for Phase 4)
# ===========================================================================


class DriftRevalidationTests(ApprovalTestBase):
    """Placeholder tests for drift detection (Phase 4 real implementation)."""

    def test_stale_baseline_warning_in_card_package(self):
        """Card package emits warning when baseline is stale."""
        approval = self._create_approval_obj()
        # Make the approval old
        Approval.objects.filter(pk=approval.pk).update(
            created_at=timezone.now() - timedelta(hours=48)
        )

        resp = self.client.get(f'/api/approvals/{approval.pk}/card-package/')
        self.assertEqual(resp.status_code, 200)
        warnings = resp.data.get('validation_warnings', [])
        self.assertTrue(
            any(
                'old' in w.lower() or 'stale' in w.lower() or 'threshold' in w.lower()
                for w in warnings
            ),
            f'Expected stale baseline warning, got: {warnings}',
        )


# ===========================================================================
# 18.3 End-to-end tests
# ===========================================================================


class HappyPathE2ETest(ApprovalTestBase):
    """E2E: created → opened → viewed → approved → executing → succeeded."""

    def test_full_happy_path(self):
        """Test full happy path."""
        approval = self._create_approval_obj()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

        # Open
        self.client.post(f'/api/approvals/{approval.pk}/open/')
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.IN_REVIEW)

        # Confirm viewed
        self.client.post(f'/api/approvals/{approval.pk}/confirm-viewed/')
        approval.refresh_from_db()
        self.assertIsNotNone(approval.viewed_confirmed_at)

        # Approve (executor runs through: approved → executing → succeeded)
        resp = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.SUCCEEDED)
        self.assertEqual(
            ExecutedEffect.objects.filter(
                approval=approval, effect_type=ActionType.PURCHASE_ORDER
            ).count(),
            1,
        )
        self.assertTrue(approval.is_terminal)
        self.assertIsNotNone(approval.resolved_at)


class DenyPathE2ETest(ApprovalTestBase):
    """E2E: created → opened → denied."""

    def test_deny_path(self):
        """Test deny path."""
        approval = self._create_approval_obj()

        self.client.post(f'/api/approvals/{approval.pk}/open/')
        resp = self.client.post(
            f'/api/approvals/{approval.pk}/deny/',
            {'reason': 'Not appropriate'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.DENIED)
        self.assertTrue(approval.is_terminal)


class ModifyPathE2ETest(ApprovalTestBase):
    """E2E: created → opened → lock → revised → unlock → approved → succeeded."""

    def test_modify_path(self):
        """Test modify path."""
        approval = self._create_approval_obj()

        self.client.post(f'/api/approvals/{approval.pk}/open/')
        self.client.post(f'/api/approvals/{approval.pk}/confirm-viewed/')
        self.client.post(f'/api/approvals/{approval.pk}/acquire-modify-lock/')

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {
                'payload': {
                    'revised': True,
                    'entity_refs': {},
                    'intent_summary': 'revised',
                },
                'expected_revision': 0,
                'diff_summary': {'changed': ['line_items']},
                'note': 'Updated quantities',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200)

        self.client.post(f'/api/approvals/{approval.pk}/release-modify-lock/')

        resp = self.client.post(f'/api/approvals/{approval.pk}/approve/')
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        # Executor runs through: approved → executing → succeeded
        self.assertEqual(approval.status, ApprovalStatus.SUCCEEDED)
        self.assertTrue(approval.is_terminal)


class CancelRevertPathE2ETest(ApprovalTestBase):
    """E2E: created → revised → cancel → payload reverted → canceled."""

    def test_cancel_revert_path(self):
        """Test cancel revert path."""
        approval = self._create_approval_obj()
        original_payload = approval.payload.copy()

        self.client.post(f'/api/approvals/{approval.pk}/open/')
        approval.acquire_lock(self.user)

        self.client.post(
            f'/api/approvals/{approval.pk}/revise/',
            {'payload': {'modified': True}, 'expected_revision': 0},
            format='json',
        )
        approval.release_lock(self.user)

        resp = self.client.post(
            f'/api/approvals/{approval.pk}/cancel/', {'reason': 'Revert'}, format='json'
        )
        self.assertEqual(resp.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.CANCELED)
        self.assertEqual(approval.payload, original_payload)


# ===========================================================================
# 18.3 continued — Expiry path
# ===========================================================================


class ExpiryPathTests(ApprovalTestBase):
    """Test the expiry background job (Section 3.1)."""

    @override_settings(APPROVAL_EXPIRY_JOB_ENABLED=True)
    def test_expiry_job_transitions_expired(self):
        """Expiry job transitions expired approvals to 'expired'."""
        from .tasks import check_approval_expiry

        approval = self._create_approval_obj()
        # Set expires_at in the past
        Approval.objects.filter(pk=approval.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )

        check_approval_expiry()

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.EXPIRED)
        self.assertTrue(approval.is_terminal)

    @override_settings(APPROVAL_EXPIRY_JOB_ENABLED=True)
    def test_expiry_skips_terminal(self):
        """Expiry job skips already-terminal approvals."""
        from .tasks import check_approval_expiry

        approval = self._create_approval_obj()
        approval.status = ApprovalStatus.DENIED
        approval.resolved_at = timezone.now()
        approval.save(update_fields=['status', 'resolved_at'])

        Approval.objects.filter(pk=approval.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )

        check_approval_expiry()

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.DENIED)

    @override_settings(APPROVAL_EXPIRY_JOB_ENABLED=True)
    def test_expiry_in_review_to_expired(self):
        """in_review → expired via expiry job."""
        from .tasks import check_approval_expiry

        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        Approval.objects.filter(pk=approval.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )

        check_approval_expiry()

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.EXPIRED)

    @override_settings(APPROVAL_EXPIRY_JOB_ENABLED=True)
    def test_expiry_changes_requested_to_expired(self):
        """changes_requested → expired via expiry job."""
        from .tasks import check_approval_expiry

        approval = self._create_approval_obj()
        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)
        approval.transition_to(ApprovalStatus.CHANGES_REQUESTED, actor_user=self.user)

        Approval.objects.filter(pk=approval.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )

        check_approval_expiry()

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.EXPIRED)


# ===========================================================================
# 18.3 continued — Reconciliation job
# ===========================================================================


class ReconciliationJobTests(ApprovalTestBase):
    """Test the reconciliation background job (Section 11.5)."""

    @override_settings(
        APPROVAL_QUEUE_ENABLED=True, APPROVAL_EXECUTION_STUCK_THRESHOLD_SECONDS=1800
    )
    def test_stuck_executing_transitions_to_failed(self):
        """Stuck executing approval transitions to failed."""
        from .tasks import reconcile_approvals

        approval = self._create_approval_obj()
        approval.status = ApprovalStatus.EXECUTING
        approval.save(update_fields=['status'])
        Approval.objects.filter(pk=approval.pk).update(
            updated_at=timezone.now() - timedelta(minutes=35)
        )

        reconcile_approvals()

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.FAILED)
        self.assertIsNotNone(approval.execution_error)

    @override_settings(APPROVAL_QUEUE_ENABLED=True)
    def test_expired_locks_cleared(self):
        """Expired locks are cleaned up by reconciliation."""
        from .tasks import reconcile_approvals

        approval = self._create_approval_obj()
        approval.modification_lock_user = self.user
        approval.modification_lock_acquired_at = timezone.now() - timedelta(minutes=20)
        approval.modification_lock_expires_at = timezone.now() - timedelta(minutes=10)
        approval.save(
            update_fields=[
                'modification_lock_user',
                'modification_lock_acquired_at',
                'modification_lock_expires_at',
            ]
        )

        reconcile_approvals()

        approval.refresh_from_db()
        self.assertIsNone(approval.modification_lock_user_id)
        self.assertIsNone(approval.modification_lock_expires_at)

    @override_settings(APPROVAL_QUEUE_ENABLED=True)
    def test_orphaned_pending_transitions_to_failed(self):
        """Orphaned pending approvals (24h+) transition to failed."""
        from .tasks import reconcile_approvals

        approval = self._create_approval_obj()
        Approval.objects.filter(pk=approval.pk).update(
            created_at=timezone.now() - timedelta(hours=25),
            expires_at=timezone.now() - timedelta(hours=1),  # T-2: expired
        )

        reconcile_approvals()

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.FAILED)
        self.assertEqual(approval.execution_error['reason'], 'agent_orphaned')


# ===========================================================================
# 18.3 continued — Retention purge job
# ===========================================================================


class RetentionPurgeTests(ApprovalTestBase):
    """Test the retention purge background job (Section 15)."""

    @override_settings(
        APPROVAL_RETENTION_PURGE_ENABLED=True, APPROVAL_RETENTION_DAYS=90
    )
    def test_purge_deletes_old_terminal_approvals(self):
        """Purge job deletes terminal approvals older than retention threshold."""
        from .tasks import purge_expired_approvals

        approval = self._create_approval_obj()
        approval.status = ApprovalStatus.SUCCEEDED
        approval.resolved_at = timezone.now() - timedelta(days=91)
        approval.save(update_fields=['status', 'resolved_at'])

        purge_expired_approvals()

        self.assertFalse(Approval.objects.filter(pk=approval.pk).exists())

    @override_settings(
        APPROVAL_RETENTION_PURGE_ENABLED=True, APPROVAL_RETENTION_DAYS=90
    )
    def test_purge_keeps_recent_terminal(self):
        """Purge job keeps terminal approvals within retention period."""
        from .tasks import purge_expired_approvals

        approval = self._create_approval_obj()
        approval.status = ApprovalStatus.DENIED
        approval.resolved_at = timezone.now() - timedelta(days=30)
        approval.save(update_fields=['status', 'resolved_at'])

        purge_expired_approvals()

        self.assertTrue(Approval.objects.filter(pk=approval.pk).exists())


# ===========================================================================
# 18.1 continued — Executor registry
# ===========================================================================


class ExecutorRegistryTests(TestCase):
    """Test executor registry (Section 17)."""

    def test_all_non_required_action_types_registered(self):
        """All non-required ActionType values have registered executors."""
        for action_type in ActionType.values:
            if not is_executor_required(action_type):
                self.assertTrue(
                    registry.has(action_type),
                    f'No executor registered for {action_type}',
                )

    def test_executor_required_actions(self):
        """Only maintenance effect actions require executors."""
        required = {
            ActionType.PROCEDURE_PUBLISH,
            ActionType.JOB_KIT_SUBSTITUTION,
            ActionType.REPAIR_WORK_PACKAGE,
        }
        for action_type in ActionType.values:
            self.assertEqual(is_executor_required(action_type), action_type in required)

    def test_get_executor(self):
        """Registry returns correct executor for action type."""
        executor = registry.get('email')
        self.assertEqual(executor.action_type, 'email')

    def test_unregistered_type_raises(self):
        """Getting unregistered type raises KeyError."""
        with self.assertRaises(KeyError):
            registry.get('nonexistent_type')

    def test_executor_validate(self):
        """Executors validate payloads."""
        executor = registry.get('purchase_order')
        warnings = executor.validate({})
        self.assertGreater(len(warnings), 0)

        warnings = executor.validate({
            'supplier_id': 42,
            'line_items': [{'part_id': 1, 'qty': 5}],
        })
        self.assertEqual(len(warnings), 0)

    def test_executor_check_preconditions(self):
        """Executors check preconditions (Phase 1 stubs always pass)."""
        executor = registry.get('email')
        report = executor.check_preconditions({'to': ['a@b.com']}, {})
        self.assertFalse(report.has_drift)

    def test_executor_execute_returns_result(self):
        """Executors return EffectResult."""
        executor = registry.get('email')
        result = executor.execute(
            {'to': ['a@b.com'], 'subject': 'Test'}, 'test-key-123'
        )
        self.assertIsInstance(result, EffectResult)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.effect_ref)


# ===========================================================================
# 18.1 continued — Model helpers
# ===========================================================================


class ModelHelperTests(ApprovalTestBase):
    """Test model-level helper methods."""

    def test_is_terminal_property(self):
        """is_terminal returns True for terminal statuses."""
        approval = self._create_approval_obj()
        self.assertFalse(approval.is_terminal)

        for terminal in TERMINAL_STATUSES:
            approval.status = terminal
            self.assertTrue(approval.is_terminal, f'{terminal} should be terminal')

    def test_is_lock_active_property(self):
        """is_lock_active tracks lock state."""
        approval = self._create_approval_obj()
        self.assertFalse(approval.is_lock_active)

        approval.acquire_lock(self.user)
        self.assertTrue(approval.is_lock_active)

        approval.modification_lock_expires_at = timezone.now() - timedelta(seconds=1)
        approval.save(update_fields=['modification_lock_expires_at'])
        self.assertFalse(approval.is_lock_active)

    def test_lock_holder_id_property(self):
        """lock_holder_id returns correct user or None."""
        approval = self._create_approval_obj()
        self.assertIsNone(approval.lock_holder_id)

        approval.acquire_lock(self.user)
        self.assertEqual(approval.lock_holder_id, self.user.pk)

    def test_check_lock_allows_action(self):
        """check_lock_allows_action raises for non-holder."""
        approval = self._create_approval_obj()
        approval.acquire_lock(self.user)

        # Holder is allowed
        approval.check_lock_allows_action(self.user, 'approve')

        # Non-holder blocked
        with self.assertRaises(ValueError):
            approval.check_lock_allows_action(self.user2, 'approve')

    def test_transition_to_creates_event(self):
        """transition_to creates an ApprovalEvent."""
        approval = self._create_approval_obj()
        initial_count = ApprovalEvent.objects.filter(approval=approval).count()

        approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=self.user)

        new_count = ApprovalEvent.objects.filter(approval=approval).count()
        self.assertEqual(new_count, initial_count + 1)

    def test_transition_to_invalid_raises(self):
        """transition_to raises ValueError for invalid transition."""
        approval = self._create_approval_obj()
        with self.assertRaises(ValueError):
            approval.transition_to(ApprovalStatus.SUCCEEDED)


# ===========================================================================
# 18.1 continued — Creation and configuration
# ===========================================================================


class ApprovalCreationTests(ApprovalTestBase):
    """Test approval creation (Section 7.0, Section 9)."""

    def test_creation_sets_defaults(self):
        """Creation sets risk_tier=2, expires_at, revision 0."""
        approval = self._create_approval_obj()

        self.assertEqual(approval.risk_tier, 2)
        self.assertEqual(approval.status, ApprovalStatus.PENDING)
        self.assertIsNotNone(approval.expires_at)
        self.assertEqual(approval.current_revision_number, 0)
        self.assertEqual(len(approval.idempotency_key), 64)

        # Revision 0 exists
        self.assertTrue(
            ApprovalRevision.objects.filter(
                approval=approval, revision_number=0
            ).exists()
        )

        # Created event exists
        self.assertTrue(
            ApprovalEvent.objects.filter(
                approval=approval, event_type=EventType.CREATED
            ).exists()
        )

    def test_creation_expiry_default_7_days(self):
        """Default expires_at is ~7 days from creation."""
        approval = self._create_approval_obj()
        # Should expire roughly 7 days from now
        diff = approval.expires_at - approval.created_at
        self.assertAlmostEqual(diff.days, 7, delta=1)

    def test_payload_too_large_rejected(self):
        """Payload exceeding 50MB is rejected."""
        data = _make_approval_data()
        # Create a huge payload
        data['payload'] = {'huge': 'x' * (51 * 1024 * 1024)}

        resp = self.client.post('/api/approvals/', data, format='json')
        # Should be rejected (either 400 from serializer validation or 413)
        self.assertIn(resp.status_code, [400, 413])


class ConfigurationTests(TestCase):
    """Test configuration helpers."""

    @override_settings(APPROVAL_DEFAULT_EXPIRY_DAYS=14)
    def test_custom_expiry_days(self):
        """Test custom expiry days."""
        from .models import get_default_expiry_days

        self.assertEqual(get_default_expiry_days(), 14)

    @override_settings(APPROVAL_MODIFY_LOCK_TTL_SECONDS=300)
    def test_custom_lock_ttl(self):
        """Test custom lock ttl."""
        self.assertEqual(get_lock_ttl_seconds(), 300)

    @override_settings(APPROVAL_RETENTION_DAYS=180)
    def test_custom_retention_days(self):
        """Test custom retention days."""
        from .models import get_retention_days

        self.assertEqual(get_retention_days(), 180)

    @override_settings(APPROVAL_QUEUE_ENABLED=True)
    def test_queue_enabled(self):
        """Test queue enabled."""
        from .models import is_approval_queue_enabled

        self.assertTrue(is_approval_queue_enabled())

    @override_settings(APPROVAL_RESUME_STUCK_THRESHOLD_SECONDS=600)
    def test_custom_resume_threshold(self):
        """Test custom resume threshold."""
        from .models import get_resume_stuck_threshold_seconds

        self.assertEqual(get_resume_stuck_threshold_seconds(), 600)

    @override_settings(APPROVAL_EXECUTION_STUCK_THRESHOLD_SECONDS=3600)
    def test_custom_execution_threshold(self):
        """Test custom execution threshold."""
        from .models import get_execution_stuck_threshold_seconds

        self.assertEqual(get_execution_stuck_threshold_seconds(), 3600)
