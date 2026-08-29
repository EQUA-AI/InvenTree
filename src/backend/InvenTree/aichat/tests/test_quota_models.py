"""S12 WP-B1: durable quota models and the assignment service.

Covers the permission gate, expiry-by-construction, audit rows, the
resolver-facing ``active_assignment`` lookup, the enforceability validator,
and the AI-plane loader's translation of a live assignment.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from aichat.models import (
    AIQuotaAssignment,
    AIQuotaAuditEvent,
    AIQuotaPolicy,
    AIQuotaProfile,
)
from aichat.services import quota


def _tomorrow():
    return timezone.now() + datetime.timedelta(days=1)


class QuotaServiceTests(TestCase):
    """Policy creation, assignment, revocation, and validation."""

    def setUp(self) -> None:
        """A permitted manager, an unpermitted user, and a target user."""
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(username='quota-manager')
        self.manager.user_permissions.add(
            Permission.objects.get(codename='assign_quota_policy')
        )
        self.manager = user_model.objects.get(pk=self.manager.pk)  # refresh perm cache
        self.plain = user_model.objects.create_user(username='quota-plain')
        self.target = user_model.objects.create_user(username='quota-target')

    def _policy(self, **overrides) -> AIQuotaPolicy:
        """Create the next evaluation-profile policy version."""
        params = {
            'profile': AIQuotaProfile.EVALUATION,
            'user_daily_tokens': 5_000_000,
            'tenant_daily_tokens': 20_000_000,
            'deployment_daily_tokens': 20_000_000,
            'requests_per_minute': 200,
            'requests_per_hour': 2_000,
        }
        params.update(overrides)
        return quota.create_policy(self.manager, **params)

    # ---- permission gate -------------------------------------------------

    def test_policy_management_requires_the_dedicated_permission(self) -> None:
        """Only assign_quota_policy holders may create or assign policies."""
        with self.assertRaises(PermissionDenied):
            quota.create_policy(
                self.plain,
                profile=AIQuotaProfile.STANDARD,
                user_daily_tokens=1,
                tenant_daily_tokens=1,
                deployment_daily_tokens=1,
                requests_per_minute=1,
                requests_per_hour=1,
            )
        policy = self._policy()
        with self.assertRaises(PermissionDenied):
            quota.assign_policy(
                self.plain, user=self.target, policy=policy, expires_at=_tomorrow()
            )
        # Revocation is management too (S17 gap-fill: the guard existed,
        # its denial path was unpinned).
        assignment = quota.assign_policy(
            self.manager, user=self.target, policy=policy, expires_at=_tomorrow()
        )
        with self.assertRaises(PermissionDenied):
            quota.revoke_assignment(self.plain, assignment=assignment)

    # ---- policies --------------------------------------------------------

    def test_policy_versions_increment_and_audit(self) -> None:
        """Each create_policy call mints the next version and one audit row."""
        first = self._policy()
        second = self._policy()
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(
            AIQuotaAuditEvent.objects.filter(action='policy_created').count(), 2
        )

    def test_validator_rejects_zero_legged_active_policies(self) -> None:
        """A zero cap on any level makes the policy set unenforceable."""
        self._policy()
        quota.validate_enforceable_policies()  # all caps present: fine
        AIQuotaPolicy.objects.create(
            profile=AIQuotaProfile.SERVICE,
            version=1,
            user_daily_tokens=0,
            tenant_daily_tokens=1,
            deployment_daily_tokens=1,
            requests_per_minute=1,
            requests_per_hour=1,
        )
        with self.assertRaises(quota.QuotaPolicyInvalid):
            quota.validate_enforceable_policies()

    # ---- assignments -----------------------------------------------------

    def test_assignment_must_expire_in_the_future(self) -> None:
        """Assignments are expiring by construction."""
        policy = self._policy()
        with self.assertRaises(quota.QuotaPolicyInvalid):
            quota.assign_policy(
                self.manager,
                user=self.target,
                policy=policy,
                expires_at=timezone.now() - datetime.timedelta(minutes=1),
            )

    def test_assignment_resolution_expiry_and_revocation(self) -> None:
        """active_assignment honors revocation and expiry, with audit."""
        policy = self._policy()
        assignment = quota.assign_policy(
            self.manager, user=self.target, policy=policy, expires_at=_tomorrow()
        )
        self.assertEqual(quota.active_assignment(self.target.pk), assignment)

        quota.revoke_assignment(self.manager, assignment=assignment, reason='done')
        self.assertIsNone(quota.active_assignment(self.target.pk))
        self.assertTrue(
            AIQuotaAuditEvent.objects.filter(
                action='revoked', target_user=self.target
            ).exists()
        )

        # Expired assignments never resolve.
        expired = quota.assign_policy(
            self.manager, user=self.target, policy=policy, expires_at=_tomorrow()
        )
        AIQuotaAssignment.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1)
        )
        self.assertIsNone(quota.active_assignment(self.target.pk))

    def test_inactive_policy_cannot_be_assigned_and_stops_resolving(self) -> None:
        """Deactivating a policy retires live assignments too."""
        policy = self._policy()
        assignment = quota.assign_policy(
            self.manager, user=self.target, policy=policy, expires_at=_tomorrow()
        )
        self.assertIsNotNone(assignment)
        AIQuotaPolicy.objects.filter(pk=policy.pk).update(active=False)
        self.assertIsNone(quota.active_assignment(self.target.pk))
        policy.refresh_from_db()
        with self.assertRaises(quota.QuotaPolicyInvalid):
            quota.assign_policy(
                self.manager, user=self.target, policy=policy, expires_at=_tomorrow()
            )

    # ---- the AI-plane loader --------------------------------------------

    def test_loader_translates_a_live_assignment(self) -> None:
        """The AI-plane loader mirrors the durable row into a PolicySnapshot."""
        from ai.core.quota.assignment_source import load_assignment

        self.assertIsNone(load_assignment(self.target.pk))
        policy = self._policy()
        quota.assign_policy(
            self.manager, user=self.target, policy=policy, expires_at=_tomorrow()
        )
        snapshot = load_assignment(self.target.pk)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.profile, 'evaluation')
        self.assertEqual(snapshot.user_cap, 5_000_000)
        self.assertEqual(snapshot.requests_per_minute, 200)


class QuotaReconciliationTests(TestCase):
    """The 5-minute sweep expires orphaned RESERVED rows only."""

    def test_orphaned_reservations_expire_and_settled_rows_survive(self) -> None:
        """Only past-expiry RESERVED rows flip to EXPIRED."""
        from aichat.models import AIQuotaReservation, AIQuotaReservationState
        from aichat.tasks import reconcile_quota_reservations

        stale = AIQuotaReservation.objects.create(
            idempotency_key='stale-turn',
            reserved_tokens=500,
            expires_at=timezone.now() - datetime.timedelta(minutes=1),
        )
        live = AIQuotaReservation.objects.create(
            idempotency_key='live-turn',
            reserved_tokens=500,
            expires_at=timezone.now() + datetime.timedelta(hours=1),
        )
        settled = AIQuotaReservation.objects.create(
            idempotency_key='settled-turn',
            reserved_tokens=500,
            settled_tokens=120,
            state=AIQuotaReservationState.SETTLED,
            expires_at=timezone.now() - datetime.timedelta(minutes=1),
        )
        reconcile_quota_reservations()
        stale.refresh_from_db()
        live.refresh_from_db()
        settled.refresh_from_db()
        self.assertEqual(stale.state, AIQuotaReservationState.EXPIRED)
        self.assertEqual(live.state, AIQuotaReservationState.RESERVED)
        self.assertEqual(settled.state, AIQuotaReservationState.SETTLED)
