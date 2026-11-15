"""Private persisted S7 intent/copy controls, never native deletion authority."""

from integration_state.tests.assertions import assert_literal_bool

from uuid import uuid4

from django.db import connection
from django.test import TestCase


class SharedRequestTests(TestCase):
    """Real private metadata round trips with explicitly synthetic owner seams."""

    def coordinates(self):
        """Exact independent actor/client/source fixture, not an authenticated grant."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.models import SourceNamespace
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_deletion_requests import DeletionIntent

        namespace = SourceNamespace.objects.create(
            environment='synthetic-private-request', active=True
        )
        scope = tuple(
            SourceAspectIdentity(
                environment=namespace.environment,
                epoch=namespace.epoch,
                entity_kind='synthetic_document',
                aspect='metadata',
                source_id=key,
            )
            for key in (7, '7')
        )
        intent = DeletionIntent(
            request_id=uuid4(),
            request_key='private-fixture-request',
            request_revision=1,
            actor_key='synthetic-owner',
            client=ClientSourceIdentity(namespace.environment, 7),
            scope=scope,
        )
        return namespace, intent

    def test_actual_intent_is_retained_idempotently_without_approval_or_erasure(self):
        """The actual typed peer intent survives storage without fabricated power."""
        from integration_state.models import PrivateDeletionRequest
        from integration_state.s7_deletion_requests import (
            DeletionIntent,
            intent_fingerprint,
        )
        from integration_state.shared_requests import (
            read_private_intent,
            record_private_intent,
        )

        namespace, intent = self.coordinates()
        depths = []
        outside = len(connection.atomic_blocks)

        def owner(value):
            self.assertEqual(value, intent)
            depths.append(len(connection.atomic_blocks))
            return True

        stored = record_private_intent(namespace.pk, intent, authorize=owner)
        self.assertIs(type(stored.intent), DeletionIntent)
        self.assertEqual(stored.intent, intent)
        self.assertEqual(stored.intent.default_retention, 'indefinite')
        self.assertEqual(stored.intent_digest, intent_fingerprint(intent))
        self.assertEqual(
            record_private_intent(namespace.pk, intent, authorize=owner), stored
        )
        self.assertEqual(
            read_private_intent(stored.request_pk, intent=intent, authorize=owner),
            stored,
        )
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
        for name in (
            'native_authorized',
            'coverage_complete',
            'deletion_applied',
            'erasure_complete',
        ):
            assert_literal_bool(self, getattr(stored, name), False)
        self.assertTrue(depths)
        self.assertEqual(set(depths), {outside})

    def test_default_retained_copy_and_actual_synthetic_progress_survive_storage(self):
        """Indefinite copy metadata and unknown revision are not erasure evidence."""
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt, PrivateCopyImpact
        from integration_state.s7_deletion_requests import CopyImpact, CopyProgress

        namespace, intent = self.coordinates()
        request = durable.record_private_intent(
            namespace.pk, intent, authorize=lambda value: True
        )
        impact = CopyImpact(
            copy_key='private-fixture-copy',
            source=intent.scope[0],
            copy_revision=None,
            owners=(intent.client,),
            attribution='source_claimed',
        )
        stored = durable.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind='backup',
            authorize=lambda value: True,
        )
        self.assertEqual(stored.impact, impact)
        self.assertEqual(stored.copy_kind, 'backup')
        self.assertEqual(
            durable.record_copy_impact(
                request.request_pk,
                impact,
                intent=intent,
                copy_kind='backup',
                authorize=lambda value: True,
            ),
            stored,
        )
        attempt = uuid4()
        pending = durable.start_private_copy_attempt(
            stored.impact_pk,
            intent=intent,
            impact=impact,
            attempt=attempt,
            max_attempts=2,
            authorize=lambda value: True,
        )
        self.assertIs(type(pending.progress), CopyProgress)
        self.assertEqual(pending.progress.status, 'retained')
        self.assertIsNone(pending.progress.copy_revision)
        self.assertEqual(pending.progress.attempt_history, (attempt,))
        self.assertEqual(
            durable.read_private_copy_progress(
                stored.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            pending,
        )
        self.assertEqual(
            durable.start_private_copy_attempt(
                stored.impact_pk,
                intent=intent,
                impact=impact,
                attempt=attempt,
                max_attempts=2,
                authorize=lambda value: True,
            ),
            pending,
        )
        self.assertEqual(PrivateCopyImpact.objects.count(), 1)
        self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
        self.assertFalse(pending.progress.deletion_applied)
        self.assertFalse(pending.progress.erasure_complete)
        self.assertTrue(pending.progress.synthetic_only)

    def reviewable(self):
        """A source-claimed fixture copy; reviewable is still not native approval."""
        from integration_state import shared_requests as durable
        from integration_state.s7_deletion_requests import CopyImpact

        namespace, intent = self.coordinates()
        request = durable.record_private_intent(
            namespace.pk, intent, authorize=lambda value: True
        )
        impact = CopyImpact(
            copy_key='private-reviewable-copy',
            source=intent.scope[0],
            copy_revision='fixture-revision-1',
            owners=(intent.client,),
            attribution='source_claimed',
            retention='reviewable',
        )
        stored = durable.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind='derivative',
            authorize=lambda value: True,
        )
        initial = durable.start_private_copy_attempt(
            stored.impact_pk,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            max_attempts=2,
            authorize=lambda value: True,
        )
        return intent, impact, initial

    def test_actual_synthetic_progress_sequence_and_replay_are_durable(self):
        """Metadata verification stays synthetic and cannot regress after replay."""
        import hashlib
        from dataclasses import replace

        from integration_state import shared_requests as durable

        intent, impact, initial = self.reviewable()
        proposed = replace(initial.progress, sequence=1, status='pending')
        pending = durable.record_private_copy_progress(
            initial,
            proposed,
            intent=intent,
            impact=impact,
            authorize=lambda value: True,
        )
        self.assertEqual(pending.progress, proposed)
        self.assertEqual(
            durable.record_private_copy_progress(
                initial,
                proposed,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            pending,
        )
        self.assertEqual(
            durable.record_private_copy_progress(
                pending,
                proposed,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            pending,
        )
        verified = replace(
            proposed,
            sequence=2,
            status='synthetic_verified',
            evidence_digest=hashlib.sha256(
                b'synthetic fixture only; no erasure'
            ).hexdigest(),
        )
        done = durable.record_private_copy_progress(
            pending,
            verified,
            intent=intent,
            impact=impact,
            authorize=lambda value: True,
        )
        self.assertEqual(done.progress, verified)
        self.assertEqual(
            durable.record_private_copy_progress(
                done,
                proposed,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            done,
        )
        self.assertEqual(
            durable.read_private_copy_progress(
                done.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            done,
        )
        self.assertFalse(done.deletion_applied)
        self.assertFalse(done.erasure_complete)

    def test_latest_attempt_requires_qualified_prior_failure_not_just_history_ids(self):
        """A retry cannot hide a changed successful predecessor behind valid IDs."""
        import hashlib
        from dataclasses import replace

        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt
        from integration_state.shared_events import SharedStateError

        intent, impact, initial = self.reviewable()
        failed = durable.record_private_copy_progress(
            initial,
            replace(initial.progress, sequence=1, status='uncertain'),
            intent=intent,
            impact=impact,
            authorize=lambda value: True,
        )
        retry = durable.retry_private_copy_attempt(
            failed,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            authorize=lambda value: True,
        )
        self.assertEqual(
            durable.read_private_copy_progress(
                retry.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            retry,
        )
        row = PrivateCopyAttempt.objects.get(pk=failed.attempt_pk)
        payload = dict(row.checkpoint)
        payload.update(
            status='synthetic_verified',
            evidence_digest=hashlib.sha256(
                b'forged synthetic prior result'
            ).hexdigest(),
        )
        PrivateCopyAttempt.objects.filter(pk=row.pk).update(
            checkpoint=payload, checkpoint_digest=durable.fingerprint(payload)
        )
        with self.assertRaises(SharedStateError):
            durable.read_private_copy_progress(
                retry.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            )

    def test_schema_valid_transition_result_cannot_replace_the_requested_status(self):
        """Hashing a forged result is not independent input/output integrity."""
        from dataclasses import replace
        from unittest import mock

        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt
        from integration_state.shared_events import SharedStateError

        intent, impact, initial = self.reviewable()
        incoming = replace(initial.progress, sequence=1, status='pending')
        original = durable.peer.transition_copy_progress
        touched = []

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            touched.append(True)
            return replace(result, status='uncertain')

        with mock.patch.object(
            durable.peer, 'transition_copy_progress', side_effect=changed
        ):
            with self.assertRaises(SharedStateError):
                durable.record_private_copy_progress(
                    initial,
                    incoming,
                    intent=intent,
                    impact=impact,
                    authorize=lambda value: True,
                )
        self.assertEqual(touched, [True])
        self.assertEqual(
            PrivateCopyAttempt.objects.get().checkpoint['status'], 'planned'
        )

    def test_actor_client_revision_and_scope_replays_cannot_adopt_a_private_request(
        self,
    ):
        """An idempotency label is not permission to rewrite its original owner."""
        from dataclasses import replace

        from assets.integration_contracts import ClientSourceIdentity
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateDeletionRequest
        from integration_state.shared_events import SharedStateError

        namespace, intent = self.coordinates()
        stored = durable.record_private_intent(
            namespace.pk, intent, authorize=lambda value: True
        )
        for altered, error in (
            (replace(intent, actor_key='other-owner'), PermissionError),
            (
                replace(intent, client=ClientSourceIdentity(namespace.environment, 8)),
                PermissionError,
            ),
            (replace(intent, request_revision=2), SharedStateError),
            (replace(intent, request_key='other-key'), SharedStateError),
            (replace(intent, request_id=uuid4()), SharedStateError),
            (
                replace(intent, scope=(replace(intent.scope[0], source_id='007'),)),
                SharedStateError,
            ),
        ):
            with self.subTest(error=error.__name__):
                with self.assertRaises(error):
                    durable.record_private_intent(
                        namespace.pk, altered, authorize=lambda value: True
                    )
                with self.assertRaises(error):
                    durable.read_private_intent(
                        stored.request_pk, intent=altered, authorize=lambda value: True
                    )
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)

    def test_copy_replay_fences_ownership_retention_and_source_without_content_change(
        self,
    ):
        """An unchanged content revision cannot qualify changed independent claims."""
        from dataclasses import replace

        from assets.integration_contracts import ClientSourceIdentity
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyImpact
        from integration_state.shared_events import SharedConflictError

        intent, impact, initial = self.reviewable()
        request_pk = PrivateCopyImpact.objects.get(pk=initial.impact_pk).request_id
        for altered in (
            replace(
                impact,
                owners=(
                    intent.client,
                    ClientSourceIdentity(intent.client.environment, 8),
                ),
            ),
            replace(impact, owners=None),
            replace(impact, retention='indefinite'),
            replace(impact, retention='protected'),
            replace(impact, attribution='unknown'),
            replace(impact, source=intent.scope[1]),
        ):
            with self.subTest(
                retention=altered.retention, attribution=altered.attribution
            ):
                self.assertEqual(altered.copy_revision, impact.copy_revision)
                with self.assertRaises(SharedConflictError):
                    durable.record_copy_impact(
                        request_pk,
                        altered,
                        intent=intent,
                        copy_kind='derivative',
                        authorize=lambda value: True,
                    )
                with self.assertRaises(SharedConflictError):
                    durable.read_private_copy_progress(
                        initial.impact_pk,
                        intent=intent,
                        impact=altered,
                        authorize=lambda value: True,
                    )
        with self.assertRaises(SharedConflictError):
            durable.record_copy_impact(
                request_pk,
                impact,
                intent=intent,
                copy_kind='cache',
                authorize=lambda value: True,
            )
        self.assertEqual(PrivateCopyImpact.objects.count(), 1)
        self.assertEqual(
            durable.read_private_copy_progress(
                initial.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            initial,
        )

    def test_shared_protected_unknown_copy_claims_never_gain_synthetic_success(self):
        """Private bookkeeping preserves non-actionable actual S7 dispositions."""
        from dataclasses import replace

        from assets.integration_contracts import ClientSourceIdentity
        from integration_state import shared_requests as durable
        from integration_state.s7_deletion_requests import CopyImpact

        namespace, intent = self.coordinates()
        request = durable.record_private_intent(
            namespace.pk, intent, authorize=lambda value: True
        )
        for index, values in enumerate((
            {
                'owners': (intent.client,),
                'attribution': 'source_claimed',
                'retention': 'indefinite',
            },
            {
                'owners': (intent.client,),
                'attribution': 'source_claimed',
                'retention': 'protected',
            },
            {
                'owners': (
                    intent.client,
                    ClientSourceIdentity(namespace.environment, 8),
                ),
                'attribution': 'source_claimed',
                'retention': 'reviewable',
            },
            {'owners': None, 'attribution': 'unknown', 'retention': 'unknown'},
        )):
            with self.subTest(case=index):
                impact = CopyImpact(
                    copy_key=f'non-actionable-{index}',
                    source=intent.scope[0],
                    copy_revision='fixture-revision',
                    **values,
                )
                stored = durable.record_copy_impact(
                    request.request_pk,
                    impact,
                    intent=intent,
                    copy_kind='unknown',
                    authorize=lambda value: True,
                )
                initial = durable.start_private_copy_attempt(
                    stored.impact_pk,
                    intent=intent,
                    impact=impact,
                    attempt=uuid4(),
                    max_attempts=2,
                    authorize=lambda value: True,
                )
                self.assertEqual(
                    initial.progress.status, 'uncertain' if index == 3 else 'retained'
                )
                with self.assertRaises(ValueError):
                    durable.record_private_copy_progress(
                        initial,
                        replace(initial.progress, sequence=1, status='pending'),
                        intent=intent,
                        impact=impact,
                        authorize=lambda value: True,
                    )
                with self.assertRaises(ValueError):
                    durable.retry_private_copy_attempt(
                        initial,
                        intent=intent,
                        impact=impact,
                        attempt=uuid4(),
                        authorize=lambda value: True,
                    )
                self.assertFalse(initial.native_authorized)
                self.assertFalse(initial.coverage_complete)
                self.assertFalse(initial.deletion_applied)
                self.assertFalse(initial.erasure_complete)

    def test_caller_savepoint_and_post_work_owner_loss_cannot_export_progress(self):
        """Committed metadata may be retained when a current result is withheld."""
        from dataclasses import replace

        from django.db import transaction

        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt
        from integration_state.shared_events import SharedStateError

        intent, impact, initial = self.reviewable()
        incoming = replace(initial.progress, sequence=1, status='pending')
        with transaction.atomic(), self.assertRaises(SharedStateError):
            durable.record_private_copy_progress(
                initial,
                incoming,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            )
        self.assertEqual(PrivateCopyAttempt.objects.get().version, initial.version)
        checks = iter((True, False))
        with self.assertRaises(PermissionError):
            durable.record_private_copy_progress(
                initial,
                incoming,
                intent=intent,
                impact=impact,
                authorize=lambda value: next(checks),
            )
        self.assertEqual(
            PrivateCopyAttempt.objects.get().checkpoint['status'], 'pending'
        )
        with self.assertRaises(PermissionError):
            durable.read_private_copy_progress(
                initial.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: False,
            )

    def test_private_store_fault_stays_typed_and_rechecks_owner_outside_locks(self):
        """A state dependency failure cannot become an empty successful receipt."""
        from unittest import mock

        from django.db import OperationalError

        from integration_state import shared_requests as durable
        from integration_state.shared_events import SharedStoreUnavailableError

        intent, impact, initial = self.reviewable()
        depths, outside = [], len(connection.atomic_blocks)

        def owner(value):
            depths.append(len(connection.atomic_blocks))
            return True

        with mock.patch.object(
            durable,
            '_open_impact',
            side_effect=OperationalError('synthetic private store failure'),
        ):
            with self.assertRaises(SharedStoreUnavailableError):
                durable.read_private_copy_progress(
                    initial.impact_pk, intent=intent, impact=impact, authorize=owner
                )
            checks = iter((True, False))
            with self.assertRaises(PermissionError):
                durable.read_private_copy_progress(
                    initial.impact_pk,
                    intent=intent,
                    impact=impact,
                    authorize=lambda value: next(checks),
                )
        self.assertEqual(depths, [outside, outside])

    def test_uncertain_retry_retains_prior_attempt_and_cumulative_budget(self):
        """A new synthetic attempt cannot reuse an ID, reset budget or accept old IO."""
        from dataclasses import replace

        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt
        from integration_state.shared_events import SharedConflictError

        intent, impact, initial = self.reviewable()
        pending = durable.record_private_copy_progress(
            initial,
            replace(initial.progress, sequence=1, status='pending'),
            intent=intent,
            impact=impact,
            authorize=lambda value: True,
        )
        uncertain = durable.record_private_copy_progress(
            pending,
            replace(pending.progress, sequence=2, status='uncertain'),
            intent=intent,
            impact=impact,
            authorize=lambda value: True,
        )
        retry = durable.retry_private_copy_attempt(
            uncertain,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            authorize=lambda value: True,
        )
        self.assertEqual(retry.progress.attempts_used, 2)
        self.assertEqual(
            retry.progress.attempt_history[:-1], uncertain.progress.attempt_history
        )
        self.assertEqual(retry.progress.max_attempts, 2)
        self.assertEqual(retry.progress.status, 'planned')
        self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
        self.assertEqual(
            PrivateCopyAttempt.objects.get(pk=uncertain.attempt_pk).checkpoint[
                'status'
            ],
            'uncertain',
        )
        with self.assertRaises(SharedConflictError):
            durable.record_private_copy_progress(
                uncertain,
                uncertain.progress,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            )
        failed = durable.record_private_copy_progress(
            retry,
            replace(retry.progress, sequence=1, status='uncertain'),
            intent=intent,
            impact=impact,
            authorize=lambda value: True,
        )
        with self.assertRaises(ValueError):
            durable.retry_private_copy_attempt(
                failed,
                intent=intent,
                impact=impact,
                attempt=uuid4(),
                authorize=lambda value: True,
            )
        self.assertEqual(
            durable.read_private_copy_progress(
                retry.impact_pk,
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            ),
            failed,
        )
