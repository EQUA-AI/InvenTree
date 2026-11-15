"""Durable actual S7 scan carriers; transport completion is not source truth."""

from django.db import connection
from django.test import TestCase


class SharedReconciliationTests(TestCase):
    """Private owner callbacks do not install native grants or completeness."""

    def coordinates(self):
        """Independent literal owner/population fixture, never a native grant."""
        from integration_state.models import SourceNamespace
        from integration_state.s7_reconciliation import ScanBinding

        namespace = SourceNamespace.objects.create(
            environment='synthetic-scan-controls', active=True
        )
        binding = ScanBinding(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            actor_key='synthetic-owner',
            client_id=7,
            filter_key='selected-native-filter',
            generation='fixture-generation',
        )
        return namespace, binding

    def test_actual_empty_scan_reservation_completion_and_replay_are_persisted(self):
        """Real S7 carriers survive durable boundaries without invented deletion."""
        from integration_state.models import (
            ReconciliationAttempt,
            ReconciliationScan,
            SourceNamespace,
        )
        from integration_state.s7_reconciliation import (
            ScanBinding,
            ScanPage,
            ScanProgress,
        )
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            begin_scan,
            read_scan,
            reserve_scan_page,
        )

        namespace = SourceNamespace.objects.create(
            environment='synthetic-durable-scan', active=True
        )
        binding = ScanBinding(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            actor_key='synthetic-scan-owner',
            client_id=7,
            filter_key='selected-fixture-filter',
            generation='fixture-generation-1',
        )
        outside_depth = len(connection.atomic_blocks)
        callback_depths = []

        def authorize(value):
            self.assertEqual(value, binding)
            callback_depths.append(len(connection.atomic_blocks))
            return True

        initial = begin_scan(
            namespace.pk,
            'private-scan-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=authorize,
        )
        self.assertIs(type(initial.progress), ScanProgress)
        self.assertEqual(
            begin_scan(
                namespace.pk,
                'private-scan-request',
                binding,
                max_pages=2,
                max_records=5,
                authorize=authorize,
            ),
            initial,
        )
        reserved = reserve_scan_page(initial, authorize=authorize)
        self.assertEqual(reserved.progress.attempts_used, 1)
        self.assertEqual(
            read_scan(initial.scan_id, binding=binding, authorize=authorize), reserved
        )
        page = ScanPage(binding=binding, offset=0, total=0, items=(), next_offset=None)
        completed = accept_scan_page(
            reserved,
            page,
            attempt=reserved.progress.pending_attempt,
            authorize=authorize,
        )
        self.assertEqual(completed.progress.state, 'transport_complete')
        self.assertFalse(completed.progress.population_complete)
        self.assertFalse(completed.progress.removal_complete)
        self.assertFalse(completed.progress.deletion_applied)
        self.assertEqual(
            accept_scan_page(
                reserved,
                page,
                attempt=reserved.progress.pending_attempt,
                authorize=authorize,
            ),
            completed,
        )
        self.assertEqual(ReconciliationScan.objects.count(), 1)
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertTrue(callback_depths)
        self.assertTrue(all(depth == outside_depth for depth in callback_depths))

    def test_epoch_change_in_last_owner_callback_withholds_the_committed_checkpoint(
        self,
    ):
        """A true owner verdict cannot keep an old namespace epoch current."""
        from uuid import uuid4

        from integration_state.models import ReconciliationScan, SourceNamespace
        from integration_state.s7_reconciliation import ScanBinding
        from integration_state.shared_events import SharedStateError
        from integration_state.shared_reconciliation import begin_scan

        namespace = SourceNamespace.objects.create(
            environment='synthetic-last-check', active=True
        )
        binding = ScanBinding(
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            actor_key='synthetic-owner',
            client_id=7,
            filter_key='native-filter',
            generation='fixture-generation',
        )
        calls = []

        def changed(value):
            self.assertEqual(value, binding)
            calls.append(True)
            if len(calls) == 2:
                SourceNamespace.objects.filter(pk=namespace.pk).update(epoch=uuid4())
            return True

        with self.assertRaises(SharedStateError):
            begin_scan(
                namespace.pk,
                'withheld-request',
                binding,
                max_pages=2,
                max_records=5,
                authorize=changed,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(ReconciliationScan.objects.count(), 1)

    def test_nonempty_exact_typed_seen_identities_survive_pages_and_stale_reservation(
        self,
    ):
        """Distinct int/text IDs and actual continuity survive durable round trips."""
        from integration_state.models import ReconciliationIdentity
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_reconciliation import ScanPage
        from integration_state.shared_events import SharedConflictError
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            begin_scan,
            read_scan,
            reserve_scan_page,
        )

        namespace, binding = self.coordinates()
        initial = begin_scan(
            namespace.pk,
            'nonempty-request',
            binding,
            max_pages=3,
            max_records=3,
            authorize=lambda value: True,
        )
        first = reserve_scan_page(initial, authorize=lambda value: True)
        identities = tuple(
            SourceAspectIdentity(
                environment=binding.environment,
                epoch=binding.epoch,
                entity_kind=binding.entity_kind,
                aspect=binding.aspect,
                source_id=key,
            )
            for key in (7, '7', 8)
        )
        page = ScanPage(
            binding=binding, offset=0, total=3, items=identities[:2], next_offset=2
        )
        progress = accept_scan_page(
            first,
            page,
            attempt=first.progress.pending_attempt,
            authorize=lambda value: True,
        )
        self.assertEqual(
            read_scan(initial.scan_id, binding=binding, authorize=lambda value: True),
            progress,
        )
        self.assertEqual(progress.progress.seen, frozenset(identities[:2]))
        with self.assertRaises(SharedConflictError):
            reserve_scan_page(initial, authorize=lambda value: True)
        second = reserve_scan_page(progress, authorize=lambda value: True)
        final = accept_scan_page(
            second,
            ScanPage(
                binding=binding,
                offset=2,
                total=3,
                items=identities[2:],
                next_offset=None,
            ),
            attempt=second.progress.pending_attempt,
            authorize=lambda value: True,
        )
        self.assertEqual(final.progress.state, 'transport_complete')
        self.assertEqual(final.progress.seen, frozenset(identities))
        self.assertEqual(ReconciliationIdentity.objects.count(), 3)
        self.assertFalse(final.progress.population_complete)

    def test_pending_checkpoint_without_its_durable_attempt_journal_is_unqualified(
        self,
    ):
        """A stored progress digest is not proof of complete reservation history."""
        from integration_state.models import ReconciliationAttempt
        from integration_state.shared_events import SharedStateError
        from integration_state.shared_reconciliation import (
            begin_scan,
            read_scan,
            reserve_scan_page,
        )

        namespace, binding = self.coordinates()
        initial = begin_scan(
            namespace.pk,
            'journal-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        pending = reserve_scan_page(initial, authorize=lambda value: True)
        self.assertEqual(
            read_scan(initial.scan_id, binding=binding, authorize=lambda value: True),
            pending,
        )
        ReconciliationAttempt.objects.get(scan_id=initial.scan_id).delete()
        with self.assertRaises(SharedStateError):
            read_scan(initial.scan_id, binding=binding, authorize=lambda value: True)

    def test_reservation_journal_requires_exact_pending_owner_version_and_response(
        self,
    ):
        """A complete ordinal count does not qualify a different stored attempt."""
        from uuid import uuid4

        from integration_state.models import ReconciliationAttempt
        from integration_state.s7_reconciliation import ScanPage
        from integration_state.shared_events import SharedStateError
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            begin_scan,
            read_scan,
            reserve_scan_page,
        )

        namespace, binding = self.coordinates()
        initial = begin_scan(
            namespace.pk,
            'journal-owner-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        pending = reserve_scan_page(initial, authorize=lambda value: True)
        row = ReconciliationAttempt.objects.get(scan_id=initial.scan_id)
        original = {
            name: getattr(row, name)
            for name in ('attempt_id', 'reserved_version', 'state', 'response_digest')
        }
        for changed in (
            {'attempt_id': uuid4()},
            {'reserved_version': pending.version + 1},
            {'state': 'accepted'},
            {'response_digest': '0' * 64},
        ):
            with self.subTest(fields=tuple(changed)):
                try:
                    ReconciliationAttempt.objects.filter(pk=row.pk).update(**changed)
                    with self.assertRaises(SharedStateError):
                        read_scan(
                            initial.scan_id,
                            binding=binding,
                            authorize=lambda value: True,
                        )
                finally:
                    ReconciliationAttempt.objects.filter(pk=row.pk).update(**original)
        completed = accept_scan_page(
            pending,
            ScanPage(binding=binding, offset=0, total=0, items=(), next_offset=None),
            attempt=pending.progress.pending_attempt,
            authorize=lambda value: True,
        )
        self.assertEqual(
            read_scan(initial.scan_id, binding=binding, authorize=lambda value: True),
            completed,
        )
        ReconciliationAttempt.objects.filter(pk=row.pk).update(response_digest='0' * 64)
        with self.assertRaises(SharedStateError):
            read_scan(initial.scan_id, binding=binding, authorize=lambda value: True)

    def test_schema_valid_constructor_cannot_forge_the_returned_checkpoint_version(
        self,
    ):
        """Reauthorization cannot certify data changed by the final constructor."""
        from unittest import mock

        from integration_state import shared_reconciliation as durable
        from integration_state.shared_events import SharedStateError

        namespace, binding = self.coordinates()
        initial = durable.begin_scan(
            namespace.pk,
            'constructor-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        self.assertEqual(
            durable.read_scan(
                initial.scan_id, binding=binding, authorize=lambda value: True
            ),
            initial,
        )
        original = durable.DurableScan.__post_init__
        activated = []

        def forged(value):
            original(value)
            activated.append(True)
            object.__setattr__(value, 'version', value.version + 1)
            original(value)

        with mock.patch.object(durable.DurableScan, '__post_init__', forged):
            with self.assertRaises(SharedStateError):
                durable.read_scan(
                    initial.scan_id, binding=binding, authorize=lambda value: True
                )
        self.assertTrue(activated)

    def test_repeated_response_using_current_checkpoint_is_not_a_new_version(self):
        """Actual S7 replay leaves attempts, version and response history unchanged."""
        from integration_state import shared_reconciliation as durable
        from integration_state.models import ReconciliationAttempt
        from integration_state.s7_reconciliation import ScanPage

        namespace, binding = self.coordinates()
        initial = durable.begin_scan(
            namespace.pk,
            'current-replay-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        pending = durable.reserve_scan_page(initial, authorize=lambda value: True)
        page = ScanPage(binding=binding, offset=0, total=0, items=(), next_offset=None)
        completed = durable.accept_scan_page(
            pending,
            page,
            attempt=pending.progress.pending_attempt,
            authorize=lambda value: True,
        )
        self.assertEqual(
            durable.accept_scan_page(
                completed,
                page,
                attempt=pending.progress.pending_attempt,
                authorize=lambda value: True,
            ),
            completed,
        )
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)

    def test_actual_s7_fault_and_malformed_page_outcomes_preserve_partial_checkpoint(
        self,
    ):
        """Bad continuity and unknown source results do not become empty success."""
        from dataclasses import replace

        from integration_state import shared_reconciliation as durable
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_reconciliation import ScanPage

        namespace, binding = self.coordinates()
        identities = tuple(
            SourceAspectIdentity(
                environment=binding.environment,
                epoch=binding.epoch,
                entity_kind=binding.entity_kind,
                aspect=binding.aspect,
                source_id=key,
            )
            for key in (7, 8)
        )
        pages = (
            ScanPage(
                binding=binding, offset=2, total=3, items=identities[1:], next_offset=3
            ),
            ScanPage(
                binding=binding, offset=1, total=4, items=identities[1:], next_offset=2
            ),
            ScanPage(binding=binding, offset=1, total=3, items=(), next_offset=1),
            ScanPage(
                binding=binding, offset=1, total=3, items=identities, next_offset=None
            ),
            ScanPage(
                binding=replace(binding, generation='different-generation'),
                offset=1,
                total=3,
                items=identities[1:],
                next_offset=2,
            ),
            *(
                ScanPage(
                    binding=binding,
                    offset=1,
                    total=None,
                    items=(),
                    next_offset=None,
                    state=state,
                )
                for state in ('denied', 'unavailable', 'incomplete')
            ),
        )
        for index, page in enumerate(pages):
            with self.subTest(case=index):
                initial = durable.begin_scan(
                    namespace.pk,
                    f'partial-request-{index}',
                    binding,
                    max_pages=3,
                    max_records=5,
                    authorize=lambda value: True,
                )
                first = durable.reserve_scan_page(initial, authorize=lambda value: True)
                partial = durable.accept_scan_page(
                    first,
                    ScanPage(
                        binding=binding,
                        offset=0,
                        total=3,
                        items=identities[:1],
                        next_offset=1,
                    ),
                    attempt=first.progress.pending_attempt,
                    authorize=lambda value: True,
                )
                second = durable.reserve_scan_page(
                    partial, authorize=lambda value: True
                )
                failed = durable.accept_scan_page(
                    second,
                    page,
                    attempt=second.progress.pending_attempt,
                    authorize=lambda value: True,
                )
                self.assertEqual(
                    failed.progress.state,
                    'incomplete' if page.state == 'available' else page.state,
                )
                self.assertEqual(failed.progress.offset, 1)
                self.assertEqual(failed.progress.expected_total, 3)
                self.assertEqual(failed.progress.seen, partial.progress.seen)
                self.assertEqual(failed.progress.attempts_used, 2)
                self.assertEqual(
                    durable.read_scan(
                        initial.scan_id, binding=binding, authorize=lambda value: True
                    ),
                    failed,
                )
                self.assertFalse(failed.progress.population_complete)
                self.assertFalse(failed.progress.removal_complete)
                self.assertFalse(failed.progress.deletion_applied)
                self.assertEqual(failed.progress.tombstones, ())

    def test_ownership_cannot_escape_caller_savepoint_and_denial_withholds_after_commit(
        self,
    ):
        """No rollbackable page lease or grant manufactured by private metadata."""
        from django.db import transaction

        from integration_state import shared_reconciliation as durable
        from integration_state.models import ReconciliationAttempt, ReconciliationScan
        from integration_state.shared_events import SharedStateError

        namespace, binding = self.coordinates()
        initial = durable.begin_scan(
            namespace.pk,
            'savepoint-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        with transaction.atomic(), self.assertRaises(SharedStateError):
            durable.reserve_scan_page(initial, authorize=lambda value: True)
        self.assertEqual(ReconciliationAttempt.objects.count(), 0)
        checks = iter((True, False))
        with self.assertRaises(PermissionError):
            durable.reserve_scan_page(initial, authorize=lambda value: next(checks))
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertEqual(ReconciliationScan.objects.get().version, initial.version + 1)
        with self.assertRaises(PermissionError):
            durable.read_scan(
                initial.scan_id, binding=binding, authorize=lambda value: False
            )
        self.assertIsNotNone(
            durable.read_scan(
                initial.scan_id, binding=binding, authorize=lambda value: True
            ).progress.pending_attempt
        )

    def test_store_failure_is_typed_and_rechecks_owner_before_raising(self):
        """Failed bookkeeping is neither empty success nor stale error disclosure."""
        from unittest import mock

        from django.db import OperationalError

        from integration_state import shared_reconciliation as durable
        from integration_state.shared_events import SharedStoreUnavailableError

        namespace, binding = self.coordinates()
        initial = durable.begin_scan(
            namespace.pk,
            'fault-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        outside = len(connection.atomic_blocks)
        depths = []

        def owner(value):
            depths.append(len(connection.atomic_blocks))
            return True

        with mock.patch.object(
            durable,
            '_open',
            side_effect=OperationalError('synthetic scan-store failure'),
        ):
            with self.assertRaises(SharedStoreUnavailableError) as raised:
                durable.read_scan(initial.scan_id, binding=binding, authorize=owner)
            self.assertIsInstance(raised.exception.__cause__, OperationalError)
            checks = iter((True, False))
            with self.assertRaises(PermissionError):
                durable.read_scan(
                    initial.scan_id,
                    binding=binding,
                    authorize=lambda value: next(checks),
                )
        self.assertEqual(depths, [outside, outside])

    def test_same_request_cannot_adopt_changed_actor_filter_or_budget(self):
        """Even a true synthetic owner callback cannot rewrite retained bindings."""
        from dataclasses import replace

        from integration_state.shared_events import SharedConflictError
        from integration_state.shared_reconciliation import begin_scan, read_scan

        namespace, binding = self.coordinates()
        initial = begin_scan(
            namespace.pk,
            'bound-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        for values in (
            {'actor_key': 'different-owner'},
            {'client_id': 8},
            {'filter_key': 'different-filter'},
            {'generation': 'different-generation'},
        ):
            altered = replace(binding, **values)
            with self.subTest(changed=tuple(values)):
                with self.assertRaises(SharedConflictError):
                    begin_scan(
                        namespace.pk,
                        'bound-request',
                        altered,
                        max_pages=2,
                        max_records=5,
                        authorize=lambda value: True,
                    )
                with self.assertRaises(SharedConflictError):
                    read_scan(
                        initial.scan_id, binding=altered, authorize=lambda value: True
                    )
        with self.assertRaises(SharedConflictError):
            begin_scan(
                namespace.pk,
                'bound-request',
                binding,
                max_pages=3,
                max_records=5,
                authorize=lambda value: True,
            )

    def test_abandoned_and_unavailable_attempts_consume_cumulative_budget_across_retry(
        self,
    ):
        """A crash/retry cannot reset source work or accept a delayed old response."""
        from integration_state import shared_reconciliation as durable
        from integration_state.models import ReconciliationAttempt
        from integration_state.s7_reconciliation import ScanPage
        from integration_state.shared_events import SharedConflictError

        namespace, binding = self.coordinates()
        initial = durable.begin_scan(
            namespace.pk,
            'retry-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )
        reserved = durable.reserve_scan_page(initial, authorize=lambda value: True)
        old = reserved.progress.pending_attempt
        recovered = durable.read_scan(
            initial.scan_id, binding=binding, authorize=lambda value: True
        )
        self.assertEqual(recovered, reserved)
        abandoned = durable.abandon_scan_page(
            recovered, attempt=old, authorize=lambda value: True
        )
        self.assertEqual(abandoned.progress.state, 'unavailable')
        self.assertEqual(abandoned.progress.attempts_used, 1)
        retry = durable.retry_scan(abandoned, authorize=lambda value: True)
        second = durable.reserve_scan_page(retry, authorize=lambda value: True)
        failed = durable.accept_scan_page(
            second,
            ScanPage(
                binding=binding,
                offset=0,
                total=None,
                items=(),
                next_offset=None,
                state='unavailable',
            ),
            attempt=second.progress.pending_attempt,
            authorize=lambda value: True,
        )
        exhausted = durable.retry_scan(failed, authorize=lambda value: True)
        self.assertEqual(exhausted.progress.state, 'budget_exhausted')
        self.assertEqual(exhausted.progress.attempts_used, 2)
        self.assertEqual(ReconciliationAttempt.objects.count(), 2)
        self.assertEqual(
            ReconciliationAttempt.objects.get(attempt_id=old).state, 'abandoned'
        )
        with self.assertRaises(SharedConflictError):
            durable.accept_scan_page(
                reserved,
                ScanPage(
                    binding=binding, offset=0, total=0, items=(), next_offset=None
                ),
                attempt=old,
                authorize=lambda value: True,
            )
