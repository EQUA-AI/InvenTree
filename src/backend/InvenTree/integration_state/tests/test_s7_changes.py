"""S5 producer-shaped private change controls; no source or actor authority."""

from integration_state.tests.assertions import assert_literal_bool

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID


class PlacementChangeTests(unittest.TestCase):
    """Keep placement evidence bounded to its real S5 aspect and fields."""

    @staticmethod
    def record(**changes):
        """Supply independent SourceChange.values-shaped synthetic source truth."""
        return {
            'id': 7,
            'namespace_id': 3,
            'epoch': UUID(int=1),
            'entity_kind': 'machine',
            'aspect': 'placement',
            'source_id': 41,
            'revision': 1,
            'operation': 'created',
            'previous': None,
            'captured_at': datetime(2026, 1, 1, tzinfo=timezone.utc),
            'current': {'client_id': 11, 'location_id': 51, 'native_version': 0},
            **changes,
        }

    def parse(self, record):
        """Use the real new adapter, not replacement fixture behavior."""
        from integration_state.s7_changes import placement_change_from_values

        return placement_change_from_values(
            environment='synthetic-s7', namespace_id=3, epoch=UUID(int=1), record=record
        )

    def test_s5_expectation_does_not_invent_an_event_or_other_aspect(self):
        """The actual S5 carrier supplies placement/fence, not a CDC event ID."""
        from assets.integration_contracts import (
            ClientSourceIdentity,
            LocationSourceIdentity,
            MachineSourceIdentity,
        )
        from assets.integration_s5_contracts import PlacementExpectation
        from integration_state.s7_changes import placement_state_from_expectation

        source = PlacementExpectation(
            machine=MachineSourceIdentity('synthetic-s7', 41),
            client=ClientSourceIdentity('synthetic-s7', 11),
            location=LocationSourceIdentity('synthetic-s7', 51),
            native_version=0,
            fence=4,
            namespace_epoch=UUID(int=1),
        )
        result = placement_state_from_expectation(source)
        self.assertEqual(
            (result.identity.entity_kind, result.identity.aspect),
            ('machine', 'placement'),
        )
        self.assertEqual(result.revision, 4)
        self.assertEqual(
            (
                result.placement.client_id,
                result.placement.location_id,
                result.placement.native_version,
            ),
            (11, 51, 0),
        )
        self.assertFalse(hasattr(result, 'event_id'))
        malformed = replace(source)
        object.__setattr__(malformed.machine, 'pk', True)
        with self.assertRaises(ValueError):
            placement_state_from_expectation(malformed)

    def test_source_row_is_detached_and_preserves_unassigned_nulls(self):
        """No mutable source JSON or inferred client survives adaptation."""
        raw = self.record()
        result = self.parse(raw)
        raw['current']['client_id'] = 99
        self.assertEqual(result.current.client_id, 11)
        self.assertEqual(result.identity.source_id, 41)
        self.assertEqual(result.event_id, 7)
        self.assertEqual(result.operation, 'created')
        result = self.parse(
            self.record(
                current={'client_id': None, 'location_id': None, 'native_version': 0}
            )
        )
        self.assertIsNone(result.current.client_id)
        self.assertIsNone(result.current.location_id)

    def test_row_namespace_epoch_type_and_snapshot_shapes_are_exact(self):
        """Foreign rows, unsupported aspects and malformed source facts refuse."""
        cases = [
            self.record(namespace_id=9),
            self.record(epoch=UUID(int=2)),
            self.record(source_id=True),
            self.record(source_id='41'),
            self.record(revision=True),
            self.record(revision=0),
            self.record(id=True),
            self.record(entity_kind='document'),
            self.record(aspect='record'),
            self.record(operation='changed', previous=None),
            self.record(operation='deleted'),
            self.record(operation='unknown'),
            self.record(current={'client_id': 11, 'location_id': 51}),
            self.record(
                current={'client_id': True, 'location_id': None, 'native_version': 0}
            ),
            self.record(
                current={'client_id': 11, 'location_id': None, 'native_version': -1}
            ),
            self.record(
                current={'client_id': 11, 'location_id': None, 'native_version': False}
            ),
        ]
        missing = self.record()
        del missing['revision']
        cases.append(missing)
        for index, value in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.parse(value)

    def test_changed_and_deleted_keep_private_source_outcomes_distinct(self):
        """An internal captured delete is not a public tombstone or erasure."""
        before = self.record()['current']
        moved = self.parse(
            self.record(
                id=8,
                revision=2,
                operation='changed',
                previous=before,
                current={'client_id': 12, 'location_id': None, 'native_version': 1},
            )
        )
        removed = self.parse(
            self.record(
                id=9,
                revision=3,
                operation='deleted',
                previous={'client_id': 12, 'location_id': None, 'native_version': 1},
                current=None,
            )
        )
        self.assertEqual(moved.previous.client_id, 11)
        self.assertEqual(moved.current.client_id, 12)
        self.assertEqual(removed.operation, 'deleted')
        self.assertIsNone(removed.current)
        self.assertFalse(removed.deletion_applied)
        self.assertEqual(removed.tombstones, ())
        self.assertFalse(removed.removal_complete)

    def test_duplicate_stale_gap_conflict_and_identity_reuse_do_not_advance(self):
        """Event IDs are identities, not transaction commit order watermarks."""
        from integration_state.s7_changes import assess_placement_change

        first = self.parse(self.record(id=90))
        moved = self.parse(
            self.record(
                id=7,
                revision=2,
                operation='changed',
                previous=self.record()['current'],
                current={'client_id': 12, 'location_id': None, 'native_version': 1},
            )
        )
        accepted = assess_placement_change(first, moved)
        self.assertEqual(accepted.state, 'accepted')
        self.assertEqual(accepted.checkpoint, moved)
        self.assertEqual(assess_placement_change(first, first).state, 'duplicate')
        self.assertEqual(assess_placement_change(moved, first).state, 'stale')
        self.assertEqual(
            assess_placement_change(first, replace(moved, revision=3)).state, 'gap'
        )
        self.assertEqual(
            assess_placement_change(first, replace(moved, event_id=90)).state,
            'conflict',
        )
        self.assertEqual(
            assess_placement_change(first, replace(first, event_id=91)).state,
            'conflict',
        )
        deleted = self.parse(
            self.record(
                id=8,
                revision=3,
                operation='deleted',
                previous={'client_id': 12, 'location_id': None, 'native_version': 1},
                current=None,
            )
        )
        self.assertEqual(assess_placement_change(moved, deleted).state, 'accepted')
        reused = self.parse(self.record(id=9, revision=4))
        rejected = assess_placement_change(deleted, reused)
        self.assertEqual(rejected.state, 'identity_reuse')
        self.assertEqual(rejected.checkpoint, deleted)
        self.assertTrue(rejected.reconciliation_required)
        self.assertEqual(assess_placement_change(None, moved).state, 'gap')
        self.assertEqual(assess_placement_change(None, first).state, 'accepted')

    def test_foreign_identity_and_broken_tuple_continuity_refuse(self):
        """Equal numeric IDs do not bridge environments, aspects or epochs."""
        from integration_state.s7_changes import assess_placement_change

        first = self.parse(self.record())
        foreign = replace(
            first, identity=replace(first.identity, environment='foreign')
        )
        with self.assertRaises(ValueError):
            assess_placement_change(first, foreign)
        broken = self.parse(
            self.record(
                id=8,
                revision=2,
                operation='changed',
                previous={'client_id': 99, 'location_id': None, 'native_version': 0},
                current={'client_id': 12, 'location_id': None, 'native_version': 1},
            )
        )
        assessment = assess_placement_change(first, broken)
        self.assertEqual(assessment.state, 'conflict')
        self.assertEqual(assessment.checkpoint, first)

    def test_client_withdrawal_never_contains_destination_coordinates(self):
        """Trusted internal invalidation still needs current per-client authority."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_changes import invalidation_for_client

        change = self.parse(
            self.record(
                id=8,
                revision=2,
                operation='changed',
                previous=self.record()['current'],
                current={'client_id': 12, 'location_id': 99, 'native_version': 1},
            )
        )
        calls = []

        def permit(identity, client):
            calls.append((identity, client))
            return True

        result = invalidation_for_client(
            change, ClientSourceIdentity('synthetic-s7', 11), authorize=permit
        )
        self.assertEqual(result.reason, 'withdraw')
        self.assertEqual(result.client.pk, 11)
        self.assertFalse(hasattr(result, 'previous'))
        self.assertFalse(hasattr(result, 'current'))
        self.assertFalse(hasattr(result, 'location_id'))
        self.assertEqual(len(calls), 2)
        result = invalidation_for_client(
            change, ClientSourceIdentity('synthetic-s7', 12), authorize=permit
        )
        self.assertEqual(result.reason, 'refresh')
        self.assertIsNone(
            invalidation_for_client(
                change, ClientSourceIdentity('synthetic-s7', 13), authorize=permit
            )
        )
        with self.assertRaises(PermissionError):
            invalidation_for_client(
                change,
                ClientSourceIdentity('synthetic-s7', 11),
                authorize=lambda *_: False,
            )
        checks = iter([True, False])
        with self.assertRaises(PermissionError):
            invalidation_for_client(
                change,
                ClientSourceIdentity('synthetic-s7', 11),
                authorize=lambda *_: next(checks),
            )

    def test_assessment_cannot_claim_unknown_or_missing_checkpoint_success(self):
        """A structured decision must not admit invented states or fake success."""
        from integration_state.s7_changes import ChangeAssessment

        for state in ('unknown', 'accepted', 'duplicate', 'stale', 'identity_reuse'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                ChangeAssessment(state=state, checkpoint=None)
        self.assertTrue(
            ChangeAssessment(state='gap', checkpoint=None).reconciliation_required
        )

    def test_invalidation_constructor_rejects_cross_client_coordinate_aliases(self):
        """The private output contract validates its members independently."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state.s7_changes import ClientInvalidation

        values = {
            'identity': self.parse(self.record()).identity,
            'client': ClientSourceIdentity('synthetic-s7', 11),
            'revision': 1,
            'reason': 'refresh',
        }
        for field, value in (
            ('client', ClientSourceIdentity('foreign', 11)),
            ('revision', True),
            ('revision', 0),
            ('reason', 'deleted'),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ClientInvalidation(**{**values, field: value})


class SharedChangeTests(unittest.TestCase):
    """Consume actual S5 F01 carriers without promoting producer claims."""

    @staticmethod
    def record(**changes):
        """Construct the actual DTO from independent synthetic metadata truth."""
        from integration_state.shared_contracts import (
            SharedChange,
            SharedSourceKey,
            SourceObservation,
        )

        return SharedChange(**{
            'change_id': 101,
            'key': SharedSourceKey(
                namespace_id=3,
                environment='synthetic-s7',
                epoch=UUID(int=1),
                entity_kind='synthetic_document',
                aspect='metadata',
                source_id='private-object-007',
                declared_incarnation='declared-only-a',
            ),
            'fence': 1,
            'observation': SourceObservation(
                state='available',
                source_revision=None,
                client_ids=None,
                content_digest='a' * 64,
            ),
            'previous': None,
            **changes,
        })

    @staticmethod
    def parse(record, **binding):
        """Supply explicit trusted namespace metadata, not a fake authorizer."""
        from integration_state.s7_changes import shared_change_from_record

        return shared_change_from_record(**{
            'environment': 'synthetic-s7',
            'namespace_id': 3,
            'epoch': UUID(int=1),
            'record': record,
            **binding,
        })

    def test_actual_carrier_is_detached_without_inventing_source_proof(self):
        """Unknown revision/owners and declared incarnation remain metadata."""
        raw = self.record()
        result = self.parse(raw)
        self.assertEqual(result.key, raw.key)
        self.assertIsNot(result.key, raw.key)
        self.assertEqual((result.event_id, result.fence), (101, 1))
        self.assertEqual(result.observation, raw.observation)
        self.assertIsNot(result.observation, raw.observation)
        self.assertIsNone(result.observation.source_revision)
        self.assertIsNone(result.observation.client_ids)
        self.assertIsNone(result.previous)
        self.assertEqual(result.key.declared_incarnation, 'declared-only-a')
        for name in (
            'native_authorized',
            'source_deleted',
            'source_population_complete',
            'deletion_applied',
        ):
            assert_literal_bool(self, getattr(result, name), False)
        self.assertNotIn('private-object-007', repr(result))
        self.assertNotIn('a' * 64, repr(result))

    def test_shared_output_constructor_independently_validates_store_shape(self):
        """A typed-looking return cannot invent fences or native placement."""
        from integration_state.s7_changes import SharedChangeObservation

        raw = self.record()
        values = {
            'key': raw.key,
            'event_id': raw.change_id,
            'fence': raw.fence,
            'observation': raw.observation,
            'previous': raw.previous,
        }
        malformed = (
            ('event_id', True),
            ('event_id', 0),
            ('event_id', 2**63),
            ('fence', True),
            ('fence', 0),
            ('fence', 2**63),
            ('fence', 2),
            ('previous', raw.observation),
            ('key', object()),
            ('observation', object()),
            ('previous', object()),
            ('key', replace(raw.key, entity_kind='machine', aspect='placement')),
        )
        for member, value in malformed:
            with self.subTest(member=member, value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    SharedChangeObservation(**{**values, member: value})

    def test_foreign_record_is_refused_before_reading_its_properties(self):
        """Constructor rejection must not happen after a foreign property read."""
        raw = self.record()
        accessed = []

        class ForeignRecord:
            change_id = raw.change_id
            fence = raw.fence
            observation = raw.observation
            previous = raw.previous

            @property
            def key(self):
                accessed.append('key')
                return raw.key

        refused = False
        try:
            self.parse(ForeignRecord())
        except ValueError:
            refused = True
        self.assertEqual(accessed, [])
        self.assertTrue(refused)

    def test_fences_keep_replay_stale_gap_and_conflict_private(self):
        """Global event identities cannot replace one exact key's store fence."""
        from integration_state.s7_changes import assess_shared_change
        from integration_state.shared_contracts import SourceObservation

        first = self.parse(self.record())
        missing = SourceObservation(
            state='missing', source_revision=None, client_ids=None, content_digest=None
        )
        second = self.parse(
            self.record(
                change_id=7, fence=2, previous=first.observation, observation=missing
            )
        )
        self.assertIs(assess_shared_change(None, first).checkpoint, first)
        self.assertIs(assess_shared_change(first, second).checkpoint, second)
        self.assertEqual(assess_shared_change(first, second).state, 'accepted')
        initial_gap = assess_shared_change(None, second)
        self.assertEqual(initial_gap.state, 'gap')
        self.assertIsNone(initial_gap.checkpoint)
        cases = (
            (replace(second), 'duplicate'),
            (first, 'stale'),
            (replace(second, observation=first.observation), 'conflict'),
            (replace(second, event_id=33), 'conflict'),
            (replace(second, event_id=33, fence=4), 'gap'),
            (
                replace(second, event_id=33, fence=3, previous=first.observation),
                'conflict',
            ),
        )
        for incoming, state in cases:
            with self.subTest(state=state):
                result = assess_shared_change(second, incoming)
                self.assertEqual(result.state, state)
                self.assertIs(result.checkpoint, second)
                self.assertEqual(
                    result.reconciliation_required, state in ('gap', 'conflict')
                )
        assert_literal_bool(self, second.source_deleted, False)
        assert_literal_bool(self, second.source_population_complete, False)

    def test_namespace_type_and_declared_incarnation_are_not_collapsed(self):
        """A supplied binding and every qualified literal remain independent."""
        raw = self.record()
        for source_id in (7, '7', '007', ' 007 '):
            with self.subTest(
                source_type=type(source_id).__name__, source_id=source_id
            ):
                result = self.parse(
                    replace(raw, key=replace(raw.key, source_id=source_id))
                )
                self.assertIs(type(result.key.source_id), type(source_id))
                self.assertEqual(result.key.source_id, source_id)
        for member, value in (
            ('environment', 'different'),
            ('namespace_id', 4),
            ('namespace_id', True),
            ('epoch', UUID(int=2)),
        ):
            with self.subTest(member=member), self.assertRaises(ValueError):
                self.parse(raw, **{member: value})

    def test_nonavailable_states_and_source_revision_claims_stay_observations(self):
        """Stored state and claimed attribution never become native removal."""
        from integration_state.shared_contracts import SourceObservation

        for state in ('available', 'missing', 'denied', 'incomplete', 'unavailable'):
            for revision in (None, 0, '0'):
                with self.subTest(state=state, revision_type=type(revision).__name__):
                    observation = SourceObservation(
                        state=state, source_revision=revision, client_ids=(11,)
                    )
                    result = self.parse(self.record(observation=observation))
                    self.assertEqual(result.observation.state, state)
                    self.assertIs(
                        type(result.observation.source_revision), type(revision)
                    )
                    self.assertEqual(result.observation.source_revision, revision)
                    self.assertEqual(result.observation.client_ids, (11,))
                    assert_literal_bool(self, result.native_authorized, False)
                    assert_literal_bool(self, result.source_deleted, False)
                    assert_literal_bool(self, result.source_population_complete, False)
                    assert_literal_bool(self, result.deletion_applied, False)

    def test_assessment_constructor_cannot_invent_success_or_states(self):
        """Private continuity outputs validate independently of the algorithm."""
        from integration_state.s7_changes import SharedChangeAssessment

        class EqualState(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        checkpoint = self.parse(self.record())
        for state in ('unknown', 'identity_reuse', True, EqualState('forged')):
            with self.subTest(state_type=type(state).__name__):
                with self.assertRaises(ValueError):
                    SharedChangeAssessment(
                        state=cast(Any, state), checkpoint=checkpoint
                    )
        for state in ('accepted', 'duplicate', 'stale'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                SharedChangeAssessment(state=state, checkpoint=None)
        with self.assertRaises(ValueError):
            SharedChangeAssessment(state='accepted', checkpoint=cast(Any, object()))
        for state in ('gap', 'conflict'):
            self.assertTrue(
                SharedChangeAssessment(
                    state=state, checkpoint=None
                ).reconciliation_required
            )

    def test_every_key_dimension_refuses_cross_binding_continuity(self):
        """Equal numeric spelling or a claimed incarnation is not one key."""
        from integration_state.s7_changes import assess_shared_change

        raw = self.record()
        key = replace(raw.key, source_id=7, declared_incarnation=None)
        previous = self.parse(replace(raw, key=key))
        variants = (
            {'source_id': '7'},
            {'declared_incarnation': 'declared-only-a'},
            {'namespace_id': 4},
            {'environment': 'different'},
            {'epoch': UUID(int=2)},
            {'entity_kind': 'synthetic_attachment'},
            {'aspect': 'content_digest'},
        )
        for changes in variants:
            with self.subTest(changes=changes):
                other = replace(key, **changes)
                incoming = self.parse(
                    replace(raw, key=other),
                    namespace_id=other.namespace_id,
                    environment=other.environment,
                    epoch=other.epoch,
                )
                with self.assertRaises(ValueError):
                    assess_shared_change(previous, incoming)

    def test_nested_foreign_metadata_and_corrupted_proof_flags_refuse(self):
        """Every carrier is admitted before cloning or reading its members."""
        from integration_state.s7_changes import assess_shared_change
        from integration_state.shared_contracts import SourceObservation

        raw = self.record()
        accessed = []

        class ForeignObservation:
            __dataclass_fields__ = SourceObservation.__dataclass_fields__
            source_revision = None
            client_ids = None
            content_digest = None
            coverage_complete = False
            source_deleted = False

            def __init__(self, **_values):
                pass

            @property
            def state(self):
                accessed.append('state')
                return 'available'

        for member in ('observation', 'previous'):
            with self.subTest(member=member), self.assertRaises(ValueError):
                self.parse(replace(raw, **{member: ForeignObservation()}))
            self.assertEqual(accessed, [])
        for member in (
            'native_authorized',
            'source_deleted',
            'source_population_complete',
            'deletion_applied',
        ):
            corrupted = self.parse(self.record())
            object.__setattr__(corrupted, member, True)
            with self.subTest(member=member), self.assertRaises(ValueError):
                assess_shared_change(None, corrupted)

    def test_actual_producer_schema_version_is_explicitly_admitted(self):
        """A future or spoofed schema cannot silently reuse this v1 consumer."""
        from unittest.mock import patch

        from integration_state import shared_contracts
        from integration_state.s7_changes import SharedChangeObservation

        class EqualSchema(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        raw = self.record()
        values = {
            'key': raw.key,
            'event_id': raw.change_id,
            'fence': raw.fence,
            'observation': raw.observation,
            'previous': raw.previous,
        }
        for version in ('aimms.shared-state.v2', None, True, EqualSchema('forged')):
            with self.subTest(version_type=type(version).__name__):
                with patch.object(shared_contracts, 'SHARED_SCHEMA', version):
                    with self.assertRaises(ValueError):
                        self.parse(raw)
                    with self.assertRaises(ValueError):
                        SharedChangeObservation(**values)


class DeliveredChangeTests(unittest.TestCase):
    """Actual S5 claim types and explicit producer-shaped private JSON."""

    @staticmethod
    def carriers(*, native=False, **payload_changes):
        """Keep source fixture truth independent of the S7 decoded result."""
        import hashlib
        import json

        from integration_state.shared_contracts import (
            ConsumerConfiguration,
            EventClaim,
            SharedSourceKey,
            SourceObservation,
            key_payload,
            observation_payload,
        )

        key = SharedSourceKey(
            namespace_id=3,
            environment='synthetic-s7',
            epoch=UUID(int=1),
            entity_kind='machine' if native else 'synthetic_document',
            aspect='placement' if native else 'metadata',
            source_id=41 if native else 'private-7',
        )
        body = {
            'schema': 'aimms.shared-event.v1',
            'change_id': 101,
            'key': key_payload(key),
            'revision': 1,
            'operation': 'created' if native else 'observed',
            'previous': None,
            'current': {'client_id': 11, 'location_id': 51, 'native_version': 0}
            if native
            else observation_payload(SourceObservation(state='available')),
            'captured_at': '2026-01-01T01:00:00+01:00',
            **payload_changes,
        }
        encoded = json.dumps(
            body,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            allow_nan=False,
        )
        configuration = ConsumerConfiguration(
            consumer_id=2,
            namespace_id=3,
            namespace_epoch=UUID(int=1),
            configuration_epoch=UUID(int=2),
            consumer_key='private-consumer',
            aspects=(('machine', 'placement'), ('synthetic_document', 'metadata')),
        )
        claim = EventClaim(
            consumer_id=2,
            delivery_id=5,
            change_id=101,
            namespace_epoch=UUID(int=1),
            configuration_epoch=UUID(int=2),
            ownership_fence=1,
            lease_token=UUID(int=3),
            worker_key='private-worker',
            expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            event_payload=encoded,
            payload_digest=hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
        )
        return configuration, claim

    @staticmethod
    def parse(configuration, claim, **binding):
        """No database lookup or native authorizer is supplied by this decoder."""
        from integration_state.s7_changes import delivery_change_from_claim

        return delivery_change_from_claim(
            configuration=configuration,
            claim=claim,
            **{
                'environment': 'synthetic-s7',
                'worker_key': 'private-worker',
                **binding,
            },
        )

    def test_actual_generic_claim_detaches_without_current_ownership_proof(self):
        """Payload bytes and producer claims do not become a current lease grant."""
        from integration_state.s7_changes import SharedChangeObservation

        configuration, claim = self.carriers()
        result = self.parse(configuration, claim)
        self.assertIs(type(result.change), SharedChangeObservation)
        change = cast(SharedChangeObservation, result.change)
        self.assertEqual(change.key.source_id, 'private-7')
        self.assertIsNone(change.observation.source_revision)
        self.assertIsNone(change.observation.client_ids)
        self.assertEqual(change.event_id, 101)
        self.assertEqual(change.fence, 1)
        self.assertEqual(result.captured_at.isoformat(), '2026-01-01T01:00:00+01:00')
        self.assertIsNot(result.claim, claim)
        self.assertIsNot(result.configuration, configuration)
        for flag in (
            'ownership_verified',
            'native_authorized',
            'population_complete',
            'removal_complete',
            'deletion_applied',
            'erasure_complete',
        ):
            assert_literal_bool(self, getattr(result, flag), False)
        self.assertEqual(result.tombstones, ())
        self.assertNotIn('private-7', repr(result))
        self.assertNotIn('private-worker', repr(result))

    def test_native_claim_retains_actual_placement_branch_and_clock(self):
        """Captured native data is not rewritten into a generic observed record."""
        from integration_state.s7_changes import PlacementChange

        configuration, claim = self.carriers(native=True)
        result = self.parse(configuration, claim)
        self.assertIs(type(result.change), PlacementChange)
        change = cast(PlacementChange, result.change)
        self.assertEqual(change.identity.source_id, 41)
        self.assertEqual(change.namespace_id, 3)
        self.assertEqual(change.operation, 'created')
        self.assertEqual(change.revision, 1)
        assert change.current is not None
        self.assertEqual(
            (
                change.current.client_id,
                change.current.location_id,
                change.current.native_version,
            ),
            (11, 51, 0),
        )
        self.assertEqual(change.captured_at.isoformat(), '2026-01-01T01:00:00+01:00')
        self.assertEqual(result.captured_at.isoformat(), change.captured_at.isoformat())
        self.assertFalse(result.ownership_verified)
        self.assertFalse(result.native_authorized)
        self.assertEqual(result.tombstones, ())

    @staticmethod
    def repayload(claim, body):
        """Bind changed fixture bytes honestly; a digest is not stored ownership."""
        import hashlib
        import json

        encoded = (
            body
            if type(body) is str
            else json.dumps(
                body,
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return replace(
            claim,
            event_payload=encoded,
            payload_digest=hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
        )

    def test_wire_schema_field_sets_type_tag_and_event_id_are_exact(self):
        """A fresh digest cannot admit another protocol or dropped provenance."""
        import json

        configuration, claim = self.carriers()
        body = json.loads(claim.event_payload)
        variants = (
            {**body, 'schema': 'aimms.shared-event.v2'},
            {**body, 'private_extra': 'not-an-approved-field'},
            {**body, 'change_id': 102},
            {**body, 'operation': 'deleted'},
            {**body, 'key': {**body['key'], 'schema': 'aimms.shared-state.v2'}},
            {**body, 'key': {**body['key'], 'key_type': 'integer'}},
            {**body, 'key': {**body['key'], 'private_extra': True}},
            {**body, 'captured_at': '2026-01-01T00:00:00Z'},
        )
        for ordinal, value in enumerate(variants):
            with self.subTest(ordinal=ordinal), self.assertRaises(ValueError):
                self.parse(configuration, self.repayload(claim, value))

    def test_supported_aspect_membership_and_literal_json_are_not_inferred(self):
        """Default JSON duplicate handling or whitespace is not producer v1."""
        configuration, claim = self.carriers()
        wrong_aspects = replace(configuration, aspects=(('machine', 'placement'),))
        with self.assertRaises(ValueError):
            self.parse(wrong_aspects, claim)
        for encoded in (
            claim.event_payload + ' ',
            claim.event_payload[:-1] + ',"schema":"aimms.shared-event.v1"}',
        ):
            with (
                self.subTest(encoded_suffix=encoded[-8:]),
                self.assertRaises(ValueError),
            ):
                self.parse(configuration, self.repayload(claim, encoded))

    def test_delivered_output_constructor_independently_binds_received_claim(self):
        """A constructor cannot certify a self-inconsistent or foreign result."""
        from integration_state.s7_changes import DeliveredChangeObservation

        configuration, claim = self.carriers()
        good = self.parse(configuration, claim)
        values = {
            'environment': good.environment,
            'worker_key': good.worker_key,
            'configuration': good.configuration,
            'claim': good.claim,
            'change': good.change,
            'captured_at': good.captured_at,
        }
        for member, value in (
            ('environment', 'foreign'),
            ('worker_key', 'other-worker'),
            ('configuration', replace(configuration, consumer_id=99)),
            ('claim', object()),
            ('change', object()),
            ('captured_at', True),
            ('captured_at', datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ):
            with self.subTest(member=member), self.assertRaises(ValueError):
                DeliveredChangeObservation(**{**values, member: value})

    def test_foreign_config_and_claim_refuse_before_member_access(self):
        """Real carriers are required even if foreign properties look valid."""
        configuration, claim = self.carriers()
        accessed = []

        class ForeignClaim:
            @property
            def consumer_id(self):
                accessed.append('consumer_id')
                return 2

        for config, incoming in ((object(), claim), (configuration, ForeignClaim())):
            with (
                self.subTest(config_type=type(config).__name__),
                self.assertRaises(ValueError),
            ):
                self.parse(config, incoming)
            self.assertEqual(accessed, [])

    def test_returned_constructor_value_stays_bound_to_original_intake(self):
        """A valid but different constructor result is not the validated claim."""
        import json
        from unittest.mock import patch

        from integration_state import s7_changes

        configuration, claim = self.carriers()
        actual_type = s7_changes.DeliveredChangeObservation
        expected = self.parse(configuration, claim)
        changed_body = {**json.loads(claim.event_payload), 'change_id': 102}
        changed_claim = replace(self.repayload(claim, changed_body), change_id=102)
        foreign_result = self.parse(configuration, changed_claim)
        activated = []

        def construct(*, substitute=False, **values):
            legitimate = actual_type(**values)
            activated.append(substitute)
            return foreign_result if substitute else legitimate

        with patch.object(
            s7_changes, 'DeliveredChangeObservation', side_effect=construct
        ):
            self.assertEqual(self.parse(configuration, claim), expected)
        self.assertEqual(activated, [False])
        activated.clear()
        with (
            patch.object(
                s7_changes,
                'DeliveredChangeObservation',
                side_effect=lambda **values: construct(substitute=True, **values),
            ),
            self.assertRaises(ValueError),
        ):
            self.parse(configuration, claim)
        self.assertEqual(activated, [True])

    def test_caller_coordinates_and_actual_schema_marker_are_admitted(self):
        """Exact DTO classes do not admit a changed producer or another binding."""
        from unittest.mock import patch

        from integration_state import shared_contracts

        configuration, claim = self.carriers()
        for config, incoming, binding in (
            (configuration, claim, {'environment': 'foreign'}),
            (configuration, claim, {'worker_key': 'other-worker'}),
            (replace(configuration, consumer_id=99), claim, {}),
            (replace(configuration, namespace_id=99), claim, {}),
            (replace(configuration, namespace_epoch=UUID(int=9)), claim, {}),
            (configuration, replace(claim, configuration_epoch=UUID(int=9)), {}),
        ):
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                self.parse(config, incoming, **binding)

        class EqualSchema(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        for marker in (None, 'aimms.shared-state.v2', EqualSchema('foreign')):
            with (
                self.subTest(marker_type=type(marker).__name__),
                patch.object(shared_contracts, 'SHARED_SCHEMA', marker),
                self.assertRaises(ValueError),
            ):
                self.parse(configuration, claim)

    def test_generic_unknowns_typed_ids_and_previous_observation_survive(self):
        """Current missing evidence stays an observation with no tombstone."""
        import json

        from integration_state.s7_changes import SharedChangeObservation
        from integration_state.shared_contracts import (
            SourceObservation,
            observation_payload,
        )

        configuration, original = self.carriers()
        body = json.loads(original.event_payload)
        previous = SourceObservation(state='available', source_revision='0')
        current = SourceObservation(state='missing', source_revision=None)
        for source_id in (7, '7', '007'):
            incoming = self.repayload(
                original,
                {
                    **body,
                    'key': {
                        **body['key'],
                        'source_id': source_id,
                        'key_type': 'integer' if type(source_id) is int else 'string',
                        'declared_incarnation': 'producer-claim-only',
                    },
                    'revision': 2,
                    'previous': observation_payload(previous),
                    'current': observation_payload(current),
                },
            )
            result = self.parse(configuration, incoming)
            self.assertIs(type(result.change), SharedChangeObservation)
            change = cast(SharedChangeObservation, result.change)
            self.assertEqual(change.key.source_id, source_id)
            self.assertIs(type(change.key.source_id), type(source_id))
            self.assertEqual(change.key.declared_incarnation, 'producer-claim-only')
            self.assertEqual(change.previous, previous)
            self.assertEqual(change.observation, current)
            self.assertFalse(change.source_deleted)
            self.assertEqual(result.tombstones, ())

    def test_nonobjects_malformed_depth_and_native_claim_labels_refuse(self):
        """Invalid metadata is not retried, repaired, or decoded as an empty success."""
        import json

        configuration, claim = self.carriers()
        for encoded in (
            '[]',
            '{}',
            '{',
            '{"schema":NaN}',
            '[' * 1200 + '0' + ']' * 1200,
        ):
            with self.subTest(length=len(encoded)), self.assertRaises(ValueError):
                self.parse(configuration, self.repayload(claim, encoded))
        configuration, native = self.carriers(native=True)
        body = json.loads(native.event_payload)
        for changed in (
            {
                **body,
                'key': {**body['key'], 'declared_incarnation': 'not-native-proof'},
            },
            {**body, 'revision': True},
            {**body, 'captured_at': False},
            {**body, 'key': {**body['key'], 'source_id': '41', 'key_type': 'string'}},
        ):
            with (
                self.subTest(changed_keys=tuple(changed)),
                self.assertRaises(ValueError),
            ):
                self.parse(configuration, self.repayload(native, changed))

    def test_expired_metadata_and_corrupt_flags_never_imply_ownership(self):
        """Decoding an expired detached claim is not authorizing its acknowledgement."""
        configuration, claim = self.carriers()
        old = replace(claim, expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        result = self.parse(configuration, old)
        self.assertFalse(result.ownership_verified)
        for name in (
            'ownership_verified',
            'native_authorized',
            'population_complete',
            'removal_complete',
            'deletion_applied',
            'erasure_complete',
        ):
            with self.subTest(flag=name):
                object.__setattr__(result, name, True)
                try:
                    with self.assertRaises(ValueError):
                        result.__post_init__()
                finally:
                    object.__setattr__(result, name, False)
        result.__post_init__()


class S3ContractMappingTests(unittest.TestCase):
    """Qualify private vocabulary, not an S3 reader, producer or native policy."""

    OPERATIONS = (
        ('work.core', 'work', 'aimms.work.core_details.v1'),
        ('maintenance.direct', 'maintenance', 'aimms.maintenance.direct_details.v1'),
        ('work_document.metadata', 'attachment', 'aimms.document.metadata.v1'),
        ('machine_document.metadata', 'attachment', 'aimms.document.metadata.v1'),
        ('work_document.content', 'attachment', 'aimms.document.content.v1'),
        ('machine_document.content', 'attachment', 'aimms.document.content.v1'),
    )

    @classmethod
    def carriers(
        cls, operation, *, source_id: int | str = 7, state='available', **observation
    ):
        """Use independent private fixtures; never claim native S3 provenance."""
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
            key_payload,
            observation_payload,
        )

        entity = next(kind for name, kind, _ in cls.OPERATIONS if name == operation)
        key = SharedSourceKey(
            namespace_id=3,
            environment='synthetic-s7',
            epoch=UUID(int=1),
            entity_kind=entity,
            aspect=operation,
            source_id=source_id,
            declared_incarnation=None,
        )
        observed = SourceObservation(**{
            'state': state,
            'source_revision': None,
            'client_ids': None,
            'content_digest': None,
            **observation,
        })
        configuration, claim = DeliveredChangeTests.carriers(
            key=key_payload(key), current=observation_payload(observed)
        )
        return (
            replace(
                configuration,
                aspects=tuple((kind, name) for name, kind, _ in cls.OPERATIONS),
            ),
            claim,
        )

    def assert_unprivileged(self, delivered):
        """Compatible data never certifies native authority, source order or effects."""
        for name in (
            'ownership_verified',
            'native_authorized',
            'population_complete',
            'removal_complete',
            'deletion_applied',
            'erasure_complete',
        ):
            assert_literal_bool(self, getattr(delivered, name), False)
        self.assertEqual(delivered.tombstones, ())

    def test_six_proposed_aspects_preserve_native_integer_identity_and_unknowns(self):
        """Equal numeric IDs retain six distinct private aspect coordinates."""
        from integration_state.s7_changes import SharedChangeObservation

        keys = set()
        for operation, entity, _ in self.OPERATIONS:
            with self.subTest(operation=operation):
                result = DeliveredChangeTests.parse(*self.carriers(operation))
                self.assertIs(type(result.change), SharedChangeObservation)
                change = cast(SharedChangeObservation, result.change)
                self.assertEqual(
                    (change.key.entity_kind, change.key.aspect), (entity, operation)
                )
                self.assertIs(type(change.key.source_id), int)
                self.assertEqual(change.key.source_id, 7)
                self.assertEqual(change.key.environment, 'synthetic-s7')
                self.assertEqual(change.key.namespace_id, 3)
                self.assertEqual(change.key.epoch, UUID(int=1))
                self.assertIsNone(change.key.declared_incarnation)
                self.assertEqual(change.observation.state, 'available')
                self.assertIsNone(change.observation.source_revision)
                self.assertIsNone(change.observation.client_ids)
                self.assertIsNone(change.observation.content_digest)
                self.assertFalse(change.observation.coverage_complete)
                self.assertFalse(change.observation.source_deleted)
                self.assertEqual(change.fence, 1)
                self.assertEqual(change.event_id, 101)
                self.assert_unprivileged(result)
                keys.add(change.key)
        self.assertEqual(len(keys), len(self.OPERATIONS))

    def test_s3_unavailability_can_stay_nondisclosing_without_missing_or_deletion(self):
        """An explicitly admitted unavailable disposition remains just that."""
        from integration_state.s7_changes import SharedChangeObservation

        for operation, _, _ in self.OPERATIONS:
            with self.subTest(operation=operation):
                result = DeliveredChangeTests.parse(
                    *self.carriers(operation, state='unavailable')
                )
                change = cast(SharedChangeObservation, result.change)
                self.assertEqual(change.observation.state, 'unavailable')
                self.assertIsNone(change.observation.client_ids)
                self.assertIsNone(change.observation.content_digest)
                self.assertFalse(change.source_deleted)
                self.assert_unprivileged(result)

    def test_each_aspect_still_requires_exact_configuration_namespace_and_worker(self):
        """A proposed operation name never admits another private binding."""
        for operation, _, _ in self.OPERATIONS:
            configuration, claim = self.carriers(operation)
            for altered in (
                replace(configuration, namespace_id=9),
                replace(configuration, namespace_epoch=UUID(int=9)),
                replace(configuration, configuration_epoch=UUID(int=9)),
                replace(configuration, aspects=(('machine', 'placement'),)),
            ):
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    DeliveredChangeTests.parse(altered, claim)
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                DeliveredChangeTests.parse(configuration, claim, worker_key='foreign')

    def test_generic_key_space_does_not_validate_s3_native_integer_provenance(self):
        """Characterize the narrower producer obligation, not a permissive adapter."""
        from integration_state.s7_changes import SharedChangeObservation

        for operation, _, _ in self.OPERATIONS:
            keys = []
            for source_id in (7, '7'):
                result = DeliveredChangeTests.parse(
                    *self.carriers(operation, source_id=source_id)
                )
                change = cast(SharedChangeObservation, result.change)
                self.assertIs(type(change.key.source_id), type(source_id))
                self.assertEqual(change.key.source_id, source_id)
                self.assert_unprivileged(result)
                keys.append(change.key)
            self.assertNotEqual(*keys)
            for invalid in (True, 0, 1 << 63):
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    self.carriers(operation, source_id=invalid)

    def test_generic_observation_states_are_not_an_s3_outcome_translator(self):
        """A future S3 producer must not invent missing or denied source facts."""
        from integration_state.s7_changes import SharedChangeObservation
        from integration_state.shared_contracts import SourceObservation

        for state in ('missing', 'denied', 'incomplete'):
            result = DeliveredChangeTests.parse(
                *self.carriers('work.core', state=state)
            )
            change = cast(SharedChangeObservation, result.change)
            self.assertEqual(change.observation.state, state)
            self.assert_unprivileged(result)
        with self.assertRaises(ValueError):
            SourceObservation(state='unsupported')

    def test_generic_digest_and_client_claims_are_not_historical_or_byte_proof(self):
        """Opaque metadata needs separate source admission and representation binding."""
        import hashlib

        from integration_state.s7_changes import SharedChangeObservation

        content_digest = hashlib.sha256(
            b'\x00synthetic-private-content\xff'
        ).hexdigest()
        wire_digest = hashlib.sha256(b'{"synthetic":"wire-representation"}').hexdigest()
        self.assertNotEqual(content_digest, wire_digest)
        for digest in (None, content_digest, wire_digest):
            for clients in (None, (), (11,)):
                result = DeliveredChangeTests.parse(
                    *self.carriers(
                        'work_document.content',
                        content_digest=digest,
                        client_ids=clients,
                    )
                )
                change = cast(SharedChangeObservation, result.change)
                self.assertEqual(change.observation.content_digest, digest)
                self.assertEqual(change.observation.client_ids, clients)
                self.assertIsNone(change.observation.source_revision)
                self.assertIsNone(change.key.declared_incarnation)
                self.assert_unprivileged(result)

    def test_generic_wire_cannot_silently_add_source_body_actor_or_record_schema(self):
        """Unknown source-specific fields need owner coordination, not a DTO dump."""
        import json

        configuration, claim = self.carriers('machine_document.metadata')
        body = json.loads(claim.event_payload)
        for member in ('payload', 'actor', 'record_schema', 'owner'):
            changed = {
                **body,
                'current': {**body['current'], member: 'synthetic-private-value'},
            }
            with self.subTest(member=member), self.assertRaises(ValueError):
                DeliveredChangeTests.parse(
                    configuration, DeliveredChangeTests.repayload(claim, changed)
                )
