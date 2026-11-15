"""Actual ASGI/S1/S6 source to the delivered private S5/S7 ledger APIs.

Synthetic fixture admission, SQLite and private metadata only: no native
telemetry writer, physical historical proof, worker, route or erasure effect.
"""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from importlib import import_module
from importlib.util import find_spec
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase
from django.utils import timezone

from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from integration_state import s7_changes, shared_delivery, shared_events
from integration_state.models import EventDelivery, SourceChange, SourceNamespace
from integration_state.s7_changes import delivery_change_from_claim
from integration_state.shared_contracts import SourceObservation
from integration_state.shared_delivery import claim_changes, configure_consumer
from integration_state.shared_events import SharedConflictError, SharedStateError


class HistoryDeliveryTests(S6AIRequestFixture, TransactionTestCase):
    """Use native non-superuser roles/grants and actual DB-session middleware."""

    def setUp(self):
        """Keep source and private bookkeeping in this disposable database."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='s6-test', active=True
        )

    def delivery_module(self):
        """Make the initial absent-entry RED distinct from behavioral controls."""
        name = 'machine_health.services.integration_s6_delivery'
        self.assertIsNotNone(
            find_spec(name), 'S6 needs its source-owned delivery bridge'
        )
        return import_module(name)

    def prepare(self, **changes):
        """Reach the actual source reader; never install a principal manually."""
        arguments = {
            'environment': 's6-test',
            'machine_pk': self.machine.pk,
            'source_pk': self.source.pk,
            'binding_pk': self.binding.pk,
            'window': self.window,
            'adapter': self.adapter,
        }
        arguments.update(changes)
        return self.delivery_module().prepare_history_delivery(**arguments)

    def append(self, prepared, **changes):
        """Use an explicit server namespace, event identity and expected CAS."""
        arguments = {
            'namespace_id': self.namespace.pk,
            'epoch': self.namespace.epoch,
            'event_key': 'synthetic-history-read',
            'expected_fence': 0,
        }
        arguments.update(changes)
        return prepared.record(**arguments)

    def configure(self, **changes):
        """Configuration and committed ownership are S5's, not the actor's grant."""
        arguments = {
            'aspects': (('telemetry_binding', 'recorded_history'),),
            'configuration_epoch': uuid4(),
        }
        arguments.update(changes)
        return configure_consumer(self.namespace.pk, 'private-s6-reader', **arguments)

    def claim(self, configuration, **changes):
        """The existing owner alone selects and leases committed events."""
        arguments = {
            'worker_key': 'private-s6-worker',
            'limit': 10,
            'lease_seconds': 300,
        }
        arguments.update(changes)
        return claim_changes(configuration.consumer_id, **arguments)

    def test_current_source_metadata_reaches_actual_delivery_without_source_authority(
        self,
    ):
        """A useful nonempty source produces one same-ledger event and private ack."""

        def operation():
            original = self.ai_read().outcome
            self.assertEqual(original.state, 'available')
            expected = hashlib.sha256(original.payload).hexdigest()
            prepared = self.prepare()
            change = self.append(prepared)
            self.assertEqual(change.key.source_id, self.binding.pk)
            self.assertIs(type(change.key.source_id), int)
            self.assertIsNone(change.key.declared_incarnation)
            self.assertIsNone(change.observation.source_revision)
            self.assertIsNone(change.observation.client_ids)
            self.assertEqual(change.observation.content_digest, expected)
            assert_literal_bool(self, change.observation.coverage_complete, False)
            assert_literal_bool(self, change.observation.source_deleted, False)
            self.assertEqual(SourceChange.objects.count(), 1)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            decoded = delivery_change_from_claim(
                environment='s6-test',
                worker_key='private-s6-worker',
                configuration=configuration,
                claim=claim,
            )
            self.assertEqual(decoded.change.event_id, change.change_id)
            self.assertEqual(decoded.change.observation, change.observation)
            assert_literal_bool(self, decoded.native_authorized, False)
            assert_literal_bool(self, decoded.population_complete, False)
            assert_literal_bool(self, decoded.deletion_applied, False)
            self.assertEqual(decoded.tombstones, ())
            receipt = prepared.acknowledge(
                change,
                configuration=configuration,
                claim=claim,
                worker_key='private-s6-worker',
            )
            self.assertEqual(receipt.change_id, change.change_id)
            self.assertEqual(receipt.outcome, 'applied')
            assert_literal_bool(self, receipt.erasure_complete, False)
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'acknowledged'
            )
            self.assertEqual(self.claim(configuration), ())
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_foreign_coherent_decoder_return_cannot_ack_original_claim(self):
        """Same event data under another consumer/lease is not this intake's result."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            foreign_config = configure_consumer(
                self.namespace.pk,
                'other-private-consumer',
                aspects=(('telemetry_binding', 'recorded_history'),),
                configuration_epoch=uuid4(),
            )
            (foreign_claim,) = self.claim(foreign_config)
            foreign_result = delivery_change_from_claim(
                environment='s6-test',
                worker_key='private-s6-worker',
                configuration=foreign_config,
                claim=foreign_claim,
            )
            self.assertEqual(foreign_result.change.event_id, change.change_id)
            with patch.object(
                s7_changes, 'delivery_change_from_claim', return_value=foreign_result
            ) as hook:
                with self.assertRaises(module.HistoryDeliveryError):
                    prepared.acknowledge(
                        change,
                        configuration=configuration,
                        claim=claim,
                        worker_key='private-s6-worker',
                    )
                hook.assert_called_once()
            self.assertEqual(
                EventDelivery.objects.filter(state='acknowledged').count(), 0
            )
            receipt = prepared.acknowledge(
                change,
                configuration=configuration,
                claim=claim,
                worker_key='private-s6-worker',
            )
            self.assertEqual(receipt.change_id, change.change_id)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_store_fault_is_sanitized_and_metadata_rolls_back(self):
        """A late private-store fault is not a source-bearing exception or success."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            original = shared_events.record_observation

            def fault(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('private-s6-fault-sentinel')

            with patch.object(
                shared_events, 'record_observation', side_effect=fault
            ) as hook:
                with self.assertRaises(module.HistoryDeliveryError) as raised:
                    self.append(prepared)
                self.assertNotIn('private-s6-fault-sentinel', str(raised.exception))
                hook.assert_called_once()
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_decode_fault_is_sanitized_without_acknowledgment(self):
        """An authorized decoder failure leaves the actual private lease unacked."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            with patch.object(
                s7_changes,
                'delivery_change_from_claim',
                side_effect=RuntimeError('private-s6-fault-sentinel'),
            ) as hook:
                with self.assertRaises(module.HistoryDeliveryError) as raised:
                    prepared.acknowledge(
                        change,
                        configuration=configuration,
                        claim=claim,
                        worker_key='private-s6-worker',
                    )
                self.assertNotIn('private-s6-fault-sentinel', str(raised.exception))
                hook.assert_called_once()
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'claimed'
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_revocation_during_store_fault_wins_over_original_fault(self):
        """Independent current source authority is refreshed on the error path too."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            original = shared_events.record_observation

            def fault(*args, **kwargs):
                original(*args, **kwargs)
                type(self.grant).objects.filter(pk=self.grant.pk).delete()
                raise RuntimeError('private-s6-fault-sentinel')

            with patch.object(
                shared_events, 'record_observation', side_effect=fault
            ) as hook:
                with self.assertRaises(module.HistoryDeliveryUnavailableError):
                    self.append(prepared)
                hook.assert_called_once()
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertTrue(type(self.grant).objects.filter(pk=self.grant.pk).exists())
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_changed_private_binding_cannot_relabel_authorized_source(self):
        """An altered handle cannot turn one qualified binding into another source."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            forged = replace(prepared, _binding_pk=self.binding.pk + 100)
            with self.assertRaises(module.HistoryDeliveryUnavailableError):
                self.append(forged)
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).key.source_id, self.binding.pk)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_missing_request_and_unsupported_source_never_persist_negative_observations(
        self,
    ):
        """Unavailable is neither empty success nor deletion or access-withdrawal evidence."""
        module = self.delivery_module()
        with self.assertRaises(module.HistoryDeliveryUnavailableError):
            self.prepare()

        def operation():
            with self.assertRaises(module.HistoryDeliveryUnavailableError):
                self.prepare(adapter=None)
            self.assertEqual(SourceChange.objects.count(), 0)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_prepared_handle_expires_with_its_actual_middleware_invocation(self):
        """A retained Python object and genuine old principal are not new authority."""
        prepared = self.run_boundary(self.prepare, method='session')
        module = self.delivery_module()
        self.assertNotIn('fixture-generation', repr(prepared))
        with self.assertRaises(module.HistoryDeliveryUnavailableError):
            self.append(prepared)
        self.assertEqual(SourceChange.objects.count(), 0)

    def test_role_and_explicit_grant_are_independently_current_before_record(self):
        """Start allowed, narrow one real native predicate, then restore its positive."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
            try:
                with self.assertRaises(module.HistoryDeliveryUnavailableError):
                    self.append(prepared)
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(SourceChange.objects.count(), 0)
            grant_pk = self.grant.pk
            type(self.grant).objects.filter(pk=grant_pk).delete()
            try:
                with self.assertRaises(module.HistoryDeliveryUnavailableError):
                    self.append(prepared)
            finally:
                type(self.grant).objects.create(
                    pk=grant_pk, user=self.actor, client=self.tenant
                )
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_returned_store_dto_is_source_bound_and_late_revocation_rolls_back(self):
        """Fresh authority is not sufficient for a wrong digest or late revoked result."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            original = shared_events.record_observation

            def forged(*args, **kwargs):
                result = original(*args, **kwargs)
                return replace(
                    result,
                    observation=SourceObservation(
                        state='available', content_digest='f' * 64
                    ),
                )

            with patch.object(
                shared_events, 'record_observation', side_effect=forged
            ) as hook:
                with self.assertRaises(module.HistoryDeliveryError):
                    self.append(prepared)
                hook.assert_called_once()
            self.assertEqual(SourceChange.objects.count(), 0)

            def revoke(*args, **kwargs):
                result = original(*args, **kwargs)
                type(self.grant).objects.filter(pk=self.grant.pk).delete()
                return result

            with patch.object(
                shared_events, 'record_observation', side_effect=revoke
            ) as hook:
                with self.assertRaises(module.HistoryDeliveryUnavailableError):
                    self.append(prepared)
                hook.assert_called_once()
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertTrue(type(self.grant).objects.filter(pk=self.grant.pk).exists())
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_replay_fence_and_outer_source_transaction_rollback_use_same_ledger(self):
        """No second ledger or high-water cursor is introduced by a metadata replay."""
        from django.db import transaction

        def operation():
            prepared = self.prepare()
            with self.assertRaisesRegex(RuntimeError, 'synthetic-rollback'):
                with transaction.atomic():
                    self.append(prepared)
                    raise RuntimeError('synthetic-rollback')
            self.assertEqual(SourceChange.objects.count(), 0)
            first = self.append(prepared)
            self.assertEqual(self.append(prepared), first)
            with self.assertRaises(SharedConflictError):
                self.append(prepared, event_key='new-event-with-stale-fence')
            second = self.append(
                prepared, event_key='next-explicit-event', expected_fence=1
            )
            self.assertEqual(second.previous, first.observation)
            self.assertEqual(second.fence, 2)
            self.assertIsNone(second.observation.source_revision)
            self.assertEqual(SourceChange.objects.count(), 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_current_source_refusal_after_decode_leaves_real_claim_unacknowledged(self):
        """Revoke after canonical S7 work, not at a mocked permission decision."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            original = s7_changes.delivery_change_from_claim

            def revoke(**kwargs):
                result = original(**kwargs)
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                return result

            try:
                with patch.object(
                    s7_changes, 'delivery_change_from_claim', side_effect=revoke
                ) as hook:
                    with self.assertRaises(module.HistoryDeliveryUnavailableError):
                        prepared.acknowledge(
                            change,
                            configuration=configuration,
                            claim=claim,
                            worker_key='private-s6-worker',
                        )
                    hook.assert_called_once()
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'claimed'
            )
            self.assertEqual(
                prepared.acknowledge(
                    change,
                    configuration=configuration,
                    claim=claim,
                    worker_key='private-s6-worker',
                ).change_id,
                change.change_id,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_old_lease_cannot_ack_but_actual_replacement_can(self):
        """Canonical old metadata is not ownership; S5 alone verifies lease IO."""

        def operation():
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (old,) = self.claim(configuration)
            EventDelivery.objects.filter(pk=old.delivery_id).update(
                lease_until=timezone.now() - timedelta(seconds=1)
            )
            (current,) = self.claim(configuration)
            self.assertGreater(current.ownership_fence, old.ownership_fence)
            with self.assertRaises(SharedConflictError):
                prepared.acknowledge(
                    change,
                    configuration=configuration,
                    claim=old,
                    worker_key='private-s6-worker',
                )
            receipt = prepared.acknowledge(
                change,
                configuration=configuration,
                claim=current,
                worker_key='private-s6-worker',
            )
            self.assertEqual(receipt.change_id, change.change_id)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_replaced_configuration_does_not_inherit_delivery_authority(self):
        """A new actual consumer epoch intentionally obtains its own private lease."""

        def operation():
            prepared = self.prepare()
            change = self.append(prepared)
            old_config = self.configure()
            (old,) = self.claim(old_config)
            current_config = self.configure()
            with self.assertRaises(SharedStateError):
                prepared.acknowledge(
                    change,
                    configuration=old_config,
                    claim=old,
                    worker_key='private-s6-worker',
                )
            (current,) = self.claim(current_config)
            self.assertEqual(
                prepared.acknowledge(
                    change,
                    configuration=current_config,
                    claim=current,
                    worker_key='private-s6-worker',
                ).change_id,
                change.change_id,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_coherent_changed_payload_is_not_this_source_observation(self):
        """Recomputed transport digest does not authorize a native revision claim."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            body = json.loads(claim.event_payload)
            body['current']['source_revision'] = 0
            payload = json.dumps(
                body,
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=False,
                allow_nan=False,
            )
            changed = replace(
                claim,
                event_payload=payload,
                payload_digest=hashlib.sha256(payload.encode()).hexdigest(),
            )
            with self.assertRaises(module.HistoryDeliveryError):
                prepared.acknowledge(
                    change,
                    configuration=configuration,
                    claim=changed,
                    worker_key='private-s6-worker',
                )
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'claimed'
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_post_commit_source_refusal_does_not_erase_or_resubmit_bookkeeping(self):
        """Late refusal withholds output; it cannot undo an already committed private ack."""

        def operation():
            module = self.delivery_module()
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            original = shared_delivery.ack_change

            def revoke(*args, **kwargs):
                result = original(*args, **kwargs)
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=False)
                return result

            try:
                with patch.object(
                    shared_delivery, 'ack_change', side_effect=revoke
                ) as hook:
                    with self.assertRaises(module.HistoryDeliveryUnavailableError):
                        prepared.acknowledge(
                            change,
                            configuration=configuration,
                            claim=claim,
                            worker_key='private-s6-worker',
                        )
                    hook.assert_called_once()
            finally:
                type(self.rule).objects.filter(pk=self.rule.pk).update(can_view=True)
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'acknowledged'
            )
            self.assertEqual(SourceChange.objects.count(), 1)
            receipt = prepared.acknowledge(
                change,
                configuration=configuration,
                claim=claim,
                worker_key='private-s6-worker',
            )
            assert_literal_bool(self, receipt.deletion_applied, False)
            assert_literal_bool(self, receipt.erasure_complete, False)
            self.assertEqual(EventDelivery.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)

    def test_memory_and_process_control_faults_propagate_with_metadata_rollback(self):
        """Do not turn process termination into unavailable or an empty success."""

        def operation():
            prepared = self.prepare()
            original = shared_events.record_observation
            for exception in (MemoryError, KeyboardInterrupt, SystemExit):
                with self.subTest(exception=exception.__name__):

                    def fault(*args, failure=exception, **kwargs):
                        original(*args, **kwargs)
                        raise failure('synthetic-control-flow')

                    with patch.object(
                        shared_events, 'record_observation', side_effect=fault
                    ) as hook:
                        with self.assertRaises(exception):
                            self.append(prepared)
                        hook.assert_called_once()
                    self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation, method='session'), True)
