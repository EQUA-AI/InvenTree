"""Actual S3 authority to private S5/S7 synthetic retained-copy bookkeeping.

Historical positives explicitly simulate attribution. Current sessions, roles,
grants, source ORM, original synthetic files and private state owners are real.
"""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
from uuid import uuid4

from django.test import TransactionTestCase

from assets.integration_contracts import ClientSourceIdentity
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from integration_state.models import (
    PrivateCopyAttempt,
    PrivateCopyImpact,
    PrivateDeletionRequest,
)
from tasks.tests import test_integration_s3_delivery_db as support


class SourceCopyFixture(support.SourceDeliveryFixture):
    """Reuse real native authority without importing another TestCase binding."""

    def setUp(self):
        """The inherited fixture owns exactly one private synthetic namespace."""
        super().setUp()
        self.request_id = uuid4()

    def copy_module(self):
        """A missing bridge is an entry RED, not a reached downstream oracle."""
        name = 'tasks.integration_s3_copy'
        self.assertIsNotNone(
            find_spec(name), 'S3 needs a bounded synthetic copy bridge'
        )
        return import_module(name)

    def source_arguments(self, **changes):
        """Native source coordinates only; no caller actor or permission callback."""
        result = {
            'environment': 's3-test',
            'operation': 'work.core',
            'source_pk': self.work.pk,
        }
        result.update(changes)
        if result['operation'] in ('work_document.content', 'machine_document.content'):
            result.setdefault('max_bytes', 128)
        return result

    def prepare_copy(self, *, source_changes=None, fixture_client=None, **changes):
        """Label the planning client separately from unknown historical clients."""
        module = self.copy_module()
        arguments = self.source_arguments(**(source_changes or {}))
        source = self.prepare(**arguments)
        client = ClientSourceIdentity('s3-test', self.tenant.pk)
        values = {
            'copy_key': ' s3-copy:007 ',
            'copy_kind': 'report',
            'source_digest': hashlib.sha256(source._payload).hexdigest(),
            'content': b's3-independent-copy-fixture',
            'owners': (client,),
            'attribution': 'source_claimed',
        }
        values.update(changes)
        fixture = module.FixtureSourceCopy(**values)
        return module.prepare_source_copy(
            fixture_copy=fixture,
            fixture_client=client if fixture_client is None else fixture_client,
            **arguments,
        )

    def record_request(self, prepared, **changes):
        """Server-selected private identities, not a native deletion request."""
        arguments = {
            'namespace_id': self.namespace.pk,
            'epoch': self.namespace.epoch,
            'request_key': ' s3-fixture-retention-review ',
            'request_id': self.request_id,
            'request_revision': 1,
        }
        arguments.update(changes)
        return prepared.record_request(**arguments)


@assume_synthetic_attribution
class SimulatedSourceCopyTests(SourceCopyFixture, TransactionTestCase):
    """Only historical attribution is simulated; no real-copy authority exists."""

    def test_actual_source_retained_copy_uses_s5_without_native_authority(self):
        """An authorized synthetic source reaches the actual existing state owners."""

        def operation():
            prepared = self.prepare_copy()
            request = self.record_request(prepared)
            self.assertEqual(prepared.read_request(request), request)
            self.assertEqual(
                request.intent.request_key, ' s3-fixture-retention-review '
            )
            self.assertEqual(request.intent.scope[0].source_id, self.work.pk)
            self.assertEqual(request.intent.scope[0].entity_kind, 'work')
            self.assertEqual(request.intent.scope[0].aspect, 'work.core')
            self.assertEqual(request.intent.scope[1].entity_kind, 's3_fixture_wire')
            self.assertEqual(
                request.intent.scope[1].source_id, prepared._copy.source_digest
            )
            impact = prepared.record_copy(request)
            self.assertEqual(impact.impact.copy_key, ' s3-copy:007 ')
            self.assertEqual(impact.impact.retention, 'indefinite')
            self.assertEqual(
                prepared.assess(request).decisions[0].disposition, 'policy_retained'
            )
            attempt = uuid4()
            progress = prepared.start(request, impact, attempt=attempt, max_attempts=2)
            self.assertEqual(progress.progress.status, 'retained')
            self.assertEqual(progress.progress.attempt_history, (attempt,))
            self.assertEqual(prepared.read_progress(request, impact), progress)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertEqual(PrivateCopyImpact.objects.count(), 1)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            self.assertNotIn(
                's3-independent-copy-fixture',
                str(PrivateCopyImpact.objects.get().payload),
            )
            observation = self.delivery().metadata_from_source_payload(
                prepared._source._payload
            )
            self.assertIsNone(observation.client_ids)
            self.assertIsNone(observation.source_revision)
            for value in (request, impact, progress, prepared.assess(request)):
                for flag in (
                    'native_authorized',
                    'coverage_complete',
                    'deletion_applied',
                    'erasure_complete',
                ):
                    assert_literal_bool(self, getattr(value, flag), False)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_actual_owner_progress_preserves_cas_and_literal_replay(self):
        """One actual owner transition replays without advancing a second version."""
        from integration_state.shared_events import SharedConflictError

        def operation():
            prepared = self.prepare_copy(retention='reviewable')
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=2)
            self.assertEqual(initial.progress.status, 'planned')
            proposed = replace(initial.progress, sequence=1, status='pending')
            pending = prepared.transition(request, impact, initial, proposed)
            self.assertEqual(pending.version, initial.version + 1)
            self.assertEqual(pending.progress, proposed)
            self.assertEqual(prepared.read_progress(request, impact), pending)
            self.assertEqual(
                prepared.transition(request, impact, initial, proposed), pending
            )
            with self.assertRaises(SharedConflictError):
                prepared.transition(
                    request, impact, initial, replace(proposed, status='uncertain')
                )
            self.assertEqual(prepared.read_progress(request, impact), pending)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_verified_evidence_is_bound_to_original_independent_copy_bytes(self):
        """A schema-valid digest is neither the copy's truth nor its source hash."""
        from unittest.mock import patch

        from integration_state import shared_requests as store
        from integration_state.shared_events import fingerprint

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy(retention='reviewable')
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=2)
            pending = prepared.transition(
                request,
                impact,
                initial,
                replace(initial.progress, sequence=1, status='pending'),
            )
            expected = hashlib.sha256(b's3-independent-copy-fixture').hexdigest()
            self.assertNotEqual(expected, prepared._copy.source_digest)
            invalid = replace(
                pending.progress,
                sequence=2,
                status='synthetic_verified',
                evidence_digest='f' * 64,
            )
            with patch.object(
                store,
                'record_private_copy_progress',
                wraps=store.record_private_copy_progress,
            ) as written:
                with self.assertRaises(module.SourceCopyError):
                    prepared.transition(request, impact, pending, invalid)
                written.assert_not_called()
            self.assertEqual(prepared.read_progress(request, impact), pending)
            verified = prepared.transition(
                request, impact, pending, replace(invalid, evidence_digest=expected)
            )
            self.assertEqual(verified.progress.evidence_digest, expected)
            row = PrivateCopyAttempt.objects.get(pk=verified.attempt_pk)
            original = row.checkpoint
            changed = dict(original, evidence_digest='f' * 64)
            try:
                PrivateCopyAttempt.objects.filter(pk=row.pk).update(
                    checkpoint=changed, checkpoint_digest=fingerprint(changed)
                )
                with self.assertRaises(module.SourceCopyError):
                    prepared.read_progress(request, impact)
            finally:
                PrivateCopyAttempt.objects.filter(pk=row.pk).update(
                    checkpoint=original, checkpoint_digest=fingerprint(original)
                )
            self.assertEqual(prepared.read_progress(request, impact), verified)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_all_six_actual_sources_keep_typed_scope_and_unknown_source_clients(self):
        """Native coordinates and whole-wire bytes never become copy/client truth."""
        self.prepare_operations()
        cases = (
            ('work.core', self.work.pk, None, 'work'),
            ('maintenance.direct', self.maintenance.pk, None, 'maintenance'),
            ('work_document.metadata', self.attachment.pk, 'workorder', 'attachment'),
            (
                'machine_document.metadata',
                self.attachment.pk,
                'assetmachine',
                'attachment',
            ),
            ('work_document.content', self.attachment.pk, 'workorder', 'attachment'),
            (
                'machine_document.content',
                self.attachment.pk,
                'assetmachine',
                'attachment',
            ),
        )
        for name, identity, owner, kind in cases:
            with self.subTest(operation=name):
                self.select_owner(owner)

                def operation(name=name, identity=identity, kind=kind):
                    prepared = self.prepare_copy(
                        source_changes={'operation': name, 'source_pk': identity},
                        copy_key='copy:' + name,
                    )
                    request = self.record_request(
                        prepared, request_key='request:' + name, request_id=uuid4()
                    )
                    source, wire = request.intent.scope
                    self.assertIs(type(source.source_id), int)
                    self.assertEqual(
                        (source.entity_kind, source.aspect, source.source_id),
                        (kind, name, identity),
                    )
                    self.assertEqual(wire.entity_kind, 's3_fixture_wire')
                    self.assertEqual(
                        wire.source_id,
                        hashlib.sha256(prepared._source._payload).hexdigest(),
                    )
                    observed = self.delivery().metadata_from_source_payload(
                        prepared._source._payload
                    )
                    self.assertIsNone(observed.client_ids)
                    self.assertIsNone(observed.source_revision)
                    impact = prepared.record_copy(request)
                    self.assertEqual(
                        impact.impact.copy_revision,
                        'fixture-sha256:'
                        + hashlib.sha256(b's3-independent-copy-fixture').hexdigest(),
                    )
                    self.assertNotEqual(wire.source_id, impact.impact.copy_revision)
                    assert_literal_bool(self, impact.native_authorized, False)
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)
        self.assertEqual(PrivateDeletionRequest.objects.count(), len(cases))
        self.assertEqual(PrivateCopyImpact.objects.count(), len(cases))

    def test_independent_planning_client_needs_a_current_grant_not_source_attribution(
        self,
    ):
        """An explicit granted fixture client stays independent of unknown history."""
        from assets.models import Client, ClientScopeGrant

        second = Client.objects.create(
            name='Synthetic independent planning', code='s3-copy-independent'
        )

        def operation():
            module = self.copy_module()
            client = ClientSourceIdentity('s3-test', second.pk)
            self.assertEqual(self.prepare()._policy[2], (self.tenant.pk,))
            with self.assertRaises(module.SourceCopyError):
                self.prepare_copy(fixture_client=client)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 0)
            grant = ClientScopeGrant.objects.create(user=self.actor, client=second)
            try:
                prepared = self.prepare_copy(fixture_client=client)
                request = self.record_request(prepared)
                self.assertEqual(request.intent.client, client)
                self.assertIn('s3-synthetic-planning-user:', request.intent.actor_key)
                self.assertIsNone(
                    self
                    .delivery()
                    .metadata_from_source_payload(prepared._source._payload)
                    .client_ids
                )
                self.assertEqual(
                    prepared.assess(request).decisions[0].disposition, 'out_of_scope'
                )
            finally:
                grant.delete()
            with self.assertRaises(module.SourceCopyUnavailableError):
                prepared.read_request(request)
            self.assertTrue(type(self.grant).objects.filter(pk=self.grant.pk).exists())
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_independent_unknown_shared_protected_and_empty_copy_claims_remain_non_actionable(
        self,
    ):
        """Source reads cannot complete copy coverage or override retained policy."""
        from assets.models import Client

        foreign = Client.objects.create(
            name='Synthetic other owner', code='s3-copy-other'
        )

        def operation():
            owner = ClientSourceIdentity('s3-test', self.tenant.pk)
            other = ClientSourceIdentity('s3-test', foreign.pk)
            cases = (
                ({'owners': None}, 'unresolved', 'uncertain'),
                ({'attribution': 'unknown'}, 'unresolved', 'uncertain'),
                ({'owners': (owner, other)}, 'shared_retained', 'retained'),
                ({'owners': (other,)}, 'out_of_scope', None),
                ({'owners': ()}, 'out_of_scope', None),
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
                with self.subTest(index=index):
                    prepared = self.prepare_copy(copy_key=f'policy:{index}', **changes)
                    request = self.record_request(prepared)
                    impact = prepared.record_copy(request)
                    assessed = prepared.assess(request)
                    self.assertEqual(assessed.decisions[0].disposition, disposition)
                    for flag in (
                        'native_authorized',
                        'coverage_complete',
                        'deletion_applied',
                        'erasure_complete',
                    ):
                        assert_literal_bool(self, getattr(assessed, flag), False)
                    if status is None:
                        with self.assertRaises(PermissionError):
                            prepared.start(
                                request, impact, attempt=uuid4(), max_attempts=1
                            )
                    else:
                        started = prepared.start(
                            request, impact, attempt=uuid4(), max_attempts=1
                        )
                        self.assertEqual(started.progress.status, status)
                        self.assertEqual(
                            started.progress.copy_revision is None,
                            changes.get('content', b'known') is None,
                        )
                        assert_literal_bool(self, started.progress.synthetic_only, True)
                        if status != 'planned':
                            with self.assertRaises(ValueError):
                                prepared.transition(
                                    request,
                                    impact,
                                    started,
                                    replace(
                                        started.progress, sequence=1, status='pending'
                                    ),
                                )
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertEqual(PrivateCopyImpact.objects.count(), len(cases))
            self.assertEqual(PrivateCopyAttempt.objects.count(), len(cases) - 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_role_and_grant_revocation_each_withhold_existing_private_progress(
        self,
    ):
        """Retained private rows are neither native role nor current client authority."""
        from assets.models import ClientScopeGrant

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy()
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            original = prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
            type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
            try:
                self.assertTrue(
                    ClientScopeGrant.objects.filter(pk=self.grant.pk).exists()
                )
                with self.assertRaises(module.SourceCopyUnavailableError):
                    prepared.read_progress(request, impact)
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(prepared.read_progress(request, impact), original)
            self.grant.delete()
            try:
                self.assertTrue(type(self.rule).objects.get(pk=self.rule.pk).can_view)
                with self.assertRaises(module.SourceCopyUnavailableError):
                    prepared.read_progress(request, impact)
            finally:
                self.grant = ClientScopeGrant.objects.create(
                    user=self.actor, client=self.tenant
                )
            self.assertEqual(prepared.read_progress(request, impact), original)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_changed_copy_ownership_retention_and_source_cannot_adopt_old_rows(self):
        """An unchanged copy revision does not bind independent copy-policy claims."""
        from integration_state.shared_events import SharedConflictError
        from tasks.models import WorkOrder

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy()
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            original = prepared.start(request, impact, attempt=uuid4(), max_attempts=1)
            self.assertEqual(self.record_request(prepared), request)
            self.assertEqual(prepared.record_copy(request), impact)
            for changes in ({'owners': None}, {'retention': 'reviewable'}):
                with self.subTest(fields=tuple(changes)):
                    changed = self.prepare_copy(**changes)
                    self.assertEqual(
                        changed._impact(request).copy_revision,
                        impact.impact.copy_revision,
                    )
                    with self.assertRaises(SharedConflictError):
                        changed.record_copy(request)
                    with self.assertRaises(module.SourceCopyError):
                        changed.read_progress(request, impact)
            for changes in (
                {'request_revision': 2},
                {'request_key': 's3-fixture-retention-review'},
            ):
                with self.subTest(fields=tuple(changes)), self.assertRaises(ValueError):
                    self.record_request(prepared, **changes)
            before = self.work.description
            WorkOrder.objects.filter(pk=self.work.pk).update(
                description='Synthetic changed source'
            )
            try:
                with self.assertRaises(module.SourceCopyUnavailableError):
                    prepared.read_progress(request, impact)
                changed = self.prepare_copy()
                with self.assertRaises(SharedConflictError):
                    self.record_request(changed)
            finally:
                WorkOrder.objects.filter(pk=self.work.pk).update(description=before)
            self.assertEqual(prepared.read_progress(request, impact), original)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            self.assertEqual(PrivateCopyImpact.objects.count(), 1)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_caller_transactions_refuse_before_source_io_or_any_private_owner_call(
        self,
    ):
        """Neither atomic nesting nor disabled autocommit may enclose copy planning."""
        from contextlib import ExitStack
        from unittest.mock import patch

        from django.core.files.storage import default_storage
        from django.db import connection, transaction

        from integration_state import shared_requests as store

        self.prepare_operations()
        self.select_owner('workorder')

        def operation():
            module = self.copy_module()
            arguments = {
                'operation': 'work_document.content',
                'source_pk': self.attachment.pk,
            }
            prepared = self.prepare_copy(
                source_changes=arguments, retention='reviewable'
            )
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            initial = prepared.start(request, impact, attempt=uuid4(), max_attempts=2)
            uncertain = prepared.transition(
                request,
                impact,
                initial,
                replace(initial.progress, sequence=1, status='uncertain'),
            )
            actions = (
                lambda: module.prepare_source_copy(
                    fixture_copy=prepared._copy,
                    fixture_client=prepared._client,
                    **self.source_arguments(**arguments),
                ),
                lambda: self.record_request(prepared),
                lambda: prepared.read_request(request),
                lambda: prepared.record_copy(request),
                lambda: prepared.assess(request),
                lambda: prepared.start(
                    request, impact, attempt=uuid4(), max_attempts=2
                ),
                lambda: prepared.read_progress(request, impact),
                lambda: prepared.transition(
                    request, impact, uncertain, uncertain.progress
                ),
                lambda: prepared.retry(request, impact, uncertain, attempt=uuid4()),
            )

            def refused():
                with ExitStack() as stack:
                    opened = stack.enter_context(
                        patch.object(
                            default_storage, 'open', wraps=default_storage.open
                        )
                    )
                    owners = [
                        stack.enter_context(
                            patch.object(store, name, wraps=getattr(store, name))
                        )
                        for name in (
                            'record_private_intent',
                            'read_private_intent',
                            'record_copy_impact',
                            'start_private_copy_attempt',
                            'read_private_copy_progress',
                            'record_private_copy_progress',
                            'retry_private_copy_attempt',
                        )
                    ]
                    for index, action in enumerate(actions):
                        with (
                            self.subTest(action=index),
                            self.assertRaises(module.SourceCopyError),
                        ):
                            action()
                    opened.assert_not_called()
                    for owner in owners:
                        owner.assert_not_called()

            with transaction.atomic():
                refused()
            connection.set_autocommit(False)
            try:
                refused()
            finally:
                connection.rollback()
                connection.set_autocommit(True)
            self.assertEqual(prepared.read_progress(request, impact), uncertain)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_actual_work_and_machine_file_reads_stay_outside_private_metadata_transactions(
        self,
    ):
        """Real delegated opens cover all public copy phases before/after owner commits."""
        from unittest.mock import patch

        from django.core.files.storage import default_storage
        from django.db import connection

        from integration_state import shared_requests as store

        self.prepare_operations()
        for owner, name in (
            ('workorder', 'work_document.content'),
            ('assetmachine', 'machine_document.content'),
        ):
            with self.subTest(operation=name):
                self.select_owner(owner)

                def operation(name=name):
                    phase, opens, committed = ['prepare'], [], []
                    native_open, writer = (
                        default_storage.open,
                        store.record_private_intent,
                    )

                    def opened(*args, **kwargs):
                        opens.append((
                            phase[0],
                            bool(committed),
                            connection.in_atomic_block,
                        ))
                        return native_open(*args, **kwargs)

                    def written(*args, **kwargs):
                        value = writer(*args, **kwargs)
                        self.assertFalse(connection.in_atomic_block)
                        committed.append(value.request_pk)
                        return value

                    with (
                        patch.object(default_storage, 'open', side_effect=opened),
                        patch.object(
                            store, 'record_private_intent', side_effect=written
                        ),
                    ):
                        prepared = self.prepare_copy(
                            source_changes={
                                'operation': name,
                                'source_pk': self.attachment.pk,
                            },
                            retention='reviewable',
                        )
                        phase[0] = 'request'
                        request = self.record_request(
                            prepared, request_key=name, request_id=uuid4()
                        )
                        phase[0] = 'request-read'
                        self.assertEqual(prepared.read_request(request), request)
                        phase[0] = 'copy'
                        impact = prepared.record_copy(request)
                        phase[0] = 'assessment'
                        self.assertEqual(
                            prepared.assess(request).state, 'needs_native_review'
                        )
                        phase[0] = 'start'
                        initial = prepared.start(
                            request, impact, attempt=uuid4(), max_attempts=2
                        )
                        phase[0] = 'transition'
                        uncertain = prepared.transition(
                            request,
                            impact,
                            initial,
                            replace(initial.progress, sequence=1, status='uncertain'),
                        )
                        phase[0] = 'retry'
                        retried = prepared.retry(
                            request, impact, uncertain, attempt=uuid4()
                        )
                        phase[0] = 'progress-read'
                        self.assertEqual(
                            prepared.read_progress(request, impact), retried
                        )
                    self.assertEqual(
                        {value[0] for value in opens},
                        {
                            'prepare',
                            'request',
                            'request-read',
                            'copy',
                            'assessment',
                            'start',
                            'transition',
                            'retry',
                            'progress-read',
                        },
                    )
                    self.assertTrue(any(after for _, after, _ in opens))
                    self.assertFalse(any(active for _, _, active in opens), repr(opens))
                    self.assertEqual(len(committed), 1)
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_late_native_refusal_preserves_all_five_actual_owner_commit_phases(self):
        """Lost disclosure never rolls back or resubmits a committed private phase."""
        from unittest.mock import patch

        from django.db import connection

        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy(retention='reviewable')
            activated = []

            def refuse(name, action):
                writer, committed = getattr(store, name), []

                def written(*args, **kwargs):
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
                    with patch.object(store, name, side_effect=written) as called:
                        with self.assertRaises(module.SourceCopyUnavailableError):
                            action()
                        called.assert_called_once()
                finally:
                    type(self.rule).objects.filter(pk=self.rule.pk).update(
                        can_view=True
                    )
                self.assertEqual(len(committed), 1)
                return committed[0]

            request = refuse(
                'record_private_intent', lambda: self.record_request(prepared)
            )
            self.assertEqual(prepared.read_request(request), request)
            self.assertEqual(self.record_request(prepared), request)
            impact = refuse('record_copy_impact', lambda: prepared.record_copy(request))
            self.assertEqual(prepared.record_copy(request), impact)
            attempt = uuid4()
            initial = refuse(
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
            pending = refuse(
                'record_private_copy_progress',
                lambda: prepared.transition(request, impact, initial, proposed),
            )
            self.assertEqual(
                prepared.transition(request, impact, initial, proposed), pending
            )
            uncertain = prepared.transition(
                request,
                impact,
                pending,
                replace(pending.progress, sequence=2, status='uncertain'),
            )
            next_attempt = uuid4()
            retried = refuse(
                'retry_private_copy_attempt',
                lambda: prepared.retry(
                    request, impact, uncertain, attempt=next_attempt
                ),
            )
            self.assertEqual(prepared.read_progress(request, impact), retried)
            self.assertEqual(retried.progress.attempt_history, (attempt, next_attempt))
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

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_postcommit_same_reference_byte_change_preserves_request_before_readback(
        self,
    ):
        """Actual original-byte mutation after commit withholds stale source disclosure."""
        from unittest.mock import patch

        from django.db import connection

        from integration_state import shared_requests as store

        self.prepare_operations()
        self.select_owner('workorder')

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy(
                source_changes={
                    'operation': 'work_document.content',
                    'source_pk': self.attachment.pk,
                }
            )
            writer, committed = store.record_private_intent, []

            def written(*args, **kwargs):
                result = writer(*args, **kwargs)
                self.assertFalse(connection.in_atomic_block)
                committed.append(result)
                with self.attachment.attachment.storage.open(
                    self.attachment.attachment.name, 'wb'
                ) as handle:
                    handle.write(b'X' * len(self.blob))
                return result

            try:
                with (
                    patch.object(store, 'record_private_intent', side_effect=written),
                    patch.object(
                        store, 'read_private_intent', wraps=store.read_private_intent
                    ) as read,
                ):
                    with self.assertRaises(module.SourceCopyUnavailableError):
                        self.record_request(prepared)
                    read.assert_not_called()
                self.assertEqual(len(committed), 1)
                self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            finally:
                with self.attachment.attachment.storage.open(
                    self.attachment.attachment.name, 'wb'
                ) as handle:
                    handle.write(self.blob)
            self.assertEqual(prepared.read_request(committed[0]), committed[0])
            self.assertEqual(self.record_request(prepared), committed[0])
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_fallible_result_validation_loss_stops_private_owner_readback(self):
        """A committed result is retained but not reread after role/source loss."""
        from unittest.mock import patch

        from integration_state import shared_requests as store
        from tasks.models import WorkOrder

        def operation():
            module = self.copy_module()
            for name in ('role', 'source'):
                with self.subTest(boundary=name):
                    prepared = self.prepare_copy()
                    original, activated, committed = module._result_key, [], []
                    arguments = {
                        'request_key': 'late-validation:' + name,
                        'request_id': uuid4(),
                    }

                    def validated(
                        value,
                        *,
                        name=name,
                        original=original,
                        activated=activated,
                        committed=committed,
                    ):
                        key = original(value)
                        if not activated:
                            committed.append(value)
                            activated.append(name)
                            if name == 'role':
                                type(self.rule).objects.filter(pk=self.rule.pk).update(
                                    can_view=False
                                )
                            else:
                                WorkOrder.objects.filter(pk=self.work.pk).update(
                                    description='Synthetic changed validation source'
                                )
                        return key

                    try:
                        with (
                            patch.object(module, '_result_key', side_effect=validated),
                            patch.object(
                                store,
                                'read_private_intent',
                                wraps=store.read_private_intent,
                            ) as read,
                        ):
                            with self.assertRaises(module.SourceCopyUnavailableError):
                                self.record_request(prepared, **arguments)
                            read.assert_not_called()
                        self.assertEqual(activated, [name])
                        self.assertEqual(len(committed), 1)
                    finally:
                        type(self.rule).objects.filter(pk=self.rule.pk).update(
                            can_view=True
                        )
                        WorkOrder.objects.filter(pk=self.work.pk).update(
                            description=self.work.description
                        )
                    self.assertEqual(prepared.read_request(committed[0]), committed[0])
                    self.assertEqual(
                        self.record_request(prepared, **arguments), committed[0]
                    )
            self.assertEqual(PrivateDeletionRequest.objects.count(), 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_owner_readback_never_reissues_a_write_to_verify_it(self):
        """Every actual mutation owner is delegated once; verification is read-only."""
        from contextlib import ExitStack
        from unittest.mock import patch

        from integration_state import shared_requests as store

        def operation():
            prepared = self.prepare_copy(retention='reviewable')
            names = (
                'record_private_intent',
                'record_copy_impact',
                'start_private_copy_attempt',
                'record_private_copy_progress',
                'retry_private_copy_attempt',
            )
            with ExitStack() as stack:
                owners = [
                    stack.enter_context(
                        patch.object(store, name, wraps=getattr(store, name))
                    )
                    for name in names
                ]
                request = self.record_request(prepared)
                impact = prepared.record_copy(request)
                initial = prepared.start(
                    request, impact, attempt=uuid4(), max_attempts=2
                )
                uncertain = prepared.transition(
                    request,
                    impact,
                    initial,
                    replace(initial.progress, sequence=1, status='uncertain'),
                )
                retried = prepared.retry(request, impact, uncertain, attempt=uuid4())
                self.assertEqual(prepared.read_progress(request, impact), retried)
                for owner in owners:
                    owner.assert_called_once()
            self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_ordinary_store_faults_are_opaque_and_process_controls_keep_identity(self):
        """Current authorized faults stay faults; late native refusal withholds them."""
        from unittest.mock import patch

        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy()
            sentinel = 'private-s3-copy-backend-sentinel'
            with patch.object(
                store, 'record_private_intent', side_effect=RuntimeError(sentinel)
            ):
                with self.assertRaises(module.SourceCopyError) as raised:
                    self.record_request(prepared)
                self.assertNotIn(sentinel, str(raised.exception))
            for fault in (MemoryError(sentinel), KeyboardInterrupt(), SystemExit(73)):
                with self.subTest(kind=type(fault).__name__):
                    with patch.object(
                        store, 'record_private_intent', side_effect=fault
                    ):
                        with self.assertRaises(type(fault)) as raised:
                            self.record_request(prepared)
                        self.assertIs(raised.exception, fault)

            def revoked(*args, **kwargs):
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                raise RuntimeError(sentinel)

            try:
                with patch.object(store, 'record_private_intent', side_effect=revoked):
                    with self.assertRaises(module.SourceCopyUnavailableError) as raised:
                        self.record_request(prepared)
                    self.assertNotIn(sentinel, str(raised.exception))
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(PrivateDeletionRequest.objects.count(), 0)
            request = self.record_request(prepared)
            self.assertEqual(prepared.read_request(request), request)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_foreign_uuid_and_client_carriers_and_expired_invocation_cannot_dispatch(
        self,
    ):
        """Exact carrier admission precedes properties and private mutation calls."""
        from unittest.mock import patch

        from integration_state import shared_requests as store

        reads, held = [], []

        class Foreign:
            @property
            def hex(self):
                reads.append('hex')
                return uuid4().hex

            @property
            def environment(self):
                reads.append('environment')
                return 's3-test'

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy()
            request = self.record_request(prepared)
            with patch.object(
                store, 'record_private_intent', wraps=store.record_private_intent
            ) as written:
                for field in ('epoch', 'request_id'):
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        self.record_request(prepared, **{field: Foreign()})
                with self.assertRaises(module.SourceCopyError):
                    module.prepare_source_copy(
                        fixture_copy=prepared._copy,
                        fixture_client=Foreign(),
                        **self.source_arguments(),
                    )
                written.assert_not_called()
            self.assertEqual(reads, [])
            held.extend((prepared, request))
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
        with patch.object(
            store, 'read_private_intent', wraps=store.read_private_intent
        ) as read:
            with self.assertRaises(self.copy_module().SourceCopyUnavailableError):
                held[0].read_request(held[1])
            read.assert_not_called()
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)

    def test_constructor_and_assessment_rebinding_cannot_replace_validated_values(self):
        """Schema validity alone cannot bind a returned intent or planning result."""
        from unittest.mock import patch

        from integration_state import s7_deletion_requests as peer
        from integration_state import shared_requests as store

        def operation():
            module = self.copy_module()
            prepared = self.prepare_copy()
            native_intent = peer.DeletionIntent

            def changed(**kwargs):
                return replace(
                    native_intent(**kwargs), request_key='different-valid-key'
                )

            with (
                patch.object(peer, 'DeletionIntent', side_effect=changed),
                patch.object(
                    store, 'record_private_intent', wraps=store.record_private_intent
                ) as written,
            ):
                with self.assertRaises(module.SourceCopyError):
                    self.record_request(prepared)
                written.assert_not_called()
            request = self.record_request(prepared)
            native_assessment = peer.assess_readiness

            def reassessed(*args, **kwargs):
                value = native_assessment(*args, **kwargs)
                return replace(
                    value,
                    decisions=(
                        replace(value.decisions[0], disposition='review_required'),
                    ),
                )

            with patch.object(peer, 'assess_readiness', side_effect=reassessed):
                with self.assertRaises(module.SourceCopyError):
                    prepared.assess(request)
            self.assertEqual(
                prepared.assess(request).decisions[0].disposition, 'policy_retained'
            )
            native_planner = module._PreparedSourceCopy

            def replanned(*args):
                return replace(
                    native_planner(*args),
                    _planning_client=('other-environment', self.tenant.pk),
                )

            with patch.object(module, '_PreparedSourceCopy', side_effect=replanned):
                with self.assertRaises(module.SourceCopyError):
                    self.prepare_copy()
            self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_retry_keeps_fixed_cumulative_budget_and_retained_attempt_history(self):
        """A new attempt cannot replace failed rows or reset its original budget."""

        def operation():
            prepared = self.prepare_copy(retention='reviewable')
            request = self.record_request(prepared)
            impact = prepared.record_copy(request)
            first_id, next_id = uuid4(), uuid4()
            initial = prepared.start(request, impact, attempt=first_id, max_attempts=2)
            uncertain = prepared.transition(
                request,
                impact,
                initial,
                replace(initial.progress, sequence=1, status='uncertain'),
            )
            retried = prepared.retry(request, impact, uncertain, attempt=next_id)
            self.assertEqual(retried.progress.attempt_history, (first_id, next_id))
            self.assertEqual(retried.progress.max_attempts, 2)
            self.assertEqual(retried.progress.sequence, 0)
            self.assertEqual(retried.progress.status, 'planned')
            self.assertEqual(prepared.read_progress(request, impact), retried)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
            pending = prepared.transition(
                request,
                impact,
                retried,
                replace(retried.progress, sequence=1, status='pending'),
            )
            failed = prepared.transition(
                request,
                impact,
                pending,
                replace(pending.progress, sequence=2, status='failed'),
            )
            for attempt in (first_id, uuid4()):
                with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                    prepared.retry(request, impact, failed, attempt=attempt)
            with self.assertRaises(ValueError):
                prepared.start(request, impact, attempt=next_id, max_attempts=3)
            self.assertEqual(prepared.read_progress(request, impact), failed)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
            self.assertEqual(
                PrivateCopyAttempt.objects.get(attempt_id=first_id).checkpoint[
                    'status'
                ],
                'uncertain',
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)


class RealPolicySourceCopyTests(SourceCopyFixture, TransactionTestCase):
    """No historical attribution patch is installed for the real-policy control."""

    def test_actual_native_source_policy_refuses_unproven_history_before_copy_io(self):
        """A genuine allowed actor/grant does not supply historical source proof."""
        from unittest.mock import patch

        from django.core.files.storage import default_storage

        from assets.integration_authority import integration_actor_from_current_ai
        from integration_state import shared_requests as store
        from tasks.scope import scoped_work_orders

        def operation():
            module = self.copy_module()
            self.assertEqual(
                integration_actor_from_current_ai(required_role='work_order').pk,
                self.actor.pk,
            )
            self.assertTrue(
                scoped_work_orders(self.actor).filter(pk=self.work.pk).exists()
            )
            fixture = module.FixtureSourceCopy(
                copy_key='unproven-copy', copy_kind='unknown', source_digest='a' * 64
            )
            with (
                patch.object(
                    default_storage, 'open', wraps=default_storage.open
                ) as opened,
                patch.object(
                    store, 'record_private_intent', wraps=store.record_private_intent
                ) as written,
            ):
                for name, identity in (
                    ('work.core', self.work.pk),
                    ('work_document.content', self.attachment.pk),
                ):
                    with self.subTest(operation=name):
                        with self.assertRaises(module.SourceCopyUnavailableError):
                            module.prepare_source_copy(
                                fixture_copy=fixture,
                                fixture_client=ClientSourceIdentity(
                                    's3-test', self.tenant.pk
                                ),
                                **self.source_arguments(
                                    operation=name, source_pk=identity
                                ),
                            )
                opened.assert_not_called()
                written.assert_not_called()
            self.assertEqual(PrivateDeletionRequest.objects.count(), 0)
            self.assertEqual(PrivateCopyImpact.objects.count(), 0)
            self.assertEqual(PrivateCopyAttempt.objects.count(), 0)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
