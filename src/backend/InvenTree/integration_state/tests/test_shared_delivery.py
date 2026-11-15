"""Durable private event ownership, not a source authorization or IO client."""

import json
from uuid import uuid4

from django.test import TestCase


class SharedDeliveryTests(TestCase):
    """Each consumer configuration owns its own retained event dispositions."""

    def coordinates(self):
        """Create independently known committed-shape generic metadata."""
        from integration_state.models import SourceNamespace
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )
        from integration_state.shared_events import record_observation

        namespace = SourceNamespace.objects.create(
            environment='synthetic-delivery', active=True
        )
        key = SharedSourceKey(
            namespace_id=namespace.pk,
            environment=namespace.environment,
            epoch=namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id=7,
        )
        result = record_observation(
            key,
            event_key='first',
            expected_fence=0,
            observation=SourceObservation(
                state='available', source_revision='private-revision'
            ),
        )
        return namespace, key, result

    def test_each_configuration_durably_claims_and_acknowledges_the_same_event(self):
        """One consumer's acknowledgment cannot hide an event from another."""
        from integration_state.models import (
            ConsumerAspectProgress,
            EventConsumer,
            EventDelivery,
        )
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )

        namespace, key, event = self.coordinates()
        configuration = uuid4()
        consumer = configure_consumer(
            namespace.pk,
            'synthetic-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=configuration,
        )
        self.assertEqual(
            configure_consumer(
                namespace.pk,
                'synthetic-consumer',
                aspects=(('synthetic_document', 'metadata'),),
                configuration_epoch=configuration,
            ),
            consumer,
        )
        claims = claim_changes(
            consumer.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim.change_id, event.change_id)
        self.assertEqual(claim.configuration_epoch, configuration)
        self.assertEqual(claim.namespace_epoch, namespace.epoch)
        payload = json.loads(claim.event_payload)
        self.assertEqual(payload['key']['source_id'], key.source_id)
        self.assertEqual(payload['current']['source_revision'], 'private-revision')
        self.assertEqual(
            claim_changes(
                consumer.consumer_id,
                worker_key='another-worker',
                limit=1,
                lease_seconds=60,
            ),
            (),
        )
        receipt = ack_change(claim, outcome='applied')
        self.assertEqual(receipt.outcome, 'applied')
        self.assertTrue(receipt.advanced)
        self.assertFalse(receipt.deletion_applied)
        self.assertEqual(ack_change(claim, outcome='applied'), receipt)
        self.assertEqual(
            claim_changes(
                consumer.consumer_id,
                worker_key='private-worker',
                limit=1,
                lease_seconds=60,
            ),
            (),
        )
        other = configure_consumer(
            namespace.pk,
            'independent-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        second = claim_changes(
            other.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )
        self.assertEqual(second[0].change_id, event.change_id)
        self.assertEqual(EventConsumer.objects.count(), 2)
        self.assertEqual(EventDelivery.objects.count(), 2)
        self.assertEqual(ConsumerAspectProgress.objects.get().revision, 1)

    def test_native_placement_capture_uses_the_same_delivery_without_source_mutation(
        self,
    ):
        """Actual captured native rows survive delivery/ack without generic replacement."""
        from assets.models import AssetMachine, Client
        from integration_state.models import SourceChange, SourceState
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )

        namespace, _, _ = self.coordinates()
        owner = Client.objects.create(
            name='Synthetic delivery source', code='shared-delivery'
        )
        machine = AssetMachine.objects.create(
            name='Synthetic delivered placement', client=owner
        )
        native_event = SourceChange.objects.get(
            source_id=machine.pk, generic_identity__isnull=True
        )
        before = AssetMachine.objects.values(
            'client_id', 'physical_location_id', 'placement_version'
        ).get(pk=machine.pk)
        consumer = configure_consumer(
            namespace.pk,
            'placement-consumer',
            aspects=(('machine', 'placement'),),
            configuration_epoch=uuid4(),
        )
        claims = claim_changes(
            consumer.consumer_id, worker_key='native-worker', limit=5, lease_seconds=60
        )
        self.assertEqual(len(claims), 1)
        payload = json.loads(claims[0].event_payload)
        self.assertEqual(claims[0].change_id, native_event.pk)
        self.assertEqual(payload['key']['source_id'], machine.pk)
        self.assertIsNone(payload['key']['declared_incarnation'])
        self.assertEqual(
            payload['current'],
            {'client_id': owner.pk, 'location_id': None, 'native_version': 0},
        )
        self.assertEqual(ack_change(claims[0], outcome='applied').outcome, 'applied')
        self.assertEqual(
            AssetMachine.objects.values(*before).get(pk=machine.pk), before
        )
        self.assertEqual(SourceState.objects.get(source_id=machine.pk).revision, 1)
        self.assertEqual(SourceChange.objects.filter(source_id=machine.pk).count(), 1)

    def test_encoder_cannot_replace_the_validated_event_with_a_schema_valid_forgery(
        self,
    ):
        """Matching returned digest/type is not proof of the producer's actual bytes."""
        from unittest.mock import patch

        from integration_state import shared_delivery
        from integration_state.models import EventDelivery
        from integration_state.shared_events import SharedStateError

        namespace, _, _ = self.coordinates()
        consumer = shared_delivery.configure_consumer(
            namespace.pk,
            'encoding-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        original_encode = shared_delivery._encode
        activated = []

        def forged(payload):
            result = json.loads(original_encode(payload))
            result['current']['source_revision'] = 'forged-transit-revision'
            activated.append(True)
            return original_encode(result)

        with patch.object(shared_delivery, '_encode', side_effect=forged):
            with self.assertRaises(SharedStateError):
                shared_delivery.claim_changes(
                    consumer.consumer_id,
                    worker_key='private-worker',
                    limit=1,
                    lease_seconds=60,
                )
        self.assertEqual(activated, [True])
        self.assertFalse(EventDelivery.objects.exists())
        valid = shared_delivery.claim_changes(
            consumer.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )
        self.assertEqual(
            json.loads(valid[0].event_payload)['current']['source_revision'],
            'private-revision',
        )

    def test_expired_attempt_is_retained_and_old_token_cannot_acknowledge_reclaim(self):
        """Lease recovery adds history and fencing, never a false unsent claim."""
        from datetime import timedelta
        from unittest.mock import patch

        from django.utils import timezone

        from integration_state.models import EventDelivery
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )
        from integration_state.shared_events import SharedConflictError

        namespace, _, _ = self.coordinates()
        consumer = configure_consumer(
            namespace.pk,
            'lease-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        first = claim_changes(
            consumer.consumer_id, worker_key='old-worker', limit=1, lease_seconds=60
        )[0]
        future = first.expires_at + timedelta(seconds=1)
        with patch.object(timezone, 'now', return_value=future):
            with self.assertRaises(SharedConflictError):
                ack_change(first, outcome='applied')
            newer = claim_changes(
                consumer.consumer_id, worker_key='new-worker', limit=1, lease_seconds=60
            )[0]
            self.assertEqual(newer.ownership_fence, first.ownership_fence + 1)
            self.assertNotEqual(newer.lease_token, first.lease_token)
            with self.assertRaises(SharedConflictError):
                ack_change(first, outcome='applied')
            self.assertTrue(ack_change(newer, outcome='applied').advanced)
        self.assertEqual(EventDelivery.objects.count(), 2)
        self.assertEqual(
            EventDelivery.objects.get(pk=first.delivery_id).state, 'expired'
        )

    def test_configuration_replacement_and_namespace_epoch_cannot_adopt_old_claims(
        self,
    ):
        """A logical consumer name does not preserve old configuration authority."""
        from integration_state.models import EventConsumer
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )
        from integration_state.shared_events import (
            SharedConflictError,
            SharedStateError,
        )

        namespace, _, _ = self.coordinates()
        epoch = uuid4()
        old = configure_consumer(
            namespace.pk,
            'config-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=epoch,
        )
        claim = claim_changes(
            old.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )[0]
        with self.assertRaises(SharedConflictError):
            configure_consumer(
                namespace.pk,
                'config-consumer',
                aspects=(('synthetic_document', 'another-aspect'),),
                configuration_epoch=epoch,
            )
        new = configure_consumer(
            namespace.pk,
            'config-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        self.assertFalse(EventConsumer.objects.get(pk=old.consumer_id).active)
        with self.assertRaises(SharedStateError):
            ack_change(claim, outcome='applied')
        newer = claim_changes(
            new.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )[0]
        self.assertEqual(newer.change_id, claim.change_id)
        namespace.epoch = uuid4()
        namespace.save(update_fields=['epoch'])
        with self.assertRaises(SharedStateError):
            ack_change(newer, outcome='applied')

    def test_out_of_order_ack_and_uncertainty_never_regress_aspect_or_requeue_effect(
        self,
    ):
        """Per-event terminal dispositions are not a global prefix or serving proof."""
        from integration_state.models import ConsumerAspectProgress
        from integration_state.shared_contracts import SourceObservation
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )
        from integration_state.shared_events import (
            SharedConflictError,
            record_observation,
        )

        namespace, key, _ = self.coordinates()
        record_observation(
            key,
            event_key='second',
            expected_fence=1,
            observation=SourceObservation(state='missing'),
        )
        consumer = configure_consumer(
            namespace.pk,
            'order-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        earlier, later = claim_changes(
            consumer.consumer_id, worker_key='private-worker', limit=2, lease_seconds=60
        )
        self.assertTrue(ack_change(later, outcome='applied').advanced)
        stale = ack_change(earlier, outcome='applied')
        self.assertEqual(stale.outcome, 'superseded')
        self.assertFalse(stale.advanced)
        self.assertEqual(ConsumerAspectProgress.objects.get().revision, 2)
        self.assertEqual(ack_change(earlier, outcome='applied'), stale)
        with self.assertRaises(SharedConflictError):
            ack_change(earlier, outcome='failed')
        record_observation(
            key,
            event_key='uncertain',
            expected_fence=2,
            observation=SourceObservation(state='unavailable'),
        )
        uncertain = claim_changes(
            consumer.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )[0]
        self.assertFalse(ack_change(uncertain, outcome='uncertain').advanced)
        self.assertEqual(ConsumerAspectProgress.objects.get().revision, 2)
        self.assertEqual(
            claim_changes(
                consumer.consumer_id,
                worker_key='private-worker',
                limit=1,
                lease_seconds=60,
            ),
            (),
        )

    def test_exact_claim_coordinates_and_budget_types_are_refused_before_ack(self):
        """A valid digest or boolean alias cannot replace ownership/configuration."""
        from dataclasses import replace

        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )
        from integration_state.shared_events import SharedConflictError

        namespace, _, _ = self.coordinates()
        consumer = configure_consumer(
            namespace.pk,
            'exact-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        claim = claim_changes(
            consumer.consumer_id, worker_key='private-worker', limit=1, lease_seconds=60
        )[0]
        for values in (
            {'lease_token': uuid4()},
            {'configuration_epoch': uuid4()},
            {'namespace_epoch': uuid4()},
            {'ownership_fence': claim.ownership_fence + 1},
            {'worker_key': 'foreign-worker'},
            {'change_id': claim.change_id + 1},
        ):
            with self.subTest(values=tuple(values)):
                with self.assertRaises(SharedConflictError):
                    ack_change(replace(claim, **values), outcome='applied')
        for bad in (True, 0, -1, 101):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    claim_changes(
                        consumer.consumer_id,
                        worker_key='private-worker',
                        limit=bad,
                        lease_seconds=60,
                    )
        self.assertTrue(ack_change(claim, outcome='applied').advanced)

    def test_caller_transaction_cannot_export_an_uncommitted_ownership_lease(self):
        """An inner savepoint is not a committed reservation before caller IO."""
        from django.db import transaction

        from integration_state.models import EventDelivery
        from integration_state.shared_delivery import claim_changes, configure_consumer
        from integration_state.shared_events import SharedStateError

        namespace, _, _ = self.coordinates()
        consumer = configure_consumer(
            namespace.pk,
            'commit-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )
        with transaction.atomic():
            with self.assertRaises(SharedStateError):
                claim_changes(
                    consumer.consumer_id,
                    worker_key='private-worker',
                    limit=1,
                    lease_seconds=60,
                )
        self.assertFalse(EventDelivery.objects.exists())
        self.assertEqual(
            len(
                claim_changes(
                    consumer.consumer_id,
                    worker_key='private-worker',
                    limit=1,
                    lease_seconds=60,
                )
            ),
            1,
        )
