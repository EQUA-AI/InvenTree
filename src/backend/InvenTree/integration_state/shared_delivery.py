"""Cursor-free private event ownership; no source writes or transport IO."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from integration_state.models import (
    ConsumerAspectProgress,
    EventConsumer,
    EventDelivery,
    SourceChange,
)
from integration_state.shared_contracts import (
    ConsumerConfiguration,
    DeliveryAcknowledgement,
    EventClaim,
    SharedSourceKey,
    exact_uuid,
    integer,
    key_payload,
    literal,
    supported_aspects,
)
from integration_state.shared_events import (
    SharedConflictError,
    SharedStateError,
    change_record,
    fingerprint,
    require_namespace,
    shared_transaction,
)

MAX_CLAIM_BATCH = 100
MAX_LEASE_SECONDS = 3600
OUTCOMES = ('applied', 'superseded', 'failed', 'uncertain')


def _encode(payload):
    """Encode only explicit private primitive metadata, without mutable carriers."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        allow_nan=False,
    )


def _configuration_payload(namespace, consumer_key, aspects, configuration_epoch):
    return {
        'namespace_id': namespace.pk,
        'environment': namespace.environment,
        'namespace_epoch': str(namespace.epoch),
        'consumer_key': consumer_key,
        'aspects': [list(pair) for pair in aspects],
        'configuration_epoch': str(configuration_epoch),
    }


def _configuration(consumer, namespace):
    """Validate actual stored configuration instead of trusting its digest label."""
    if type(consumer.aspects) is not list or any(
        type(pair) is not list for pair in consumer.aspects
    ):
        raise SharedStateError('Stored consumer aspects are unqualified')
    result = ConsumerConfiguration(
        consumer_id=consumer.pk,
        namespace_id=consumer.namespace_id,
        namespace_epoch=consumer.namespace_epoch,
        configuration_epoch=consumer.configuration_epoch,
        consumer_key=consumer.consumer_key,
        aspects=tuple(tuple(pair) for pair in consumer.aspects),
    )
    payload = _configuration_payload(
        namespace, result.consumer_key, result.aspects, result.configuration_epoch
    )
    if (
        consumer.namespace_id != namespace.pk
        or consumer.namespace_epoch != namespace.epoch
        or fingerprint(payload) != consumer.binding_digest
        or not consumer.active
    ):
        raise SharedStateError('Consumer configuration is unavailable or changed')
    return result


def _consumer(consumer_id):
    """Lock metadata only: namespace, configuration, then delivery/progress rows."""
    namespace_id = (
        EventConsumer.objects
        .filter(pk=consumer_id)
        .values_list('namespace_id', flat=True)
        .first()
    )
    if namespace_id is None:
        raise SharedStateError('Consumer configuration is unavailable')
    namespace = require_namespace(namespace_id)
    consumer = EventConsumer.objects.select_for_update().filter(pk=consumer_id).first()
    if consumer is None:
        raise SharedStateError('Consumer configuration is unavailable')
    _configuration(consumer, namespace)
    return consumer, namespace


def configure_consumer(namespace_id, consumer_key, *, aspects, configuration_epoch):
    """Trusted server configuration, not an actor grant or automatic source opt-in."""
    integer(namespace_id)
    literal(consumer_key, 128)
    supported_aspects(aspects)
    exact_uuid(configuration_epoch)
    detached = tuple(tuple(pair) for pair in aspects)
    with shared_transaction(durable=True):
        namespace = require_namespace(namespace_id)
        binding = _configuration_payload(
            namespace, consumer_key, detached, configuration_epoch
        )
        prior = (
            EventConsumer.objects
            .select_for_update()
            .filter(configuration_epoch=configuration_epoch)
            .first()
        )
        if prior is not None:
            if prior.binding_digest != fingerprint(binding):
                raise SharedConflictError(
                    'Configuration epoch has a conflicting binding'
                )
            return _configuration(prior, namespace)
        EventConsumer.objects.filter(
            namespace=namespace, consumer_key=consumer_key, active=True
        ).update(active=False)
        consumer = EventConsumer.objects.create(
            namespace=namespace,
            namespace_epoch=namespace.epoch,
            consumer_key=consumer_key,
            configuration_epoch=configuration_epoch,
            aspects=binding['aspects'],
            binding_digest=fingerprint(binding),
            active=True,
        )
        return _configuration(consumer, namespace)


def _event(change, namespace):
    """Reconstruct an actual generic or captured placement event, not caller data."""
    if change.namespace_id != namespace.pk or change.epoch != namespace.epoch:
        raise SharedStateError('Event belongs to another source namespace epoch')
    if change.generic_identity_id is None:
        from integration_state.s7_changes import (
            PLACEMENT_CHANGE_FIELDS,
            placement_change_from_values,
        )

        native = placement_change_from_values(
            environment=namespace.environment,
            namespace_id=namespace.pk,
            epoch=namespace.epoch,
            record={field: getattr(change, field) for field in PLACEMENT_CHANGE_FIELDS},
        )
        key = key_payload(
            SharedSourceKey(
                namespace_id=namespace.pk,
                environment=namespace.environment,
                epoch=native.identity.epoch,
                entity_kind=native.identity.entity_kind,
                aspect=native.identity.aspect,
                source_id=native.identity.source_id,
            )
        )
        revision = native.revision
    else:
        record = change_record(change, change.generic_identity, namespace)
        key, revision = key_payload(record.key), record.fence
    payload = {
        'schema': 'aimms.shared-event.v1',
        'change_id': change.pk,
        'key': key,
        'revision': revision,
        'operation': change.operation,
        'previous': change.previous,
        'current': change.current,
        'captured_at': change.captured_at.isoformat(),
    }
    expected = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        allow_nan=False,
    )
    encoded = _encode(payload)
    literal(encoded, 65536)
    if encoded != expected:
        raise SharedStateError('Encoded delivery differs from its validated event')
    return encoded, key


def _claim(delivery, consumer):
    """Return an immutable exact lease, with no mutable model/JSON reference."""
    return EventClaim(
        consumer_id=consumer.pk,
        delivery_id=delivery.pk,
        change_id=delivery.change_id,
        namespace_epoch=consumer.namespace_epoch,
        configuration_epoch=consumer.configuration_epoch,
        ownership_fence=delivery.fence,
        lease_token=delivery.lease_token,
        worker_key=delivery.worker_key,
        expires_at=delivery.lease_until,
        event_payload=delivery.event_payload,
        payload_digest=delivery.payload_digest,
    )


def claim_changes(consumer_id, *, worker_key, limit, lease_seconds):
    """Claim a bounded committed batch by absence, never a numeric high-water mark.

    An expired attempt is retained and replaced with a fresh token/fence. This
    is at-least-once private delivery, not proof that an external effect was
    unsent or that replaying such an effect is safe.
    """
    integer(consumer_id)
    literal(worker_key, 128)
    integer(limit)
    integer(lease_seconds)
    if limit > MAX_CLAIM_BATCH or lease_seconds > MAX_LEASE_SECONDS:
        raise ValueError('The bounded claim budget is exceeded')
    with shared_transaction(durable=True):
        consumer, namespace = _consumer(consumer_id)
        now = timezone.now()
        terminal = EventDelivery.objects.filter(
            consumer=consumer, change_id=OuterRef('pk'), state='acknowledged'
        )
        leased = EventDelivery.objects.filter(
            consumer=consumer,
            change_id=OuterRef('pk'),
            state='claimed',
            lease_until__gt=now,
        )
        supported = Q(pk__in=[])
        for kind, aspect in _configuration(consumer, namespace).aspects:
            supported |= Q(entity_kind=kind, aspect=aspect)
        candidates = SourceChange.objects.filter(
            namespace=namespace, epoch=namespace.epoch
        ).filter(supported)
        candidates = candidates.annotate(
            _terminal=Exists(terminal), _leased=Exists(leased)
        ).filter(_terminal=False, _leased=False)
        result = []
        for change in candidates.select_related('generic_identity').order_by('pk')[
            :limit
        ]:
            payload, _ = _event(change, namespace)
            previous = (
                EventDelivery.objects
                .filter(consumer=consumer, change=change)
                .order_by('-fence')
                .first()
            )
            fence = 1 if previous is None else previous.fence + 1
            integer(fence)
            EventDelivery.objects.filter(
                consumer=consumer, change=change, state='claimed'
            ).update(state='expired')
            delivery = EventDelivery.objects.create(
                consumer=consumer,
                change=change,
                fence=fence,
                lease_token=uuid4(),
                worker_key=worker_key,
                lease_until=now + timedelta(seconds=lease_seconds),
                event_payload=payload,
                payload_digest=hashlib.sha256(payload.encode('utf-8')).hexdigest(),
                state='claimed',
            )
            result.append(_claim(delivery, consumer))
        return tuple(result)


def _receipt(delivery):
    return DeliveryAcknowledgement(
        delivery_id=delivery.pk,
        change_id=delivery.change_id,
        outcome=delivery.outcome,
        advanced=delivery.advanced,
    )


def _receipt_digest(delivery):
    return fingerprint({
        'delivery_id': delivery.pk,
        'consumer_id': delivery.consumer_id,
        'change_id': delivery.change_id,
        'fence': delivery.fence,
        'token': str(delivery.lease_token),
        'payload_digest': delivery.payload_digest,
        'requested_outcome': delivery.requested_outcome,
        'outcome': delivery.outcome,
        'advanced': delivery.advanced,
    })


def ack_change(claim, *, outcome):
    """CAS exact owned payload/configuration/epoch; never regress aspect progress.

    All explicit outcomes are retained terminal delivery dispositions. Failed
    and uncertain outcomes do not advance aspect progress or silently requeue
    an effect. A separately configured consumer can intentionally observe the
    event again; it does not inherit any old configuration's authority.
    """
    if type(claim) is not EventClaim:
        raise ValueError('An exact private event claim is required')
    claim.__post_init__()
    if type(outcome) is not str or outcome not in OUTCOMES:
        raise ValueError('An exact delivery outcome is required')
    with shared_transaction(durable=True):
        consumer, namespace = _consumer(claim.consumer_id)
        delivery = (
            EventDelivery.objects
            .select_for_update()
            .filter(pk=claim.delivery_id, consumer=consumer)
            .first()
        )
        if delivery is None or _claim(delivery, consumer) != claim:
            raise SharedConflictError('Delivery claim ownership or binding changed')
        change = SourceChange.objects.select_related('generic_identity').get(
            pk=delivery.change_id
        )
        payload, key = _event(change, namespace)
        if payload != delivery.event_payload:
            raise SharedStateError('Claimed event payload changed')
        if delivery.state == 'acknowledged':
            if delivery.requested_outcome != outcome:
                raise SharedConflictError('Acknowledgment has a conflicting outcome')
            if delivery.receipt_digest != _receipt_digest(delivery):
                raise SharedStateError('Stored acknowledgment is unqualified')
            return _receipt(delivery)
        if delivery.state != 'claimed' or delivery.lease_until <= timezone.now():
            raise SharedConflictError('Delivery ownership expired or was replaced')
        advanced = False
        actual_outcome = outcome
        if outcome in ('applied', 'superseded'):
            progress = (
                ConsumerAspectProgress.objects
                .select_for_update()
                .filter(consumer=consumer, source_digest=fingerprint(key))
                .first()
            )
            if progress is not None and progress.revision >= change.revision:
                actual_outcome = 'superseded'
            else:
                ConsumerAspectProgress.objects.update_or_create(
                    consumer=consumer,
                    source_digest=fingerprint(key),
                    defaults={'change': change, 'revision': change.revision},
                )
                advanced = True
        delivery.state = 'acknowledged'
        delivery.requested_outcome = outcome
        delivery.outcome = actual_outcome
        delivery.advanced = advanced
        delivery.receipt_digest = _receipt_digest(delivery)
        delivery.save(
            update_fields=[
                'state',
                'requested_outcome',
                'outcome',
                'advanced',
                'receipt_digest',
            ]
        )
        return _receipt(delivery)
