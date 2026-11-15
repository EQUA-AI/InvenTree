"""Same-ledger private observations; no source reader, writer or transport IO."""

import hashlib
import json
from contextlib import contextmanager
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connection, transaction

from integration_state.models import (
    GenericSourceIdentity,
    SourceChange,
    SourceNamespace,
    SourceState,
)
from integration_state.shared_contracts import (
    SharedChange,
    SharedSourceKey,
    integer,
    key_payload,
    literal,
    observation_from_payload,
    observation_payload,
)


class SharedStateError(ValueError):
    """Private state is unqualified, never a successful empty population."""


class SharedConflictError(SharedStateError):
    """An exact source/event/fence identity has a conflicting observation."""


class SharedStoreUnavailableError(SharedStateError):
    """The private state dependency failed after its atomic rollback."""


@contextmanager
def shared_transaction(*, durable=False):
    """Refuse unqualified routing before ORM; translate DB faults after rollback."""
    if (
        settings.DATABASE_ROUTERS
        or connection.alias != 'default'
        or connection.vendor not in ('sqlite', 'postgresql')
    ):
        raise SharedStateError('Shared state routing or database is unqualified')
    try:
        if (
            durable
            and not connection.get_autocommit()
            and (
                not connection.atomic_blocks
                or not connection.atomic_blocks[-1]._from_testcase
            )
        ):
            raise SharedStateError(
                'Ownership requires an independently committed transaction'
            )
        with transaction.atomic(durable=durable):
            yield
    except DatabaseError as exc:
        raise SharedStoreUnavailableError('Shared state store is unavailable') from exc


def fingerprint(payload):
    """Hash explicit detached primitive metadata, not a returned claim's label."""
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            allow_nan=False,
        ).encode('utf-8')
    ).hexdigest()


def require_namespace(namespace_id, *, epoch=None, environment=None):
    """Coordinate private state without ever locking a native operational row."""
    integer(namespace_id)
    namespace = (
        SourceNamespace.objects
        .select_for_update(no_key=True)
        .filter(pk=namespace_id)
        .first()
    )
    if namespace is None or not namespace.active:
        raise SharedStateError('Shared source namespace is unavailable')
    if (epoch is not None and namespace.epoch != epoch) or (
        environment is not None and namespace.environment != environment
    ):
        raise SharedStateError('Shared source namespace binding changed')
    return namespace


def _key(identity, namespace):
    """Restore exact registry data, without treating a surrogate as native ID."""
    result = SharedSourceKey(
        namespace_id=namespace.pk,
        environment=namespace.environment,
        epoch=identity.epoch,
        entity_kind=identity.entity_kind,
        aspect=identity.aspect,
        source_id=identity.integer_key
        if identity.key_type == 'integer'
        else identity.text_key,
        declared_incarnation=identity.declared_incarnation,
    )
    if (
        identity.namespace_id != namespace.pk
        or fingerprint(key_payload(result)) != identity.identity_digest
    ):
        raise SharedStateError('Shared source identity is unqualified')
    return result


def change_record(change, identity, namespace):
    """Detach an actual private ledger row into its versioned observation DTO."""
    if (
        change.generic_identity_id != identity.pk
        or change.namespace_id != namespace.pk
        or change.epoch != identity.epoch
        or change.entity_kind != identity.entity_kind
        or change.aspect != identity.aspect
        or change.source_id is not None
        or change.operation != 'observed'
    ):
        raise SharedStateError('Shared change binding is unqualified')
    key = _key(identity, namespace)
    integer(change.revision)
    expected_digest = fingerprint({
        'key': key_payload(key),
        'event_key': change.producer_key,
        'expected_fence': change.revision - 1,
        'observation': change.current,
        'previous': change.previous,
    })
    if change.canonical_digest != expected_digest:
        raise SharedStateError('Shared change payload is unqualified')
    return SharedChange(
        change_id=change.pk,
        key=key,
        fence=change.revision,
        observation=observation_from_payload(change.current),
        previous=None
        if change.previous is None
        else observation_from_payload(change.previous),
    )


def record_observation(key, *, event_key, expected_fence, observation):
    """Atomically append non-authoritative metadata with exact replay/fence CAS.

    The caller must enlist a separately admitted source mutation in its own
    transaction. This function neither reads nor changes any native source.
    """
    values = key_payload(key)
    payload = observation_payload(observation)
    literal(event_key, 128)
    integer(expected_fence, zero=True)
    if (values['entity_kind'], values['aspect']) == ('machine', 'placement'):
        raise SharedStateError('Native placement capture owns this source aspect')
    payload_binding = {
        'key': values,
        'event_key': event_key,
        'expected_fence': expected_fence,
        'observation': payload,
    }
    identity_digest = fingerprint(values)
    with shared_transaction():
        namespace = require_namespace(
            values['namespace_id'],
            epoch=UUID(values['epoch']),
            environment=values['environment'],
        )
        identity, _ = GenericSourceIdentity.objects.get_or_create(
            namespace=namespace,
            epoch=namespace.epoch,
            identity_digest=identity_digest,
            defaults={
                'entity_kind': values['entity_kind'],
                'aspect': values['aspect'],
                'key_type': values['key_type'],
                'integer_key': values['source_id']
                if values['key_type'] == 'integer'
                else None,
                'text_key': values['source_id']
                if values['key_type'] == 'string'
                else None,
                'declared_incarnation': values['declared_incarnation'],
            },
        )
        if key_payload(_key(identity, namespace)) != values:
            raise SharedStateError('Shared source identity is unqualified')
        previous_event = SourceChange.objects.filter(
            generic_identity=identity, producer_key=event_key
        ).first()
        if previous_event is not None:
            result = change_record(previous_event, identity, namespace)
            if previous_event.canonical_digest != fingerprint(
                payload_binding | {'previous': previous_event.previous}
            ):
                raise SharedConflictError(
                    'Shared event identity has a conflicting payload'
                )
            return result
        state = (
            SourceState.objects
            .select_for_update()
            .filter(generic_identity=identity)
            .first()
        )
        latest = (
            SourceChange.objects
            .filter(generic_identity=identity)
            .order_by('-revision')
            .first()
        )
        if state is None:
            if latest is not None:
                raise SharedStateError('Shared source state is unqualified')
            actual_fence, previous = 0, None
        else:
            if (
                latest is None
                or state.namespace_id != namespace.pk
                or state.epoch != namespace.epoch
                or state.entity_kind != identity.entity_kind
                or state.aspect != identity.aspect
                or state.revision != latest.revision
                or state.generic_snapshot != latest.current
            ):
                raise SharedStateError('Shared source state is unqualified')
            actual_fence, previous = state.revision, state.generic_snapshot
        if actual_fence != expected_fence:
            raise SharedConflictError('Shared source fence changed')
        revision = actual_fence + 1
        integer(revision)
        if state is None:
            state = SourceState.objects.create(
                namespace=namespace,
                epoch=namespace.epoch,
                entity_kind=identity.entity_kind,
                aspect=identity.aspect,
                source_id=None,
                generic_identity=identity,
                generic_snapshot=payload,
                revision=revision,
                native_version=None,
                deleted=False,
            )
        else:
            state.revision = revision
            state.generic_snapshot = payload
            state.save(update_fields=['revision', 'generic_snapshot'])
        change = SourceChange.objects.create(
            namespace=namespace,
            epoch=namespace.epoch,
            entity_kind=identity.entity_kind,
            aspect=identity.aspect,
            source_id=None,
            generic_identity=identity,
            producer_key=event_key,
            canonical_digest=fingerprint(payload_binding | {'previous': previous}),
            revision=revision,
            operation='observed',
            previous=previous,
            current=payload,
        )
        return change_record(change, identity, namespace)
