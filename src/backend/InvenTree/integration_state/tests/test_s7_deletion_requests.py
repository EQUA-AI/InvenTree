"""Private deletion-readiness simulations; no native approval or actual erasure."""

import hashlib
import unittest
from dataclasses import replace
from uuid import UUID


class DeletionReadinessTests(unittest.TestCase):
    """Copy/request identity and shared retention remain independent obligations."""

    @staticmethod
    def source():
        """Use genuine typed coordinates with independently specified fixture IDs."""
        from integration_state.s7_contracts import SourceAspectIdentity

        return SourceAspectIdentity(
            environment='synthetic-s7',
            epoch=UUID(int=1),
            entity_kind='document',
            aspect='content',
            source_id=41,
        )

    def intent(self, **changes):
        """Build a private fixture request, not an authenticated native mutation."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_deletion_requests import DeletionIntent

        values = {
            'request_id': UUID(int=1),
            'request_key': 'synthetic-request',
            'request_revision': 1,
            'actor_key': 'synthetic-actor',
            'client': ClientSourceIdentity('synthetic-s7', 11),
            'scope': (self.source(),),
        }
        values.update(changes)
        return DeletionIntent(**values)

    def impact(self, **changes):
        """Make source claims explicit without treating them as historical proof."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_deletion_requests import CopyImpact

        values = {
            'copy_key': 'synthetic-copy',
            'source': self.source(),
            'copy_revision': 'revision-1',
            'owners': (ClientSourceIdentity('synthetic-s7', 11),),
            'attribution': 'source_claimed',
            'retention': 'reviewable',
        }
        values.update(changes)
        return CopyImpact(**values)

    def test_foreign_copy_claim_is_rejected_before_reading_its_fields(self):
        """Type admission precedes properties on an untrusted-shaped value."""
        from integration_state.s7_deletion_requests import assess_readiness

        touched = []

        class ForeignImpact:
            @property
            def copy_key(self):
                touched.append('read-before-admission')
                return 'synthetic-foreign-key'

        with self.assertRaises(ValueError):
            assess_readiness(
                self.intent(), (ForeignImpact(),), authorize=lambda _: True
            )
        self.assertEqual(touched, [])

    def test_actor_bound_intent_duplicate_and_conflict_keep_exact_scope(self):
        """A duplicate never adopts another actor/client, revision or literal payload."""
        from integration_state.s7_deletion_requests import same_intent

        original = self.intent()
        self.assertTrue(same_intent(original, self.intent()))
        with self.assertRaises(PermissionError):
            same_intent(original, self.intent(actor_key='other-actor'))
        with self.assertRaises(ValueError):
            same_intent(original, self.intent(request_revision=2))
        with self.assertRaises(ValueError):
            same_intent(
                original, self.intent(scope=(replace(self.source(), source_id='41'),))
            )
        for field, value in (
            ('request_revision', True),
            ('request_id', str(UUID(int=1))),
            ('scope', ()),
            ('actor_key', ''),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.intent(**{field: value})

    def test_indefinite_shared_unknown_and_reviewable_copies_stay_separate(self):
        """Even all-reviewable claims do not establish native authority or coverage."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_deletion_requests import CopyImpact, assess_readiness

        intent = self.intent()
        default_retained = CopyImpact(
            copy_key='default-retained',
            source=self.source(),
            copy_revision='r1',
            owners=(intent.client,),
            attribution='source_claimed',
        )
        shared = self.impact(
            copy_key='shared',
            owners=(intent.client, ClientSourceIdentity('synthetic-s7', 12)),
        )
        unknown = self.impact(copy_key='unknown', owners=None, attribution='unknown')
        reviewable = self.impact(copy_key='reviewable')
        report = assess_readiness(
            intent,
            (default_retained, shared, unknown, reviewable),
            authorize=lambda _: True,
        )
        self.assertEqual(
            [row.disposition for row in report.decisions],
            ['policy_retained', 'shared_retained', 'unresolved', 'review_required'],
        )
        self.assertEqual(report.state, 'blocked')
        self.assertEqual(default_retained.retention, 'indefinite')
        self.assertEqual(report.request.default_retention, 'indefinite')
        self.assertFalse(report.coverage_complete)
        self.assertFalse(report.native_authorized)
        self.assertFalse(report.erasure_complete)
        self.assertFalse(report.deletion_applied)
        self.assertEqual(
            assess_readiness(intent, (reviewable,), authorize=lambda _: True).state,
            'needs_native_review',
        )
        self.assertEqual(
            assess_readiness(intent, (), authorize=lambda _: True).state, 'blocked'
        )
        with self.assertRaises(ValueError):
            assess_readiness(intent, (reviewable, reviewable), authorize=lambda _: True)

    def test_synthetic_progress_is_request_copy_attempt_and_sequence_fenced(self):
        """Partial/replayed responses cannot advance a newer request or copy."""
        from integration_state.s7_deletion_requests import (
            start_copy_progress,
            transition_copy_progress,
        )

        intent, impact = self.intent(), self.impact()
        initial = start_copy_progress(
            intent,
            impact,
            attempt=UUID(int=2),
            max_attempts=2,
            authorize=lambda _: True,
        )
        pending = replace(initial, sequence=1, status='pending')
        self.assertEqual(
            transition_copy_progress(
                intent, impact, initial, pending, authorize=lambda _: True
            ),
            pending,
        )
        digest = hashlib.sha256(
            b'synthetic verification only; no file was erased'
        ).hexdigest()
        verified = replace(
            pending, sequence=2, status='synthetic_verified', evidence_digest=digest
        )
        self.assertEqual(
            transition_copy_progress(
                intent, impact, pending, verified, authorize=lambda _: True
            ),
            verified,
        )
        self.assertEqual(
            transition_copy_progress(
                intent, impact, verified, verified, authorize=lambda _: True
            ),
            verified,
        )
        self.assertEqual(
            transition_copy_progress(
                intent, impact, verified, pending, authorize=lambda _: True
            ),
            verified,
        )
        conflict = replace(
            verified,
            evidence_digest=hashlib.sha256(b'different fixture truth').hexdigest(),
        )
        with self.assertRaises(ValueError):
            transition_copy_progress(
                intent, impact, verified, conflict, authorize=lambda _: True
            )
        for changed_intent, changed_impact in (
            (self.intent(request_revision=2), impact),
            (intent, replace(impact, copy_revision='revision-2')),
        ):
            with (
                self.subTest(request_revision=changed_intent.request_revision),
                self.assertRaises(ValueError),
            ):
                transition_copy_progress(
                    changed_intent,
                    changed_impact,
                    pending,
                    verified,
                    authorize=lambda _: True,
                )
        with self.assertRaises(ValueError):
            transition_copy_progress(
                intent,
                impact,
                initial,
                replace(pending, sequence=3),
                authorize=lambda _: True,
            )
        with self.assertRaises(ValueError):
            replace(pending, status='synthetic_verified', evidence_digest=None)
        self.assertTrue(verified.synthetic_only)
        self.assertFalse(verified.deletion_applied)
        self.assertFalse(verified.erasure_complete)

    def test_shared_and_policy_retained_copies_cannot_gain_synthetic_success(self):
        """A progress update cannot erase a protected/shared-copy conflict."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_deletion_requests import (
            start_copy_progress,
            transition_copy_progress,
        )

        intent = self.intent()
        for impact in (
            self.impact(retention='indefinite'),
            self.impact(
                owners=(intent.client, ClientSourceIdentity('synthetic-s7', 12))
            ),
        ):
            with self.subTest(policy=impact.retention):
                retained = start_copy_progress(
                    intent,
                    impact,
                    attempt=UUID(int=2),
                    max_attempts=2,
                    authorize=lambda _: True,
                )
                self.assertEqual(retained.status, 'retained')
                with self.assertRaises(ValueError):
                    transition_copy_progress(
                        intent,
                        impact,
                        retained,
                        replace(retained, sequence=1, status='pending'),
                        authorize=lambda _: True,
                    )
                self.assertFalse(retained.deletion_applied)

    def test_uncertainty_retry_keeps_attempt_history_and_budget(self):
        """Retry never reuses a lost attempt or resets a failed request's allowance."""
        from integration_state.s7_deletion_requests import (
            retry_copy_progress,
            start_copy_progress,
            transition_copy_progress,
        )

        intent, impact = self.intent(), self.impact()
        initial = start_copy_progress(
            intent,
            impact,
            attempt=UUID(int=2),
            max_attempts=2,
            authorize=lambda _: True,
        )
        pending = replace(initial, sequence=1, status='pending')
        uncertain = replace(pending, sequence=2, status='uncertain')
        observed = transition_copy_progress(
            intent, impact, pending, uncertain, authorize=lambda _: True
        )
        retry = retry_copy_progress(
            intent, impact, observed, attempt=UUID(int=3), authorize=lambda _: True
        )
        self.assertEqual(retry.status, 'planned')
        self.assertEqual(retry.attempts_used, 2)
        self.assertEqual(retry.attempt_history, (UUID(int=2), UUID(int=3)))
        again_pending = replace(retry, sequence=1, status='pending')
        failed = replace(again_pending, sequence=2, status='failed')
        observed = transition_copy_progress(
            intent, impact, again_pending, failed, authorize=lambda _: True
        )
        with self.assertRaises(ValueError):
            retry_copy_progress(
                intent, impact, observed, attempt=UUID(int=4), authorize=lambda _: True
            )
        with self.assertRaises(ValueError):
            retry_copy_progress(
                intent, impact, uncertain, attempt=UUID(int=2), authorize=lambda _: True
            )

    def test_current_authority_and_faults_do_not_become_empty_success(self):
        """Callback checks are protocol-only; they do not grant native approval."""
        from integration_state.s7_deletion_requests import (
            assess_readiness,
            start_copy_progress,
        )

        intent, impact = self.intent(), self.impact()
        with self.assertRaises(PermissionError):
            assess_readiness(intent, (impact,), authorize=lambda _: False)
        checks = iter((True, False))
        with self.assertRaises(PermissionError):
            start_copy_progress(
                intent,
                impact,
                attempt=UUID(int=2),
                max_attempts=2,
                authorize=lambda _: next(checks),
            )

        def unavailable(_):
            raise RuntimeError('Synthetic owner authority fault')

        with self.assertRaises(RuntimeError):
            assess_readiness(intent, (impact,), authorize=unavailable)

    def test_replay_rejects_changed_ownership_or_retention_without_content_change(self):
        """A content revision alone does not bind a copy's disclosure/retention scope."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_deletion_requests import (
            start_copy_progress,
            transition_copy_progress,
        )

        intent, impact = self.intent(), self.impact()
        initial = start_copy_progress(
            intent,
            impact,
            attempt=UUID(int=2),
            max_attempts=2,
            authorize=lambda _: True,
        )
        pending = replace(initial, sequence=1, status='pending')
        verified = replace(
            pending,
            sequence=2,
            status='synthetic_verified',
            evidence_digest=hashlib.sha256(
                b'explicit synthetic fixture proof'
            ).hexdigest(),
        )
        previous = transition_copy_progress(
            intent, impact, pending, verified, authorize=lambda _: True
        )
        for changed in (
            replace(
                impact, owners=(intent.client, ClientSourceIdentity('synthetic-s7', 12))
            ),
            replace(impact, retention='indefinite'),
        ):
            with (
                self.subTest(retention=changed.retention),
                self.assertRaises(ValueError),
            ):
                transition_copy_progress(
                    intent, changed, previous, previous, authorize=lambda _: True
                )
