"""Actual shared-ledger persistence with synthetic source observations only."""

from unittest import TestCase as UnitTestCase
from uuid import uuid4

from django.test import TestCase


class SharedEventTests(TestCase):
    """Generic private observations cannot replace native placement capture."""

    def coordinates(self):
        """Independent exact synthetic namespace/key/metadata fixture."""
        from integration_state.models import SourceNamespace
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )

        namespace = SourceNamespace.objects.create(
            environment='synthetic-shared', active=True
        )
        key = SharedSourceKey(
            namespace_id=namespace.pk,
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id='private-7',
        )
        return (
            namespace,
            key,
            SourceObservation(state='available', source_revision='source-v1'),
        )

    def test_distinct_typed_keys_commit_in_the_existing_state_and_change_ledger(self):
        """A registry retains native key types; no second generic event log exists."""
        from integration_state.models import SourceChange, SourceNamespace, SourceState
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )
        from integration_state.shared_events import record_observation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-shared', active=True
        )
        observation = SourceObservation(state='available', source_revision=None)
        results = []
        for source_id in (7, '7'):
            key = SharedSourceKey(
                namespace_id=namespace.pk,
                environment=namespace.environment,
                epoch=namespace.epoch,
                entity_kind='synthetic_document',
                aspect='metadata',
                source_id=source_id,
            )
            result = record_observation(
                key, event_key=str(uuid4()), expected_fence=0, observation=observation
            )
            self.assertEqual(result.key, key)
            self.assertEqual(result.fence, 1)
            self.assertIsNone(result.observation.source_revision)
            self.assertIsNone(result.key.declared_incarnation)
            self.assertFalse(result.observation.source_deleted)
            results.append(result)
        self.assertNotEqual(results[0].change_id, results[1].change_id)
        self.assertEqual(SourceState.objects.count(), 2)
        self.assertEqual(SourceChange.objects.count(), 2)
        for state in SourceState.objects.all():
            self.assertIsNone(state.source_id)
            self.assertIsNone(state.native_version)
            self.assertIsNotNone(state.generic_identity_id)
            self.assertFalse(state.deleted)
        for change in SourceChange.objects.all():
            self.assertIsNone(change.source_id)
            self.assertIsNotNone(change.generic_identity_id)
            self.assertEqual(change.operation, 'observed')
            self.assertIsNone(change.previous)
            self.assertEqual(change.current['state'], 'available')

    def test_replay_retains_prior_receipt_without_regressing_later_state(self):
        """Producer keys bind exact payloads, not whichever revision is latest."""
        from integration_state.models import SourceChange, SourceState
        from integration_state.shared_contracts import SourceObservation
        from integration_state.shared_events import (
            SharedConflictError,
            record_observation,
        )

        _, key, observation = self.coordinates()
        first = record_observation(
            key, event_key='first', expected_fence=0, observation=observation
        )
        later = SourceObservation(state='missing', source_revision=None)
        second = record_observation(
            key, event_key='later', expected_fence=1, observation=later
        )
        replay = record_observation(
            key, event_key='first', expected_fence=0, observation=observation
        )
        self.assertEqual(replay, first)
        self.assertEqual(SourceState.objects.get().revision, second.fence)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertFalse(SourceState.objects.get().deleted)
        with self.assertRaises(SharedConflictError):
            record_observation(
                key, event_key='first', expected_fence=0, observation=later
            )
        with self.assertRaises(SharedConflictError):
            record_observation(
                key, event_key='stale', expected_fence=0, observation=later
            )
        self.assertEqual(SourceChange.objects.count(), 2)

    def test_outer_rollback_and_real_event_insert_fault_leave_no_partial_state(self):
        """The same ledger and its registry commit or roll back together."""
        from unittest.mock import patch

        from django.db import IntegrityError, transaction

        from integration_state.models import (
            GenericSourceIdentity,
            SourceChange,
            SourceState,
        )
        from integration_state.shared_events import (
            SharedStoreUnavailableError,
            record_observation,
        )

        _, key, observation = self.coordinates()
        with self.assertRaisesRegex(RuntimeError, 'synthetic outer rollback'):
            with transaction.atomic():
                record_observation(
                    key,
                    event_key='rolled-back',
                    expected_fence=0,
                    observation=observation,
                )
                raise RuntimeError('synthetic outer rollback')
        self.assertFalse(SourceState.objects.exists())
        self.assertFalse(SourceChange.objects.exists())
        self.assertFalse(GenericSourceIdentity.objects.exists())
        original_create = SourceChange.objects.create

        def conflicting_insert(*args, **kwargs):
            original_create(*args, **kwargs)
            return original_create(*args, **kwargs)

        with patch.object(
            SourceChange.objects, 'create', side_effect=conflicting_insert
        ):
            with self.assertRaises(SharedStoreUnavailableError) as raised:
                record_observation(
                    key, event_key='failed', expected_fence=0, observation=observation
                )
            self.assertIsInstance(raised.exception.__cause__, IntegrityError)
        self.assertFalse(SourceState.objects.exists())
        self.assertFalse(SourceChange.objects.exists())
        self.assertFalse(GenericSourceIdentity.objects.exists())
        committed = record_observation(
            key, event_key='valid', expected_fence=0, observation=observation
        )
        self.assertEqual(committed.fence, 1)

    def test_tampered_stored_payload_cannot_be_returned_as_an_exact_replay(self):
        """A matching stored digest label is not a verified payload snapshot."""
        from integration_state.models import SourceChange
        from integration_state.shared_events import SharedStateError, record_observation

        _, key, observation = self.coordinates()
        first = record_observation(
            key, event_key='first', expected_fence=0, observation=observation
        )
        change = SourceChange.objects.get(pk=first.change_id)
        original = dict(change.current)
        change.current = original | {'source_revision': 'forged-source-revision'}
        change.save(update_fields=['current'])
        with self.assertRaises(SharedStateError):
            record_observation(
                key, event_key='first', expected_fence=0, observation=observation
            )
        change.current = original
        change.save(update_fields=['current'])
        self.assertEqual(
            record_observation(
                key, event_key='first', expected_fence=0, observation=observation
            ),
            first,
        )

    def test_previous_snapshot_is_bound_to_the_committed_event_digest(self):
        """A schema-valid forged before value is not the originally captured one."""
        from integration_state.models import SourceChange
        from integration_state.shared_contracts import SourceObservation
        from integration_state.shared_events import SharedStateError, record_observation

        _, key, observation = self.coordinates()
        record_observation(
            key, event_key='first', expected_fence=0, observation=observation
        )
        later = SourceObservation(state='missing')
        result = record_observation(
            key, event_key='later', expected_fence=1, observation=later
        )
        change = SourceChange.objects.get(pk=result.change_id)
        original = dict(change.previous)
        change.previous = original | {'source_revision': 'forged-before-revision'}
        change.save(update_fields=['previous'])
        with self.assertRaises(SharedStateError):
            record_observation(
                key, event_key='later', expected_fence=1, observation=later
            )
        change.previous = original
        change.save(update_fields=['previous'])
        self.assertEqual(
            record_observation(
                key, event_key='later', expected_fence=1, observation=later
            ),
            result,
        )


class SharedEntryTests(UnitTestCase):
    """Keep absent entry distinct from reached database behavior."""

    def test_versioned_shared_contract_is_declared(self):
        """A literal version identifies this private contract, not user authority."""
        from integration_state.shared_contracts import SHARED_SCHEMA

        self.assertEqual(SHARED_SCHEMA, 'aimms.shared-state.v1')
