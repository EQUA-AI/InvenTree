"""Actual native S6 source to existing private S5/S7 fixture copy planning."""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase

from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from assets.integration_contracts import ClientSourceIdentity
from integration_state.models import (
    PrivateCopyAttempt,
    PrivateCopyImpact,
    PrivateDeletionRequest,
    SourceNamespace,
)
from machine_health.services import integration_s6_delivery as delivery


class HistoryCopyTests(S6AIRequestFixture, TransactionTestCase):
    """Source read eligibility is not native deletion or historical copy authority."""

    def setUp(self):
        """Use only the owned synthetic source and private metadata database."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='s6-test', active=True
        )
        self.request_id = uuid4()

    def copy_module(self):
        """Keep initial entry absence distinct from behavioral test execution."""
        name = 'machine_health.services.integration_s6_copy'
        self.assertIsNotNone(find_spec(name), 'S6 needs its source-bound copy planner')
        return import_module(name)

    def source_arguments(self):
        """Keep exact actual native source coordinates, not caller actor objects."""
        return {
            'environment': 's6-test',
            'machine_pk': self.machine.pk,
            'source_pk': self.source.pk,
            'binding_pk': self.binding.pk,
            'window': self.window,
            'adapter': self.adapter,
        }

    def prepare(self, **fixture_changes):
        """Declare a real synthetic copy fixture separately from current source scope."""
        module = self.copy_module()
        source = delivery.prepare_history_delivery(**self.source_arguments())
        fields = {
            'copy_key': ' fixture-copy:7 ',
            'copy_kind': 'report',
            'source_digest': hashlib.sha256(source._payload).hexdigest(),
            'content': b'private-copy-fixture-body',
            'owners': (ClientSourceIdentity('s6-test', self.tenant.pk),),
            'attribution': 'source_claimed',
        }
        fields.update(fixture_changes)
        fixture = module.FixtureHistoryCopy(**fields)
        return module.prepare_history_copy(
            fixture_copy=fixture, **self.source_arguments()
        )

    def record_request(self, prepared, **changes):
        """Explicit server-owned request identity/revision, never a native delete."""
        fields = {
            'namespace_id': self.namespace.pk,
            'epoch': self.namespace.epoch,
            'request_key': 's6-fixture-copy-review',
            'request_id': self.request_id,
            'request_revision': 1,
        }
        fields.update(changes)
        return prepared.record_request(**fields)

    def test_native_source_reaches_private_retained_copy_without_deletion_authority(
        self,
    ):
        """Source-authorized private planning reaches actual persisted S7 carriers."""

        def operation():
            prepared = self.prepare()
            request = self.record_request(prepared)
            self.assertEqual(prepared.read_request(request), request)
            copy = prepared.record_copy(request)
            self.assertEqual(copy.impact.copy_key, ' fixture-copy:7 ')
            self.assertEqual(copy.copy_kind, 'report')
            self.assertEqual(copy.impact.retention, 'indefinite')
            assessment = prepared.assess(request)
            self.assertEqual(assessment.decisions[0].disposition, 'policy_retained')
            attempt = uuid4()
            progress = prepared.start(request, copy, attempt=attempt, max_attempts=2)
            self.assertEqual(progress.progress.status, 'retained')
            self.assertEqual(progress.progress.attempt_history, (attempt,))
            self.assertEqual(prepared.read_progress(request, copy), progress)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertEqual(PrivateCopyImpact.objects.count(), 1)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            self.assertNotIn(
                'private-copy-fixture-body',
                str(PrivateCopyImpact.objects.get().payload),
            )
            for value in (request, copy, assessment, progress):
                for name in (
                    'native_authorized',
                    'coverage_complete',
                    'deletion_applied',
                    'erasure_complete',
                ):
                    assert_literal_bool(self, getattr(value, name), False)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_copy_record_verification_does_not_resubmit_the_write(self):
        """Readback is not another submission even when the store supports replay."""
        from integration_state import shared_requests as store

        def operation():
            prepared = self.prepare()
            request = self.record_request(prepared)
            with patch.object(
                store, 'record_copy_impact', wraps=store.record_copy_impact
            ) as writer:
                impact = prepared.record_copy(request)
                writer.assert_called_once()
            self.assertEqual(PrivateCopyImpact.objects.get().pk, impact.impact_pk)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_reviewable_fixture_preserves_progress_replay_and_retry_budget(self):
        """Only synthetic metadata transitions; attempt history never resets."""

        def operation():
            module = self.copy_module()
            prepared = self.prepare(retention='reviewable')
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=2)
            proposed = replace(initial.progress, sequence=1, status='pending')
            pending = prepared.transition(request, impact, initial, proposed)
            self.assertEqual(pending.progress, proposed)
            self.assertEqual(
                prepared.transition(request, impact, initial, proposed), pending
            )
            self.assertEqual(
                prepared.transition(request, impact, pending, proposed), pending
            )
            uncertain = prepared.transition(
                request,
                impact,
                pending,
                replace(proposed, sequence=2, status='uncertain'),
            )
            retry = prepared.retry(request, impact, uncertain, attempt=uuid4())
            self.assertEqual(retry.progress.status, 'planned')
            self.assertEqual(retry.progress.attempts_used, 2)
            self.assertEqual(retry.progress.max_attempts, 2)
            next_pending = prepared.transition(
                request,
                impact,
                retry,
                replace(retry.progress, sequence=1, status='pending'),
            )
            failed = prepared.transition(
                request,
                impact,
                next_pending,
                replace(next_pending.progress, sequence=2, status='failed'),
            )
            with self.assertRaises(module.HistoryCopyError):
                prepared.retry(request, impact, failed, attempt=uuid4())
            self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
            self.assertEqual(prepared.read_progress(request, impact), failed)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_foreign_source_return_is_rejected_before_reading_properties(self):
        """A foreign producer-shaped object cannot supply authentication capability."""

        def operation():
            module = self.copy_module()
            genuine = self.prepare()
            reads = []

            class Foreign:
                @property
                def _payload(self):
                    reads.append(True)
                    return genuine._source._payload

                def _check(self):
                    return genuine._source._check()

            with patch.object(
                delivery, 'prepare_history_delivery', return_value=Foreign()
            ) as producer:
                with self.assertRaises(module.HistoryCopyUnavailableError):
                    module.prepare_history_copy(
                        fixture_copy=genuine._copy, **self.source_arguments()
                    )
                producer.assert_called_once()
            self.assertEqual(reads, [])
            self.assertEqual(
                self.record_request(genuine).intent.client.pk, self.tenant.pk
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_request_uuid_admission_precedes_reading_foreign_properties(self):
        """A UUID-shaped object cannot execute a property before validation."""

        def operation():
            prepared = self.prepare()
            reads = []
            request_id = self.request_id

            class Foreign:
                @property
                def hex(self):
                    reads.append(True)
                    return request_id.hex

            foreign = Foreign()
            for field in ('epoch', 'request_id'):
                with self.subTest(field=field):
                    reads.clear()
                    with self.assertRaises(ValueError):
                        self.record_request(prepared, **{field: foreign})
                    self.assertEqual(reads, [])
            self.assertEqual(PrivateDeletionRequest.objects.count(), 0)
            self.assertEqual(
                self.record_request(prepared).intent.request_id, self.request_id
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_schema_valid_stored_evidence_must_still_match_fixture_bytes(self):
        """A private checkpoint digest is not independent proof of copied bytes."""
        from integration_state.shared_events import fingerprint

        def operation():
            module = self.copy_module()
            prepared = self.prepare(retention='reviewable')
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
            pending = prepared.transition(
                request,
                impact,
                initial,
                replace(initial.progress, sequence=1, status='pending'),
            )
            expected = hashlib.sha256(b'private-copy-fixture-body').hexdigest()
            verified = prepared.transition(
                request,
                impact,
                pending,
                replace(
                    pending.progress,
                    sequence=2,
                    status='synthetic_verified',
                    evidence_digest=expected,
                ),
            )
            self.assertEqual(prepared.read_progress(request, impact), verified)
            row = PrivateCopyAttempt.objects.get(pk=verified.attempt_pk)
            original = row.checkpoint
            changed = dict(original, evidence_digest='f' * 64)
            self.assertNotEqual(expected, changed['evidence_digest'])
            try:
                PrivateCopyAttempt.objects.filter(pk=row.pk).update(
                    checkpoint=changed, checkpoint_digest=fingerprint(changed)
                )
                with self.assertRaises(module.HistoryCopyError):
                    prepared.read_progress(request, impact)
            finally:
                PrivateCopyAttempt.objects.filter(pk=row.pk).update(
                    checkpoint=original, checkpoint_digest=fingerprint(original)
                )
            self.assertEqual(prepared.read_progress(request, impact), verified)
            assert_literal_bool(self, verified.progress.synthetic_only, True)
            assert_literal_bool(self, verified.erasure_complete, False)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_independent_fixture_policies_never_become_native_copy_authority(self):
        """Unknown/shared/protected/empty claims are not historical copy inventory."""

        def operation():
            owner = ClientSourceIdentity('s6-test', self.tenant.pk)
            foreign = ClientSourceIdentity('s6-test', self.foreign_tenant.pk)
            cases = (
                ({'owners': None}, 'unresolved', 'uncertain'),
                ({'attribution': 'unknown'}, 'unresolved', 'uncertain'),
                ({'owners': (owner, foreign)}, 'shared_retained', 'retained'),
                ({'owners': (foreign,)}, 'out_of_scope', None),
                ({'retention': 'protected'}, 'policy_retained', 'retained'),
                ({'retention': 'unknown'}, 'unresolved', 'uncertain'),
                (
                    {'retention': 'reviewable', 'content': None},
                    'unresolved',
                    'uncertain',
                ),
                (
                    {'retention': 'reviewable', 'content': b''},
                    'review_required',
                    'planned',
                ),
                ({}, 'policy_retained', 'retained'),
            )
            for index, (changes, disposition, status) in enumerate(cases):
                with self.subTest(index=index, disposition=disposition):
                    prepared = self.prepare(
                        copy_key=f'fixture-policy:{index}', **changes
                    )
                    request = self.record_request(prepared)
                    impact = prepared.record_copy(request)
                    assessment = prepared.assess(request)
                    self.assertEqual(request.intent.client, owner)
                    self.assertEqual(assessment.decisions[0].disposition, disposition)
                    assert_literal_bool(self, assessment.coverage_complete, False)
                    assert_literal_bool(self, assessment.native_authorized, False)
                    assert_literal_bool(self, assessment.deletion_applied, False)
                    assert_literal_bool(self, assessment.erasure_complete, False)
                    if status is None:
                        with self.assertRaises(PermissionError):
                            prepared.start(
                                request, impact, attempt=uuid4(), max_attempts=1
                            )
                    else:
                        progress = prepared.start(
                            request, impact, attempt=uuid4(), max_attempts=1
                        )
                        self.assertEqual(progress.progress.status, status)
                        self.assertEqual(
                            progress.progress.copy_revision is None,
                            changes.get('content', b'known') is None,
                        )
                        assert_literal_bool(self, progress.progress.synthetic_only, True)
                        assert_literal_bool(self, progress.erasure_complete, False)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertEqual(PrivateCopyImpact.objects.count(), len(cases))
            self.assertEqual(PrivateCopyAttempt.objects.count(), len(cases) - 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_native_role_and_point_approval_are_independently_current(self):
        """Private persisted rows and an intact grant cannot replace native checks."""

        def operation():
            module = self.copy_module()
            prepared = self.prepare()
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            original = prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
            for model, row, denied, restored in (
                (type(self.rule), self.rule, {'can_view': False}, {'can_view': True}),
                (
                    type(self.point),
                    self.point,
                    {'status': 'pending'},
                    {'status': 'approved'},
                ),
            ):
                with self.subTest(model=model.__name__):
                    self.assertEqual(prepared.read_progress(request, impact), original)
                    model.objects.filter(pk=row.pk).update(**denied)
                    try:
                        self.assertTrue(
                            type(self.grant).objects.filter(pk=self.grant.pk).exists()
                        )
                        with self.assertRaises(module.HistoryCopyUnavailableError):
                            prepared.read_progress(request, impact)
                    finally:
                        model.objects.filter(pk=row.pk).update(**restored)
                    self.assertEqual(prepared.read_progress(request, impact), original)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_late_refusal_preserves_each_already_committed_private_phase(self):
        """A lost disclosure cannot erase request/copy/start/transition/retry commits."""
        from django.db import connection

        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare(retention='reviewable')
            activated = []

            def refuse_after_commit(name, action):
                writer = getattr(store, name)
                committed = []

                def held(*args, **kwargs):
                    value = writer(*args, **kwargs)
                    self.assertFalse(connection.in_atomic_block)
                    self.assertTrue(connection.get_autocommit())
                    committed.append(value)
                    activated.append(name)
                    type(self.rule).objects.filter(pk=self.rule.pk).update(
                        can_view=False
                    )
                    return value

                try:
                    with patch.object(store, name, side_effect=held) as observer:
                        with self.assertRaises(module.HistoryCopyUnavailableError):
                            action()
                        observer.assert_called_once()
                        self.assertFalse(
                            type(self.rule).objects.get(pk=self.rule.pk).can_view
                        )
                        self.assertTrue(
                            type(self.grant).objects.filter(pk=self.grant.pk).exists()
                        )
                finally:
                    type(self.rule).objects.filter(pk=self.rule.pk).update(
                        can_view=True
                    )
                self.assertEqual(len(committed), 1)
                return committed[0]

            request = refuse_after_commit(
                'record_private_intent', lambda: self.record_request(prepared)
            )
            self.assertEqual(prepared.read_request(request), request)
            self.assertEqual(self.record_request(prepared), request)
            impact = refuse_after_commit(
                'record_copy_impact', lambda: prepared.record_copy(request)
            )
            self.assertEqual(prepared.record_copy(request), impact)
            attempt = uuid4()
            initial = refuse_after_commit(
                'start_private_copy_attempt',
                lambda: prepared.start(
                    request, impact, attempt=attempt, max_attempts=2
                ),
            )
            self.assertEqual(prepared.read_progress(request, impact), initial)
            self.assertEqual(
                prepared.start(request, impact, attempt=attempt, max_attempts=2),
                initial,
            )
            proposed = replace(initial.progress, sequence=1, status='pending')
            pending = refuse_after_commit(
                'record_private_copy_progress',
                lambda: prepared.transition(request, impact, initial, proposed),
            )
            self.assertEqual(prepared.read_progress(request, impact), pending)
            self.assertEqual(
                prepared.transition(request, impact, initial, proposed), pending
            )
            uncertain = prepared.transition(
                request,
                impact,
                pending,
                replace(proposed, sequence=2, status='uncertain'),
            )
            next_attempt = uuid4()
            retried = refuse_after_commit(
                'retry_private_copy_attempt',
                lambda: prepared.retry(
                    request, impact, uncertain, attempt=next_attempt
                ),
            )
            self.assertEqual(prepared.read_progress(request, impact), retried)
            self.assertEqual(retried.progress.attempt_history, (attempt, next_attempt))
            self.assertEqual(retried.progress.max_attempts, 2)
            self.assertEqual(
                activated,
                [
                    'record_private_intent',
                    'record_copy_impact',
                    'start_private_copy_attempt',
                    'record_private_copy_progress',
                    'retry_private_copy_attempt',
                ],
            )
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertEqual(PrivateCopyImpact.objects.count(), 1)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_assessment_projection_and_its_late_native_authority_are_checked(self):
        """Schema-valid changed decisions and post-assessment revocation are refused."""
        from integration_state import s7_deletion_requests as peer

        def operation():
            module = self.copy_module()
            prepared = self.prepare()
            request = self.record_request(prepared)
            original = peer.assess_readiness

            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                return replace(
                    result,
                    decisions=(
                        replace(result.decisions[0], disposition='review_required'),
                    ),
                )

            with patch.object(
                peer, 'assess_readiness', side_effect=changed
            ) as observer:
                with self.assertRaises(module.HistoryCopyError):
                    prepared.assess(request)
                observer.assert_called_once()

            def revoked(*args, **kwargs):
                result = original(*args, **kwargs)
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                return result

            try:
                with patch.object(
                    peer, 'assess_readiness', side_effect=revoked
                ) as observer:
                    with self.assertRaises(module.HistoryCopyUnavailableError):
                        prepared.assess(request)
                    observer.assert_called_once()
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(
                prepared.assess(request).decisions[0].disposition, 'policy_retained'
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_changed_copy_claim_cannot_adopt_progress_at_same_content_revision(self):
        """Copy authority/retention is bound independently of the content digest."""
        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare(retention='reviewable')
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
            owner = ClientSourceIdentity('s6-test', self.tenant.pk)
            foreign = ClientSourceIdentity('s6-test', self.foreign_tenant.pk)
            for fields in (
                {'retention': 'protected'},
                {'owners': (owner, foreign)},
                {'attribution': 'unknown'},
            ):
                with self.subTest(fields=tuple(fields)):
                    arguments = {'retention': 'reviewable', **fields}
                    changed = self.prepare(**arguments)
                    self.assertEqual(changed._copy.content, prepared._copy.content)
                    self.assertEqual(changed._coordinates, prepared._coordinates)
                    with patch.object(
                        store,
                        'read_private_copy_progress',
                        wraps=store.read_private_copy_progress,
                    ) as reader:
                        with self.assertRaises(module.HistoryCopyError):
                            changed.read_progress(request, impact)
                        reader.assert_not_called()
            self.assertEqual(prepared.read_progress(request, impact), initial)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_fresh_invocation_recovers_only_its_exact_original_source_window(self):
        """Private rows survive an invocation; its prior capability does not."""

        def first():
            prepared = self.prepare()
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
            return prepared, request, impact, initial

        old, request, impact, initial = self.run_boundary(first, method='session')
        module = self.copy_module()
        with self.assertRaises(module.HistoryCopyUnavailableError):
            old.read_progress(request, impact)

        def next_invocation():
            with self.assertRaises(module.HistoryCopyUnavailableError):
                old.read_progress(request, impact)
            current = self.prepare()
            self.assertEqual(current.read_progress(request, impact), initial)
            self.assertEqual(
                request.intent.scope[1].source_id,
                hashlib.sha256(current._source._payload).hexdigest(),
            )
            window = self.window
            self.window = type(window)(-1, 3)
            try:
                changed = self.prepare()
                self.assertEqual(changed._coordinates[:4], current._coordinates[:4])
                self.assertNotEqual(changed._coordinates[-1], current._coordinates[-1])
                with self.assertRaises(module.HistoryCopyError):
                    changed.read_request(request)
            finally:
                self.window = window
            self.assertEqual(current.read_progress(request, impact), initial)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(next_invocation, method='session'), True)

    def test_ordinary_faults_are_content_free_and_process_control_keeps_identity(self):
        """No false empty success, hidden commit or replaced process-control fault."""
        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare()
            sentinel = 'private-copy-fixture-body'
            with patch.object(
                store, 'record_private_intent', side_effect=RuntimeError(sentinel)
            ) as writer:
                with self.assertRaises(module.HistoryCopyError) as raised:
                    self.record_request(prepared)
                writer.assert_called_once()
            self.assertNotIn(sentinel, str(raised.exception))
            for fault in (
                MemoryError(sentinel),
                KeyboardInterrupt(sentinel),
                SystemExit(sentinel),
            ):
                with self.subTest(fault=type(fault).__name__):
                    with patch.object(
                        store, 'record_private_intent', side_effect=fault
                    ) as writer:
                        with self.assertRaises(type(fault)) as raised:
                            self.record_request(prepared)
                        self.assertIs(raised.exception, fault)
                        writer.assert_called_once()

            def revoked(*args, **kwargs):
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                raise RuntimeError(sentinel)

            try:
                with patch.object(
                    store, 'record_private_intent', side_effect=revoked
                ) as writer:
                    with self.assertRaises(
                        module.HistoryCopyUnavailableError
                    ) as raised:
                        self.record_request(prepared)
                    writer.assert_called_once()
                    self.assertNotIn(sentinel, str(raised.exception))
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 0)
            self.assertEqual(
                self.record_request(prepared).intent.request_id, self.request_id
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_schema_valid_return_forgery_cannot_undo_a_committed_attempt(self):
        """An authentic stored row and a valid but different returned version disagree."""
        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare()
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            original = store.start_private_copy_attempt
            committed = []

            def forged(*args, **kwargs):
                value = original(*args, **kwargs)
                committed.append(value)
                return replace(value, version=value.version + 1)

            with patch.object(
                store, 'start_private_copy_attempt', side_effect=forged
            ) as observer:
                with self.assertRaises(module.HistoryCopyError):
                    prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
                observer.assert_called_once()
            self.assertEqual(len(committed), 1)
            self.assertEqual(prepared.read_progress(request, impact), committed[0])
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            self.assertEqual(
                PrivateCopyAttempt.objects.get().version, committed[0].version
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_copy_readback_does_not_open_private_state_after_late_revocation(self):
        """Withholding the result alone does not authorize a new private read."""
        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare()
            request = self.record_request(prepared)
            writer, reader = store.record_copy_impact, store._open_impact
            committed, late_reads = [], []

            def revoke(*args, **kwargs):
                result = writer(*args, **kwargs)
                committed.append(result)
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                return result

            def observe(*args, **kwargs):
                if (
                    committed
                    and not type(self.rule).objects.get(pk=self.rule.pk).can_view
                ):
                    late_reads.append(True)
                return reader(*args, **kwargs)

            try:
                with (
                    patch.object(
                        store, 'record_copy_impact', side_effect=revoke
                    ) as write_observer,
                    patch.object(store, '_open_impact', side_effect=observe),
                ):
                    with self.assertRaises(module.HistoryCopyUnavailableError):
                        prepared.record_copy(request)
                    write_observer.assert_called_once()
                    self.assertEqual(len(committed), 1)
                    self.assertEqual(late_reads, [])
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(PrivateCopyImpact.objects.count(), 1)
            self.assertEqual(prepared.record_copy(request), committed[0])
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)
