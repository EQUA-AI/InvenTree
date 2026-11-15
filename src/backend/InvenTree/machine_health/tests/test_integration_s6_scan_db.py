"""Actual ASGI/native S6 recorded-window identities through S5/S7 scans."""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
from unittest.mock import patch

from django.db import transaction
from django.test import TransactionTestCase

from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from integration_state import shared_reconciliation as scan_store
from integration_state.models import (
    ReconciliationAttempt,
    ReconciliationIdentity,
    ReconciliationScan,
    SourceNamespace,
)
from integration_state.s7_contracts import SourceAspectIdentity
from integration_state.s7_reconciliation import ScanPage, ScanProgress
from integration_state.shared_events import SharedConflictError, SharedStateError
from integration_state.shared_reconciliation import DurableScan
from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter
from machine_health.tests.test_integration_s6_wire import observation


class HistoryScanTests(S6AIRequestFixture, TransactionTestCase):
    """Use actual middleware sessions and ordinary persisted roles/grants."""

    def setUp(self):
        """Keep the fixture source and private bookkeeping in an owned database."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='s6-test', active=True
        )

    def scan_module(self):
        """Preserve an absent-entry RED without pretending later assertions ran."""
        name = 'machine_health.services.integration_s6_scan'
        self.assertIsNotNone(find_spec(name), 'S6 needs its source-owned scan adapter')
        return import_module(name)

    def prepare(self, **changes):
        """Enter the real current-AI source path, not a synthetic principal."""
        arguments = {
            'environment': 's6-test',
            'machine_pk': self.machine.pk,
            'source_pk': self.source.pk,
            'binding_pk': self.binding.pk,
            'window': self.window,
            'adapter': self.adapter,
            'page_size': 1,
        }
        arguments.update(changes)
        return self.scan_module().prepare_history_scan(**arguments)

    def begin(self, prepared, **changes):
        """Use one explicit server-selected namespace/request and fixed budget."""
        arguments = {
            'namespace_id': self.namespace.pk,
            'epoch': self.namespace.epoch,
            'request_key': 's6-recorded-window',
            'max_pages': 4,
            'max_records': 8,
        }
        arguments.update(changes)
        return prepared.begin(**arguments)

    def set_records(self, identities):
        """Specify independent source truth, never a scan-derived expected population."""
        records = tuple(
            observation(
                value={'private-scan-body': index},
                source=self.identity,
                external_key=self.point.path,
                unit=self.point.unit,
                source_observation_id=identity,
            )
            for index, identity in enumerate(identities)
        )
        self.adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation='fixture-generation',
            records=records,
        )

    def test_actual_source_identity_reaches_durable_scan_without_population_authority(
        self,
    ):
        """A nonempty native-authorized source reaches real persisted S7 progress."""
        expected = self.record.sample.source_observation_id

        def operation():
            prepared = self.prepare()
            initial = self.begin(prepared)
            self.assertIs(type(initial), DurableScan)
            self.assertIs(type(initial.progress), ScanProgress)
            reserved = prepared.reserve(initial)
            self.assertEqual(reserved.progress.attempts_used, 1)
            self.assertEqual(ReconciliationAttempt.objects.count(), 1)
            final = prepared.accept(reserved, attempt=reserved.progress.pending_attempt)
            self.assertEqual(final.progress.state, 'transport_complete')
            self.assertEqual(final.progress.offset, 1)
            self.assertEqual(final.progress.expected_total, 1)
            (identity,) = final.progress.seen
            self.assertIs(type(identity), SourceAspectIdentity)
            self.assertIs(type(identity.source_id), str)
            self.assertEqual(identity.source_id, expected)
            assert_literal_bool(self, final.progress.population_complete, False)
            assert_literal_bool(self, final.progress.removal_complete, False)
            assert_literal_bool(self, final.progress.deletion_applied, False)
            self.assertEqual(final.progress.tombstones, ())
            self.assertEqual(
                prepared.read(
                    final.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                ),
                final,
            )
            self.assertEqual(ReconciliationScan.objects.count(), 1)
            self.assertEqual(ReconciliationIdentity.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_lost_attempt_retry_preserves_reservation_budget_and_exact_replay(self):
        """A source response loss does not reset attempts or create removal evidence."""

        def operation():
            prepared = self.prepare()
            initial = self.begin(prepared, max_pages=2)
            first = prepared.reserve(initial)
            lost = prepared.abandon(first, attempt=first.progress.pending_attempt)
            self.assertEqual(lost.progress.state, 'unavailable')
            self.assertEqual(lost.progress.attempts_used, 1)
            resumed = prepared.retry(lost)
            second = prepared.reserve(resumed)
            final = prepared.accept(second, attempt=second.progress.pending_attempt)
            self.assertEqual(final.progress.attempts_used, 2)
            self.assertEqual(
                prepared.accept(second, attempt=second.progress.pending_attempt), final
            )
            self.assertEqual(
                prepared.accept(final, attempt=second.progress.pending_attempt), final
            )
            self.assertEqual(ReconciliationAttempt.objects.count(), 2)
            assert_literal_bool(self, final.progress.population_complete, False)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_page_projection_cannot_persist_foreign_schema_valid_identity(self):
        """Validate producer-derived IDs before a real S5 acceptance commits them."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            reserved = prepared.reserve(self.begin(prepared))
            identity = SourceAspectIdentity(
                environment='s6-test',
                epoch=self.namespace.epoch,
                entity_kind=module.ASPECT[0],
                aspect=module.ASPECT[1],
                source_id='foreign-observation',
            )
            with patch.object(
                module, 'SourceAspectIdentity', return_value=identity
            ) as constructor:
                with self.assertRaises(module.HistoryScanError):
                    prepared.accept(reserved, attempt=reserved.progress.pending_attempt)
                constructor.assert_called_once()
            self.assertEqual(ReconciliationIdentity.objects.count(), 0)
            self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
            self.assertEqual(
                prepared.accept(
                    reserved, attempt=reserved.progress.pending_attempt
                ).progress.offset,
                1,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_binding_constructor_cannot_stamp_another_client_on_native_source(self):
        """A schema-valid binding cannot relabel this independently granted source."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            correct = prepared._binding(self.namespace.epoch)
            forged = replace(correct, client_id=self.foreign_tenant.pk)
            with patch.object(
                module, 'ScanBinding', return_value=forged
            ) as constructor:
                with self.assertRaises(module.HistoryScanError):
                    self.begin(prepared)
                self.assertGreater(constructor.call_count, 0)
            self.assertEqual(ReconciliationScan.objects.count(), 0)
            self.assertEqual(
                self.begin(prepared).progress.binding.client_id, self.tenant.pk
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_incomplete_page_constructor_cannot_commit_complete_empty_progress(self):
        """A real unresolved tie remains incomplete even when a constructor lies."""
        self.set_records(('tie:1', 'tie:2', 'tie:3'))

        def operation():
            module = self.scan_module()
            prepared = self.prepare(max_samples_per_read=2)
            assert_literal_bool(self, prepared._window.complete, False)
            reserved = prepared.reserve(self.begin(prepared))
            forged = ScanPage(
                binding=prepared._binding(self.namespace.epoch),
                offset=0,
                total=0,
                items=(),
                next_offset=None,
            )
            with patch.object(module, 'ScanPage', return_value=forged) as constructor:
                with self.assertRaises(module.HistoryScanError):
                    prepared.accept(reserved, attempt=reserved.progress.pending_attempt)
                constructor.assert_called_once()
            self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
            final = prepared.accept(reserved, attempt=reserved.progress.pending_attempt)
            self.assertEqual(final.progress.state, 'incomplete')
            self.assertIsNone(final.progress.expected_total)
            assert_literal_bool(self, final.progress.population_complete, False)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_factory_without_actual_invocation_or_supported_source_is_unavailable(self):
        """A real old principal/source selection does not mint a current handle."""
        module = self.scan_module()
        with self.assertRaises(module.HistoryScanUnavailableError):
            self.prepare()

        def operation():
            with self.assertRaises(module.HistoryScanUnavailableError):
                self.prepare(adapter=None)
            self.assertEqual(ReconciliationScan.objects.count(), 0)
            self.assertEqual(self.begin(self.prepare()).progress.state, 'running')
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_pages_resume_under_fresh_actual_invocation_with_literal_ids_and_same_window(
        self,
    ):
        """Fresh source authority resumes committed state, not an expired Python handle."""
        self.set_records(('7', ' 7 ', 'last'))

        def first():
            prepared = self.prepare(page_size=2)
            reserved = prepared.reserve(self.begin(prepared))
            result = prepared.accept(
                reserved, attempt=reserved.progress.pending_attempt
            )
            self.assertEqual(result.progress.offset, 2)
            return prepared, result

        old, prior = self.run_boundary(first, method='session')
        module = self.scan_module()
        with self.assertRaises(module.HistoryScanUnavailableError):
            old.reserve(prior)

        def resumed():
            prepared = self.prepare(page_size=2)
            current = prepared.read(
                prior.scan_id,
                namespace_id=self.namespace.pk,
                epoch=self.namespace.epoch,
            )
            self.assertEqual(current, prior)
            reserved = prepared.reserve(current)
            result = prepared.accept(
                reserved, attempt=reserved.progress.pending_attempt
            )
            self.assertEqual(result.progress.offset, 3)
            self.assertEqual(
                {item.source_id for item in result.progress.seen}, {'7', ' 7 ', 'last'}
            )
            self.assertEqual(result.progress.state, 'transport_complete')
            self.assertEqual(ReconciliationAttempt.objects.count(), 2)
            self.assertNotIn(
                'private-scan-body', str(ReconciliationScan.objects.get().checkpoint)
            )
            return True

        assert_literal_bool(self, self.run_boundary(resumed, method='session'), True)

    def test_source_correction_or_page_shape_change_refuses_old_progress(self):
        """Matching IDs alone do not bind original values or selected paging semantics."""
        prior = self.run_boundary(lambda: self.begin(self.prepare()), method='session')

        def operation():
            with self.assertRaises(SharedConflictError):
                self.prepare(page_size=2).read(
                    prior.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                )
            self.assertEqual(
                self.prepare().read(
                    prior.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                ),
                prior,
            )
            self.set_records((self.record.sample.source_observation_id,))
            with self.assertRaises(SharedConflictError):
                self.prepare().read(
                    prior.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                )
            self.assertEqual(ReconciliationAttempt.objects.count(), 0)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_complete_empty_transport_never_authorizes_removal(self):
        """A genuine empty source remains an exact selected transport, not population."""
        self.set_records(())

        def operation():
            prepared = self.prepare()
            reserved = prepared.reserve(self.begin(prepared))
            result = prepared.accept(
                reserved, attempt=reserved.progress.pending_attempt
            )
            self.assertEqual(result.progress.state, 'transport_complete')
            self.assertEqual(result.progress.expected_total, 0)
            self.assertEqual(result.progress.seen, frozenset())
            assert_literal_bool(self, result.progress.population_complete, False)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_nested_transaction_and_false_epoch_do_not_create_private_progress(self):
        """A durable ownership reservation must actually commit, not escape a savepoint."""
        from uuid import uuid4

        def operation():
            prepared = self.prepare()
            with transaction.atomic():
                with self.assertRaises(SharedStateError):
                    self.begin(prepared)
            with self.assertRaises(SharedStateError):
                self.begin(prepared, epoch=uuid4())
            self.assertEqual(ReconciliationScan.objects.count(), 0)
            self.assertEqual(self.begin(prepared).progress.state, 'running')
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_late_revocation_withholds_committed_reservation_without_resetting_it(self):
        """A refused disclosure is not rollback of a previously committed S5 attempt."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            initial = self.begin(prepared)
            original = scan_store.reserve_scan_page

            def revoke(*args, **kwargs):
                result = original(*args, **kwargs)
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                return result

            try:
                with patch.object(
                    scan_store, 'reserve_scan_page', side_effect=revoke
                ) as hook:
                    with self.assertRaises(module.HistoryScanUnavailableError):
                        prepared.reserve(initial)
                    hook.assert_called_once()
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            recovered = prepared.read(
                initial.scan_id,
                namespace_id=self.namespace.pk,
                epoch=self.namespace.epoch,
            )
            self.assertEqual(recovered.progress.attempts_used, 1)
            self.assertIsNotNone(recovered.progress.pending_attempt)
            with self.assertRaises(SharedConflictError):
                prepared.reserve(initial)
            self.assertEqual(ReconciliationAttempt.objects.count(), 1)
            self.assertEqual(
                prepared.accept(
                    recovered, attempt=recovered.progress.pending_attempt
                ).progress.offset,
                1,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_native_role_and_explicit_grant_are_independently_required_for_resume(self):
        """Keep the other real authority positive while changing one predicate."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            initial = self.begin(prepared)
            type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
            try:
                with self.assertRaises(module.HistoryScanUnavailableError):
                    prepared.read(
                        initial.scan_id,
                        namespace_id=self.namespace.pk,
                        epoch=self.namespace.epoch,
                    )
                self.assertTrue(
                    type(self.grant).objects.filter(pk=self.grant.pk).exists()
                )
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(
                prepared.read(
                    initial.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                ),
                initial,
            )
            grant_pk = self.grant.pk
            type(self.grant).objects.filter(pk=grant_pk).delete()
            try:
                with self.assertRaises(module.HistoryScanUnavailableError):
                    prepared.reserve(initial)
            finally:
                type(self.grant).objects.create(
                    pk=grant_pk, user=self.actor, client=self.tenant
                )
            reserved = prepared.reserve(initial)
            self.assertEqual(reserved.progress.attempts_used, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_store_fault_rechecks_authority_and_sanitizes_private_exception(self):
        """An ordinary store error neither leaks source text nor hides a revocation."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            initial = self.begin(prepared)
            with patch.object(
                scan_store,
                'reserve_scan_page',
                side_effect=RuntimeError('private-scan-fault-sentinel'),
            ):
                with self.assertRaises(module.HistoryScanError) as raised:
                    prepared.reserve(initial)
                self.assertNotIn('private-scan-fault-sentinel', str(raised.exception))

            def revoke(*args, **kwargs):
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                raise RuntimeError('private-scan-fault-sentinel')

            try:
                with patch.object(scan_store, 'reserve_scan_page', side_effect=revoke):
                    with self.assertRaises(module.HistoryScanUnavailableError):
                        prepared.reserve(initial)
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(ReconciliationAttempt.objects.count(), 0)
            self.assertEqual(prepared.reserve(initial).progress.attempts_used, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_forged_store_result_is_refused_without_undoing_committed_attempt(self):
        """A schema-valid returned version is not the actual stored checkpoint."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            initial = self.begin(prepared)
            original = scan_store.reserve_scan_page

            def forged(*args, **kwargs):
                result = original(*args, **kwargs)
                return replace(result, version=result.version + 1)

            with patch.object(
                scan_store, 'reserve_scan_page', side_effect=forged
            ) as hook:
                with self.assertRaises(module.HistoryScanError):
                    prepared.reserve(initial)
                hook.assert_called_once()
            current = prepared.read(
                initial.scan_id,
                namespace_id=self.namespace.pk,
                epoch=self.namespace.epoch,
            )
            self.assertEqual(current.version, initial.version + 1)
            self.assertEqual(current.progress.attempts_used, 1)
            self.assertEqual(ReconciliationAttempt.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_page_and_record_exhaustion_cannot_reset_fixed_budgets(self):
        """The source adapter preserves both actual S7 exhaustion transitions."""
        self.set_records(('source:1', 'source:2'))

        def operation():
            prepared = self.prepare()
            initial = self.begin(prepared, max_pages=1)
            reserved = prepared.reserve(initial)
            partial = prepared.accept(
                reserved, attempt=reserved.progress.pending_attempt
            )
            exhausted = prepared.reserve(partial)
            self.assertEqual(exhausted.progress.state, 'budget_exhausted')
            self.assertEqual(exhausted.progress.offset, 1)
            self.assertEqual(exhausted.progress.attempts_used, 1)
            with self.assertRaises(self.scan_module().HistoryScanError):
                prepared.retry(exhausted)
            with self.assertRaises(SharedConflictError):
                self.begin(prepared, max_pages=2)
            limited = self.begin(prepared, request_key='record-limit', max_records=1)
            pending = prepared.reserve(limited)
            result = prepared.accept(pending, attempt=pending.progress.pending_attempt)
            self.assertEqual(result.progress.state, 'budget_exhausted')
            self.assertEqual(result.progress.offset, 0)
            self.assertEqual(result.progress.attempts_used, 1)
            assert_literal_bool(self, result.progress.population_complete, False)
            self.assertEqual(ReconciliationAttempt.objects.count(), 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_memory_and_process_control_faults_keep_their_original_identity(self):
        """Ordinary sanitized faults do not swallow memory or process controls."""

        def operation():
            prepared = self.prepare()
            initial = self.begin(prepared)
            for fault in (MemoryError(), KeyboardInterrupt(), SystemExit(19)):
                with self.subTest(fault=type(fault).__name__):
                    with patch.object(
                        scan_store, 'reserve_scan_page', side_effect=fault
                    ) as hook:
                        with self.assertRaises(type(fault)) as raised:
                            prepared.reserve(initial)
                        self.assertIs(raised.exception, fault)
                        hook.assert_called_once()
            self.assertEqual(ReconciliationAttempt.objects.count(), 0)
            self.assertEqual(prepared.reserve(initial).progress.attempts_used, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_late_accept_revocation_preserves_already_committed_identities(self):
        """Post-commit refusal withholds disclosure, not previously committed work."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            reserved = prepared.reserve(self.begin(prepared))
            original = scan_store.accept_scan_page

            def revoke(*args, **kwargs):
                result = original(*args, **kwargs)
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                return result

            try:
                with patch.object(
                    scan_store, 'accept_scan_page', side_effect=revoke
                ) as hook:
                    with self.assertRaises(module.HistoryScanUnavailableError):
                        prepared.accept(
                            reserved, attempt=reserved.progress.pending_attempt
                        )
                    hook.assert_called_once()
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            recovered = prepared.read(
                reserved.scan_id,
                namespace_id=self.namespace.pk,
                epoch=self.namespace.epoch,
            )
            self.assertEqual(recovered.progress.state, 'transport_complete')
            self.assertEqual(ReconciliationIdentity.objects.count(), 1)
            self.assertEqual(
                prepared.accept(recovered, attempt=reserved.progress.pending_attempt),
                recovered,
            )
            assert_literal_bool(self, recovered.progress.population_complete, False)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_last_window_projection_cannot_outlive_native_source_authority(self):
        """Revoke inside the final actual coordinate constructor, not before it."""

        def operation():
            module = self.scan_module()
            prepared = self.prepare()
            initial = self.begin(prepared)
            constructor = module._WindowCoordinates
            observed = []

            def observe(*args, **kwargs):
                result = constructor(*args, **kwargs)
                observed.append(True)
                return result

            with patch.object(module, '_WindowCoordinates', side_effect=observe):
                self.assertEqual(
                    prepared.read(
                        initial.scan_id,
                        namespace_id=self.namespace.pk,
                        epoch=self.namespace.epoch,
                    ),
                    initial,
                )
            self.assertTrue(observed)
            target = len(observed)
            calls, activated = [], []

            def revoke(*args, **kwargs):
                result = constructor(*args, **kwargs)
                calls.append(True)
                if len(calls) == target:
                    type(self.rule).objects.filter(pk=self.rule.pk).update(
                        can_view=False
                    )
                    activated.append(True)
                return result

            try:
                with patch.object(module, '_WindowCoordinates', side_effect=revoke):
                    with self.assertRaises(module.HistoryScanUnavailableError):
                        prepared.read(
                            initial.scan_id,
                            namespace_id=self.namespace.pk,
                            epoch=self.namespace.epoch,
                        )
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(activated, [True])
            self.assertEqual(
                prepared.read(
                    initial.scan_id,
                    namespace_id=self.namespace.pk,
                    epoch=self.namespace.epoch,
                ),
                initial,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)
