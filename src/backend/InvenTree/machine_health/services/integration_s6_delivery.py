"""Internal recorded-fixture observation to S5/S7 private delivery metadata.

Unregistered and source-read-only. No worker, scan, native writer, competing
ledger, data-copy inventory, tombstone, approval or erasure capability exists
here. Metadata epoch/fence and a fixture generation are NOT native incarnation,
revision, commit order or historical attribution. Source clocks/IDs/values stay
in the original private S6 bytes; only their digest enters the shared ledger.
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
    integer,
    key_payload,
    observation_payload,
)
from machine_health.services.integration_s6_wire import MAX_HISTORY_BYTES

ASPECT = ('telemetry_binding', 'recorded_history')
_OBSERVATION_TYPE = SourceObservation


class HistoryDeliveryError(ValueError):
    """Private composition failed; no source body or credential is disclosed."""


class HistoryDeliveryUnavailableError(HistoryDeliveryError):
    """Current source/actor is unavailable; absence never becomes a tombstone."""


def metadata_from_recorded_payload(payload):
    """Project metadata only, NOT source validation or authentication authority.

    The factory below alone obtains source-qualified bytes. This helper's digest
    preserves the entire original encoding, including clocks, signed zero,
    unknowns, provenance and gaps; it does not invent a native revision or client
    history from current placement or the selected fixture generation.
    """
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_HISTORY_BYTES:
        raise HistoryDeliveryError('Recorded history metadata is unavailable')
    body = json.loads(payload)
    if (
        type(body) is not dict
        or type(body.get('schema_version')) is not str
        or body['schema_version'] != 'aimms.s6.history-source.v1'
        or type(body.get('complete')) is not bool
        or body.get('population_complete') is not False
        or body.get('revision_complete') is not False
    ):
        raise HistoryDeliveryError('Recorded history metadata is unavailable')
    expected = {
        'schema': 'aimms.shared-state.v1',
        'state': 'available' if body['complete'] else 'incomplete',
        'source_revision': None,
        'client_ids': None,
        'content_digest': hashlib.sha256(payload).hexdigest(),
        'coverage_complete': False,
        'source_deleted': False,
    }
    result = SourceObservation(
        state=expected['state'], content_digest=expected['content_digest']
    )
    if type(result) is not _OBSERVATION_TYPE or observation_payload(result) != expected:
        raise HistoryDeliveryError('Recorded history metadata is unavailable')
    return result


def _signature(change):
    """Bind every returned private field independently of DTO scalar equality."""
    if type(change) is not SharedChange:
        raise HistoryDeliveryError('Recorded history change is unavailable')
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
    """Snapshot explicit primitives; S7 legitimately detaches both input DTOs."""
    if (
        type(configuration) is not ConsumerConfiguration
        or type(claim) is not EventClaim
    ):
        raise HistoryDeliveryError('Recorded history delivery is unavailable')
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
class _PreparedHistoryDelivery:
    """A non-serializable source-bound handle, only for this ASGI invocation."""

    _read: object = field(repr=False)
    _payload: bytes = field(repr=False)
    _environment: str = field(repr=False)
    _binding_pk: int = field(repr=False)

    def _check(self):
        """Refresh actual authentication, native role/grants/source and original bytes."""
        from machine_health.services import integration_s6_reader as reader

        if (
            type(self._read) is not reader._AI_OBSERVATION_TYPE
            or type(self._environment) is not str
            or type(self._binding_pk) is not int
            or self._read.reauthorize() is not True
            or type(self._read.outcome) is not reader._OUTCOME_TYPE
            or self._read.outcome.payload is not self._payload
        ):
            raise HistoryDeliveryUnavailableError(
                'Recorded history delivery is unavailable'
            )
        source = json.loads(self._payload)['context']
        if (
            source['environment'] != self._environment
            or source['binding_pk'] != self._binding_pk
        ):
            raise HistoryDeliveryUnavailableError(
                'Recorded history delivery is unavailable'
            )

    def _validate_change(self, change):
        """Compare producer-derived expectations and independently read actual ledger."""
        from integration_state.models import SourceChange
        from integration_state.shared_events import change_record

        _signature(change)
        key = change.key
        expected = metadata_from_recorded_payload(self._payload)
        if (
            key.environment != self._environment
            or (key.entity_kind, key.aspect) != ASPECT
            or type(key.source_id) is not int
            or key.source_id != self._binding_pk
            or key.declared_incarnation is not None
            or observation_payload(change.observation) != observation_payload(expected)
        ):
            raise HistoryDeliveryError('Recorded history change is unavailable')
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
            != _signature(change)
        ):
            raise HistoryDeliveryError('Recorded history change is unavailable')
        return row

    @contextmanager
    def _source_work(self):
        """Recheck current authority on both successful and ordinary-fault exits.

        Yields:
            No authority carrier; callers retain the same private source handle.
        """
        from integration_state.shared_events import SharedStateError

        self._check()
        try:
            yield
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, (HistoryDeliveryError, SharedStateError)):
                raise
            raise HistoryDeliveryError('Recorded history delivery failed') from None
        self._check()

    def record(self, *, namespace_id, epoch, event_key, expected_fence):
        """Append/replay through S5 only; roll back metadata on late source refusal.

        This is a bounded observation of a native binding, not a complete history
        population or automatic source-writer capture. Metadata transactions take
        no native source locks, and the source itself is never changed.
        """
        from integration_state.shared_events import (
            record_observation,
            shared_transaction,
        )

        self._check()
        key = SharedSourceKey(
            namespace_id=namespace_id,
            environment=self._environment,
            epoch=epoch,
            entity_kind=ASPECT[0],
            aspect=ASPECT[1],
            source_id=self._binding_pk,
        )
        observation = metadata_from_recorded_payload(self._payload)
        with shared_transaction(), self._source_work():
            result = record_observation(
                key,
                event_key=event_key,
                expected_fence=expected_fence,
                observation=observation,
            )
            self._check()
            row = self._validate_change(result)
            if (
                key_payload(result.key) != key_payload(key)
                or row.producer_key != event_key
                or result.fence != expected_fence + 1
            ):
                raise HistoryDeliveryError('Recorded history change is unavailable')
            self._check()
        return result

    def acknowledge(self, change, *, configuration, claim, worker_key):
        """Decode the actual claim, then let S5 verify and commit its private ack.

        Claim/configuration come from the real existing owner, not this source
        read or an AI label. No broad auto-claim loop is installed. A post-commit
        refusal cannot roll back a committed bookkeeping acknowledgement and
        must not cause an external effect or its resubmission.
        """
        from integration_state import s7_changes
        from integration_state.models import EventDelivery
        from integration_state.shared_delivery import ack_change

        with self._source_work():
            self._validate_change(change)
            expected_binding = _delivery_binding(configuration, claim)
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
                raise HistoryDeliveryError('Recorded history delivery is unavailable')
            decoded.__post_init__()
            if type(decoded.change) is not s7_changes.SharedChangeObservation:
                raise HistoryDeliveryError('Recorded history delivery is unavailable')
            actual = SharedChange(
                change_id=decoded.change.event_id,
                key=decoded.change.key,
                fence=decoded.change.fence,
                observation=decoded.change.observation,
                previous=decoded.change.previous,
            )
            if _signature(actual) != _signature(change):
                raise HistoryDeliveryError('Recorded history delivery is unavailable')
            self._check()
            receipt = ack_change(claim, outcome='applied')
            self._check()
            if type(receipt) is not DeliveryAcknowledgement:
                raise HistoryDeliveryError('Recorded history receipt is unavailable')
            receipt.__post_init__()
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
                raise HistoryDeliveryError('Recorded history receipt is unavailable')
            self._check()
        return receipt


_PREPARED_TYPE = _PreparedHistoryDelivery


def prepare_history_delivery(
    *, environment, machine_pk, source_pk, binding_pk, window, adapter=None, **budgets
):
    """Obtain actual current-AI/native-source proof, never caller actor labels.

    Only the existing reader's explicitly bounded keyword budgets are accepted.
    Unsupported/unavailable results are not persisted as missing/deleted rows.
    Current fixture capabilities do not opt real telemetry into this service.
    """
    from machine_health.services import integration_s6_reader as reader

    observed = reader.read_recorded_history_for_current_ai(
        environment=environment,
        machine_pk=machine_pk,
        source_pk=source_pk,
        binding_pk=binding_pk,
        window=window,
        adapter=adapter,
        **budgets,
    )
    if (
        type(observed) is not reader._AI_OBSERVATION_TYPE
        or type(observed.outcome) is not reader._OUTCOME_TYPE
        or observed.outcome.state != 'available'
    ):
        raise HistoryDeliveryUnavailableError(
            'Recorded history delivery is unavailable'
        )
    result = _PreparedHistoryDelivery(
        observed, observed.outcome.payload, environment, binding_pk
    )
    if (
        type(result) is not _PREPARED_TYPE
        or result._read is not observed
        or result._payload is not observed.outcome.payload
        or result._environment != environment
        or type(result._binding_pk) is not int
        or result._binding_pk != binding_pk
    ):
        raise HistoryDeliveryError('Recorded history delivery is unavailable')
    result._check()
    metadata_from_recorded_payload(result._payload)
    result._check()
    return result
