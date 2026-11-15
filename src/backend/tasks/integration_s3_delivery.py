"""Unregistered S3 source observations to S5/S7 private delivery metadata.

No native writer, complete population, history proof, copy inventory, approval,
source removal or erasure is provided. Whole-wire digests are not lifecycle
watermarks, attachment metadata revisions or original-file-only hashes.
"""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field

from integration_state.shared_contracts import (
    ConsumerConfiguration,
    DeliveryAcknowledgement,
    EventClaim,
    SharedChange,
    SharedSourceKey,
    SourceObservation,
    exact_uuid,
    integer,
    key_payload,
    literal,
    observation_payload,
)

MAX_SOURCE_WIRE_BYTES = 2 * 1024 * 1024
_OBSERVATION_TYPE = SourceObservation
_KEY_TYPE = SharedSourceKey
ASPECTS = (
    ('work', 'work.core'),
    ('maintenance', 'maintenance.direct'),
    ('attachment', 'work_document.metadata'),
    ('attachment', 'machine_document.metadata'),
    ('attachment', 'work_document.content'),
    ('attachment', 'machine_document.content'),
)


class SourceDeliveryError(ValueError):
    """Private composition failed without exposing source bodies or credentials."""


class SourceDeliveryUnavailableError(SourceDeliveryError):
    """Unavailable authority never becomes missing, deletion or empty success."""


def _independent_source_boundary():
    """Refuse caller-owned transactions before any source transport/file IO."""
    from django.db import connection

    if connection.in_atomic_block or not connection.get_autocommit():
        raise SourceDeliveryError('S3 delivery requires an independent boundary')


def _current_policy():
    """Refresh actual credentials/role/grants without reading a source payload.

    This private conservative fence is not historical client attribution. Full
    source/native scope/owner/byte checks run outside metadata ownership.
    """
    from ai.core.auth import get_current_principal
    from assets.integration_authority import (
        explicitly_granted_client_ids,
        integration_actor_from_current_ai,
    )

    principal = get_current_principal()
    actor = integration_actor_from_current_ai(required_role='work_order')
    if principal is None or actor is None:
        raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
    grants = tuple(
        sorted(row['client_id'] for row in explicitly_granted_client_ids(actor))
    )
    if (
        type(actor.pk) is not int
        or not grants
        or any(type(value) is not int or value < 1 for value in grants)
        or get_current_principal() is not principal
    ):
        raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
    return principal, actor.pk, grants


def metadata_from_source_payload(payload):
    """Project metadata only; arbitrary supplied bytes confer no read authority.

    Only the source-bound factory obtains native/current-invocation observations.
    The independent two-MiB whole-wire budget refuses rather than truncates.
    """
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_SOURCE_WIRE_BYTES:
            raise ValueError('Unsupported source bytes')
        body = json.loads(payload)
        if (
            type(body) is not dict
            or body.get('wire_schema') != 'aimms.s3.source-wire.v1'
            or type(body.get('record')) is not dict
            or body['record'].get('schema_version')
            not in (
                'aimms.work.core_details.v1',
                'aimms.maintenance.direct_details.v1',
                'aimms.document.metadata.v1',
                'aimms.document.content.v1',
            )
        ):
            raise ValueError('Unsupported source version')
        expected = {
            'schema': 'aimms.shared-state.v1',
            'state': 'available',
            'source_revision': None,
            'client_ids': None,
            'content_digest': hashlib.sha256(payload).hexdigest(),
            'coverage_complete': False,
            'source_deleted': False,
        }
        result = SourceObservation(
            state='available', content_digest=expected['content_digest']
        )
        if (
            type(result) is not _OBSERVATION_TYPE
            or observation_payload(result) != expected
        ):
            raise ValueError('Source observation binding changed')
        return result
    except MemoryError:
        raise
    except Exception:
        raise SourceDeliveryError('S3 delivery metadata is unavailable') from None


def _coordinates(environment, operation, source_pk):
    """Reject coerced labels/IDs before source or private-state work."""
    literal(environment, 128)
    integer(source_pk)
    if type(operation) is not str:
        raise SourceDeliveryError('S3 delivery operation is unavailable')
    for domain, aspect in ASPECTS:
        if operation == aspect:
            return domain, aspect
    raise SourceDeliveryUnavailableError('S3 delivery operation is unavailable')


def _signature(change):
    """Validate and bind every primitive of the actual detached S5 change."""
    if type(change) is not SharedChange:
        raise SourceDeliveryError('S3 delivery change is unavailable')
    integer(change.change_id)
    integer(change.fence)
    return json.dumps(
        {
            'id': change.change_id,
            'key': key_payload(change.key),
            'fence': change.fence,
            'observation': observation_payload(change.observation),
            'previous': None
            if change.previous is None
            else observation_payload(change.previous),
        },
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        allow_nan=False,
    )


def _delivery_binding(configuration, claim):
    """Accept legitimate detached DTOs, but never a foreign consumer or lease."""
    if (
        type(configuration) is not ConsumerConfiguration
        or type(claim) is not EventClaim
    ):
        raise SourceDeliveryError('S3 delivery ownership is unavailable')
    configuration.__post_init__()
    claim.__post_init__()
    return (
        configuration.consumer_id,
        configuration.namespace_id,
        configuration.namespace_epoch.hex,
        configuration.configuration_epoch.hex,
        configuration.consumer_key,
        configuration.aspects,
        claim.consumer_id,
        claim.delivery_id,
        claim.change_id,
        claim.namespace_epoch.hex,
        claim.configuration_epoch.hex,
        claim.ownership_fence,
        claim.lease_token.hex,
        claim.worker_key,
        claim.expires_at.isoformat(),
        claim.event_payload,
        claim.payload_digest,
    )


@dataclass(frozen=True)
class _PreparedSourceDelivery:
    """Non-content-repr handle bound to the existing real ASGI invocation."""

    _read: object = field(repr=False)
    _payload: bytes = field(repr=False)
    _environment: str = field(repr=False)
    _operation: str = field(repr=False)
    _source_pk: int = field(repr=False)
    _policy: tuple = field(repr=False)

    def _check_policy(self):
        """Fence current native actor/grants without source content under locks."""
        from ai.core.auth import get_current_principal

        if get_current_principal() is not self._policy[0]:
            raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
        principal, actor_pk, grants = _current_policy()
        if (
            principal is not self._policy[0]
            or actor_pk != self._policy[1]
            or grants != self._policy[2]
        ):
            raise SourceDeliveryUnavailableError('S3 delivery is unavailable')

    def _check(self):
        """Refresh actual credentials, native policy/owner and full retained bytes."""
        from tasks import integration_s3_reader as reader

        try:
            _independent_source_boundary()
            _coordinates(self._environment, self._operation, self._source_pk)
            if type(self._read) is not reader._AIReadObservation:
                raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
            reader._outcome_key(self._read.outcome)
            if (
                self._read.outcome.state != 'available'
                or self._read.outcome.payload is not self._payload
            ):
                raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
            reader._checked_record(
                self._payload, self._operation, self._environment, self._source_pk
            )
            if self._read.reauthorize() is not True:
                raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
        except MemoryError:
            raise
        except SourceDeliveryError:
            raise
        except Exception:
            raise SourceDeliveryError('S3 delivery is unavailable') from None

    @contextmanager
    def _source_work(self):
        """Recheck successful and ordinary-fault exits without undoing commits.

        Yields:
            No authorization token or source data; the same private handle remains.
        """
        from integration_state.shared_events import SharedStateError

        self._check()
        try:
            yield
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, (SourceDeliveryError, SharedStateError)):
                raise
            raise SourceDeliveryError('S3 delivery failed') from None
        self._check()

    def _validate_observation(self, change):
        """Compare detached primitives, without any source or store read."""
        signature = _signature(change)
        expected = metadata_from_source_payload(self._payload)
        key = change.key
        if (
            key.environment != self._environment
            or (key.entity_kind, key.aspect)
            != _coordinates(self._environment, self._operation, self._source_pk)
            or type(key.source_id) is not int
            or key.source_id != self._source_pk
            or key.declared_incarnation is not None
            or observation_payload(change.observation) != observation_payload(expected)
        ):
            raise SourceDeliveryError('S3 delivery change is unavailable')
        return signature

    def _validate_change(self, change):
        """Reauthorize outside ownership, then read committed owner metadata."""
        from integration_state.models import SourceChange
        from integration_state.shared_events import change_record

        signature = self._validate_observation(change)
        key = change.key
        self._check()
        row = (
            SourceChange.objects
            .select_related('generic_identity', 'namespace')
            .filter(pk=change.change_id)
            .first()
        )
        if (
            row is None
            or row.generic_identity_id is None
            or not row.namespace.active
            or row.namespace.epoch != key.epoch
            or _signature(change_record(row, row.generic_identity, row.namespace))
            != signature
        ):
            raise SourceDeliveryError('S3 delivery change is unavailable')
        return row

    def record(self, *, namespace_id, epoch, event_key, expected_fence):
        """Append/replay through S5; pre-commit refusal rolls back metadata only."""
        from integration_state.shared_events import (
            record_observation,
            shared_transaction,
        )

        _independent_source_boundary()
        with self._source_work():
            integer(namespace_id)
            exact_uuid(epoch)
            literal(event_key, 128)
            integer(expected_fence, zero=True)
            domain, aspect = _coordinates(
                self._environment, self._operation, self._source_pk
            )
            expected_key = {
                'schema': 'aimms.shared-state.v1',
                'namespace_id': namespace_id,
                'environment': self._environment,
                'epoch': str(epoch),
                'entity_kind': domain,
                'aspect': aspect,
                'key_type': 'integer',
                'source_id': self._source_pk,
                'declared_incarnation': None,
            }
            key = SharedSourceKey(
                namespace_id=namespace_id,
                environment=self._environment,
                epoch=epoch,
                entity_kind=domain,
                aspect=aspect,
                source_id=self._source_pk,
            )
            if type(key) is not _KEY_TYPE or key_payload(key) != expected_key:
                raise SourceDeliveryError('S3 delivery key is unavailable')
            observation = metadata_from_source_payload(self._payload)
            self._check()
            with shared_transaction(durable=True):
                self._check_policy()
                result = record_observation(
                    key,
                    event_key=event_key,
                    expected_fence=expected_fence,
                    observation=observation,
                )
                self._validate_observation(result)
                self._check_policy()
                if (
                    key_payload(result.key) != expected_key
                    or result.fence != expected_fence + 1
                ):
                    raise SourceDeliveryError('S3 delivery change is unavailable')
                self._check_policy()
            # Full source/file validation and owner readback happen only after
            # the independent metadata commit. A late refusal preserves it.
            self._check()
            row = self._validate_change(result)
            if row.producer_key != event_key:
                raise SourceDeliveryError('S3 delivery change is unavailable')
        return result

    def acknowledge(self, change, *, configuration, claim, worker_key):
        """Decode real committed metadata; S5 alone verifies/commits the private ack.

        A late source refusal after S5 commits cannot erase the acknowledgement
        or authorize source/effect resubmission. This installs no claim loop.
        """
        from integration_state import s7_changes
        from integration_state.models import EventDelivery
        from integration_state.shared_delivery import ack_change

        _independent_source_boundary()
        with self._source_work():
            self._validate_change(change)
            expected_binding = _delivery_binding(configuration, claim)
            literal(worker_key, 128)
            self._check()
            decoded = s7_changes.delivery_change_from_claim(
                environment=self._environment,
                worker_key=worker_key,
                configuration=configuration,
                claim=claim,
            )
            if (
                type(decoded) is not s7_changes._DELIVERED_CHANGE_TYPE
                or _delivery_binding(decoded.configuration, decoded.claim)
                != expected_binding
                or type(decoded.environment) is not str
                or decoded.environment != self._environment
                or type(decoded.worker_key) is not str
                or decoded.worker_key != worker_key
            ):
                raise SourceDeliveryError('S3 delivery ownership is unavailable')
            decoded.__post_init__()
            if type(decoded.change) is not s7_changes.SharedChangeObservation:
                raise SourceDeliveryError('S3 delivery change is unavailable')
            actual = SharedChange(
                change_id=decoded.change.event_id,
                key=decoded.change.key,
                fence=decoded.change.fence,
                observation=decoded.change.observation,
                previous=decoded.change.previous,
            )
            if _signature(actual) != _signature(change):
                raise SourceDeliveryError('S3 delivery change is unavailable')
            self._check()
            receipt = ack_change(claim, outcome='applied')
            self._check()
            if type(receipt) is not DeliveryAcknowledgement:
                raise SourceDeliveryError('S3 delivery receipt is unavailable')
            receipt.__post_init__()
            self._check()
            stored = EventDelivery.objects.filter(pk=claim.delivery_id).first()
            if (
                stored is None
                or stored.state != 'acknowledged'
                or stored.consumer_id != claim.consumer_id
                or stored.change_id != change.change_id
                or receipt.delivery_id != stored.pk
                or receipt.change_id != stored.change_id
                or receipt.outcome != stored.outcome
                or receipt.advanced is not stored.advanced
            ):
                raise SourceDeliveryError('S3 delivery receipt is unavailable')
            self._check()
        return receipt


_PREPARED_TYPE = _PreparedSourceDelivery


def prepare_source_delivery(*, environment, operation, source_pk, max_bytes=None):
    """Obtain actual current-AI/native-source bytes, never a caller actor label.

    Server code chooses the six admitted source operations and coordinates.
    Unavailable/unsupported historical records are never persisted as missing.
    """
    from tasks import integration_s3_reader as reader

    try:
        _coordinates(environment, operation, source_pk)
        _independent_source_boundary()
        observed = reader.read_source_for_current_ai(
            environment=environment,
            operation=operation,
            source_pk=source_pk,
            max_bytes=max_bytes,
        )
        if type(observed) is not reader._AIReadObservation:
            raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
        reader._outcome_key(observed.outcome)
        if observed.outcome.state != 'available':
            raise SourceDeliveryUnavailableError('S3 delivery is unavailable')
        policy = _current_policy()
        result = _PreparedSourceDelivery(
            observed,
            observed.outcome.payload,
            environment,
            operation,
            source_pk,
            policy,
        )
        if (
            type(result) is not _PREPARED_TYPE
            or result._read is not observed
            or result._payload is not observed.outcome.payload
            or type(result._environment) is not str
            or result._environment != environment
            or type(result._operation) is not str
            or result._operation != operation
            or type(result._source_pk) is not int
            or result._source_pk != source_pk
            or result._policy is not policy
        ):
            raise SourceDeliveryError('S3 delivery is unavailable')
        result._check()
        metadata_from_source_payload(result._payload)
        result._check()
        result._check_policy()
        return result
    except MemoryError:
        raise
    except SourceDeliveryError:
        raise
    except Exception:
        raise SourceDeliveryError('S3 delivery is unavailable') from None
