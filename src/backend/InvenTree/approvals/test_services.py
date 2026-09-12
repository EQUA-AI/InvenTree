"""Direct-call parity of the extracted approval business services."""

from . import services
from .models import ApprovalStatus
from .tests import ApprovalTestBase


class ApprovalServicesTests(ApprovalTestBase):
    """Business actions are callable without fabricating an HTTP request."""

    def test_open_review_and_deny(self):
        """The service preserves the existing payload and state transitions."""
        approval = self._create_approval_obj()
        opened = services.open_approval(approval.pk, actor=self.user)
        self.assertEqual(opened.status, 200)
        self.assertEqual(opened.data['status'], ApprovalStatus.IN_REVIEW)
        services.confirm_viewed(approval.pk, actor=self.user)
        denied = services.deny(
            approval.pk, actor=self.user, data={'reason': 'Test rejection'}
        )
        self.assertEqual(denied.data['status'], ApprovalStatus.DENIED)
        approval.refresh_from_db()
        self.assertEqual(approval.deny_reason, 'Test rejection')

    def test_conflict_is_typed(self):
        """Illegal transitions expose the same public status and detail."""
        approval = self._create_approval_obj()
        with self.assertRaises(services.ApprovalConflictError) as error:
            services.deny(approval.pk, actor=self.user, data={'reason': 'Not opened'})
        self.assertEqual(error.exception.http_status, 409)
        self.assertEqual(error.exception.code, 'conflict')

    def test_direct_call_requires_review_permission(self):
        """Non-HTTP callers cannot skip the reviewer permission check."""
        approval = self._create_approval_obj()
        self.user2.user_permissions.clear()
        actor = type(self.user2).objects.get(pk=self.user2.pk)
        with self.assertRaises(services.ApprovalForbiddenError):
            services.open_approval(approval.pk, actor=actor)

    def test_lock_revision_and_cancel(self):
        """Lock and revision services preserve cancellation's prior payload."""
        approval = self._create_approval_obj()
        original = approval.payload
        services.open_approval(approval.pk, actor=self.user)
        services.acquire_modify_lock(approval.pk, actor=self.user)
        services.revise(
            approval.pk,
            actor=self.user,
            data={'payload': {'changed': True}, 'expected_revision': 0},
        )
        services.release_modify_lock(approval.pk, actor=self.user)
        result = services.cancel(
            approval.pk, actor=self.user, data={'reason': 'Test cancellation'}
        )
        self.assertEqual(result.data['status'], ApprovalStatus.CANCELED)
        approval.refresh_from_db()
        self.assertEqual(approval.payload, original)
