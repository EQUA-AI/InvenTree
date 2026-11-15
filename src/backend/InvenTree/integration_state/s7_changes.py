"""Private placement and shared-observation replay/invalidation decisions.

Actual S5 carriers stay separate. No poller, high-watermark, persistence, source
mutation, public tombstone or native actor/grant policy is introduced.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from assets.integration_contracts import ClientSourceIdentity
from assets.integration_s5_contracts import PlacementExpectation
from integration_state import shared_contracts
from integration_state.s7_contracts import (
    SourceAspectIdentity,
    exact_epoch,
    literal,
    positive_id,
)
from integration_state.shared_contracts import (
    ConsumerConfiguration,
    EventClaim,
    SharedChange,
    SharedSourceKey,
    SourceObservation,
    key_payload,
    observation_from_payload,
    observation_payload,
)
from integration_state.shared_contracts import integer as shared_integer

PLACEMENT_CHANGE_FIELDS = (
    'id',
    'namespace_id',
    'epoch',
    'entity_kind',
    'aspect',
    'source_id',
    'revision',
    'operation',
    'previous',
    'current',
    'captured_at',
)


@dataclass(frozen=True, kw_only=True)
class PlacementValue:
    """Retain a captured tuple, including genuinely unassigned coordinates."""

    client_id: int | None = field(repr=False)
    location_id: int | None = field(repr=False)
    native_version: int

    def __post_init__(self):
        """Missing native values do not become measured zeros or ownership."""
        for value in (self.client_id, self.location_id):
            if value is not None:
                positive_id(value)
        if type(self.native_version) is not int or self.native_version < 0:
            raise ValueError(
                'An exact nonnegative native placement version is required'
            )


def _identity(value):
    if type(value) is not SourceAspectIdentity:
        raise ValueError('An exact source aspect identity is required')
    value.__post_init__()
    positive_id(value.source_id)
    if value.entity_kind != 'machine' or value.aspect != 'placement':
        raise ValueError('Only the captured machine placement aspect is supported')


def _value(value):
    if type(value) is not PlacementValue:
        raise ValueError('An exact captured placement tuple is required')
    value.__post_init__()


@dataclass(frozen=True, kw_only=True)
class PlacementStateObservation:
    """A native S5 expectation has a fence but supplies no durable event ID."""

    identity: SourceAspectIdentity
    revision: int
    placement: PlacementValue = field(repr=False)

    def __post_init__(self):
        """Do not widen placement into other domains or generic source truth."""
        _identity(self.identity)
        positive_id(self.revision)
        _value(self.placement)


@dataclass(frozen=True, kw_only=True)
class PlacementChange:
    """Private captured evidence, not permission or an erasure instruction."""

    identity: SourceAspectIdentity
    namespace_id: int = field(repr=False)
    event_id: int
    revision: int
    operation: str
    captured_at: datetime
    previous: PlacementValue | None = field(repr=False)
    current: PlacementValue | None = field(repr=False)
    tombstones: tuple = field(default=(), init=False)
    removal_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)

    def __post_init__(self):
        """Revalidate detached source shapes without treating capture time as order."""
        _identity(self.identity)
        for value in (self.namespace_id, self.event_id, self.revision):
            positive_id(value)
        if type(self.operation) is not str or self.operation not in (
            'baseline',
            'created',
            'changed',
            'deleted',
        ):
            raise ValueError('An actual captured placement operation is required')
        if type(self.captured_at) is not datetime:
            raise ValueError('The original native capture time is required')
        for value in (self.previous, self.current):
            if value is not None:
                _value(value)
        shapes = {
            'baseline': (False, True),
            'created': (False, True),
            'changed': (True, True),
            'deleted': (True, False),
        }
        if (self.previous is not None, self.current is not None) != shapes[
            self.operation
        ]:
            raise ValueError('Captured before/current shape contradicts its operation')
        if self.operation == 'changed' and self.previous == self.current:
            raise ValueError('An unchanged placement is not a source change')
        if type(self.tombstones) is not tuple or self.tombstones:
            raise ValueError('Placement evidence cannot authorize tombstones')
        if self.removal_complete is not False or self.deletion_applied is not False:
            raise ValueError(
                'Placement evidence cannot claim complete removal or deletion'
            )


def placement_state_from_expectation(source):
    """Consume the actual S5 carrier without fabricating a source event or grant."""
    if type(source) is not PlacementExpectation:
        raise ValueError('An actual S5 placement expectation is required')
    source.__post_init__()
    return PlacementStateObservation(
        identity=SourceAspectIdentity(
            environment=source.machine.environment,
            epoch=source.namespace_epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=source.machine.pk,
        ),
        revision=source.fence,
        placement=PlacementValue(
            client_id=source.client.pk,
            location_id=source.location.pk if source.location is not None else None,
            native_version=source.native_version,
        ),
    )


def _snapshot(value):
    if value is None:
        return None
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != {'client_id', 'location_id', 'native_version'}
    ):
        raise ValueError('An exact captured placement object is required')
    return PlacementValue(**value)


def placement_change_from_values(*, environment, namespace_id, epoch, record):
    """Adapt SourceChange.values(*PLACEMENT_CHANGE_FIELDS), not arbitrary CDC.

    The caller owns namespace/capture admission. This pure mapping does not
    authenticate that caller, query source state, or authorize event disclosure.
    """
    literal(environment, 128)
    positive_id(namespace_id)
    exact_epoch(epoch)
    if (
        type(record) is not dict
        or any(type(key) is not str for key in record)
        or set(record) != set(PLACEMENT_CHANGE_FIELDS)
    ):
        raise ValueError('The exact S5 source-change projection is required')
    positive_id(record['namespace_id'])
    exact_epoch(record['epoch'])
    if record['namespace_id'] != namespace_id or record['epoch'] != epoch:
        raise ValueError('The captured row belongs to another namespace or epoch')
    return PlacementChange(
        identity=SourceAspectIdentity(
            environment=environment,
            epoch=record['epoch'],
            entity_kind=record['entity_kind'],
            aspect=record['aspect'],
            source_id=record['source_id'],
        ),
        namespace_id=record['namespace_id'],
        event_id=record['id'],
        revision=record['revision'],
        operation=record['operation'],
        captured_at=record['captured_at'],
        previous=_snapshot(record['previous']),
        current=_snapshot(record['current']),
    )


@dataclass(frozen=True, kw_only=True)
class ChangeAssessment:
    """A pure next-checkpoint decision; the caller must durably own delivery."""

    state: str
    checkpoint: PlacementChange | None = field(repr=False)

    def __post_init__(self):
        """Unknown state and missing successful evidence are never accepted."""
        if type(self.state) is not str or self.state not in (
            'accepted',
            'duplicate',
            'stale',
            'conflict',
            'gap',
            'identity_reuse',
        ):
            raise ValueError('An explicit placement assessment state is required')
        if self.checkpoint is not None:
            _change(self.checkpoint)
        elif self.state not in ('gap', 'conflict'):
            raise ValueError('This assessment requires a source checkpoint')

    @property
    def reconciliation_required(self):
        """Conflict, gaps and PK reuse cannot silently advance a checkpoint."""
        return self.state in ('conflict', 'gap', 'identity_reuse')


def _change(value):
    if type(value) is not PlacementChange:
        raise ValueError('An exact captured placement change is required')
    value.__post_init__()


def assess_placement_change(previous, incoming):
    """Fence one aspect by revision, never by allocated ID or capture timestamp.

    This is not a commit-safe event enumerator. Polling id > last_seen can still
    lose late commits; durable pending/claim/ack remains an owner dependency.
    """
    _change(incoming)
    if previous is None:
        state = 'accepted' if incoming.revision == 1 else 'gap'
    else:
        _change(previous)
        if (
            previous.identity != incoming.identity
            or previous.namespace_id != incoming.namespace_id
        ):
            raise ValueError('A checkpoint cannot cross source aspect identity')
        if incoming.event_id == previous.event_id:
            state = 'duplicate' if incoming == previous else 'conflict'
        elif incoming.revision < previous.revision:
            state = 'stale'
        elif incoming.revision == previous.revision:
            state = 'conflict'
        elif incoming.revision != previous.revision + 1:
            state = 'gap'
        elif previous.operation == 'deleted':
            state = 'identity_reuse'
        elif incoming.previous != previous.current:
            state = 'conflict'
        else:
            state = 'accepted'
    return ChangeAssessment(
        state=state, checkpoint=incoming if state == 'accepted' else previous
    )


@dataclass(frozen=True, kw_only=True)
class ClientInvalidation:
    """Minimal single-client refresh/withdraw notice with no destination data."""

    identity: SourceAspectIdentity
    client: ClientSourceIdentity = field(repr=False)
    revision: int
    reason: str

    def __post_init__(self):
        """Validate the output independently of its source projection caller."""
        _identity(self.identity)
        if type(self.client) is not ClientSourceIdentity:
            raise ValueError('An exact source client is required')
        self.client.__post_init__()
        positive_id(self.revision)
        if self.client.environment != self.identity.environment:
            raise ValueError('Invalidation client belongs to another source')
        if type(self.reason) is not str or self.reason not in ('refresh', 'withdraw'):
            raise ValueError('Invalidation is a refresh or withdrawal, not deletion')


def invalidation_for_client(change, client, *, authorize):
    """Recheck a trusted owner's current authority around a private projection.

    A scripted callback tests composition only; this is not a new native policy,
    authenticated route, historical attribution or permission to delete a copy.
    """
    _change(change)
    if type(client) is not ClientSourceIdentity or not callable(authorize):
        raise ValueError('An exact client and current source authorizer are required')
    client.__post_init__()
    if client.environment != change.identity.environment:
        raise ValueError('Client invalidation cannot cross source environments')
    if authorize(change.identity, client) is not True:
        raise PermissionError('Current change authority is unavailable')
    before = change.previous.client_id if change.previous is not None else None
    after = change.current.client_id if change.current is not None else None
    result = None
    if client.pk in (before, after):
        result = ClientInvalidation(
            identity=change.identity,
            client=client,
            revision=change.revision,
            reason='refresh' if client.pk == after else 'withdraw',
        )
    _change(change)
    client.__post_init__()
    if authorize(change.identity, client) is not True:
        raise PermissionError('Current change authority is unavailable')
    return result


def _shared_key(value):
    if type(value) is not SharedSourceKey:
        raise ValueError('An exact shared observation key is required')
    if (
        type(shared_contracts.SHARED_SCHEMA) is not str
        or shared_contracts.SHARED_SCHEMA != 'aimms.shared-state.v1'
    ):
        raise ValueError('Only the actual S5 shared-state v1 contract is admitted')
    value.__post_init__()
    if (value.entity_kind, value.aspect) == ('machine', 'placement'):
        raise ValueError('Native placement must retain its actual placement carrier')


def _shared_observation(value):
    if type(value) is not SourceObservation:
        raise ValueError('An exact stored source observation is required')
    value.__post_init__()


@dataclass(frozen=True, kw_only=True)
class SharedChangeObservation:
    """Private S5 metadata, not native authority or proof of outer commit."""

    key: SharedSourceKey = field(repr=False)
    event_id: int
    fence: int
    observation: SourceObservation = field(repr=False)
    previous: SourceObservation | None = field(repr=False)
    native_authorized: bool = field(default=False, init=False)
    source_deleted: bool = field(default=False, init=False)
    source_population_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)

    def __post_init__(self):
        """Validate private store shape independently of the adapting function."""
        _shared_key(self.key)
        shared_integer(self.event_id)
        shared_integer(self.fence)
        _shared_observation(self.observation)
        if (self.previous is None) != (self.fence == 1):
            raise ValueError('The actual per-key store fence requires prior metadata')
        if self.previous is not None:
            _shared_observation(self.previous)
        if any(
            value is not False
            for value in (
                self.native_authorized,
                self.source_deleted,
                self.source_population_complete,
                self.deletion_applied,
            )
        ):
            raise ValueError('Shared metadata cannot acquire native or deletion proof')


def shared_change_from_record(
    *, environment, namespace_id, epoch, record: SharedChange
):
    """Detach an actual S5 F01 carrier under an explicit private binding.

    The fence orders this store key, not native revisions or transaction commit.
    Producer claims and declared incarnation remain unqualified metadata.
    """
    if type(record) is not SharedChange:
        raise ValueError('An actual S5 shared change carrier is required')
    literal(environment, 128)
    positive_id(namespace_id)
    exact_epoch(epoch)
    _shared_key(record.key)
    _shared_observation(record.observation)
    if record.previous is not None:
        _shared_observation(record.previous)
    key = replace(record.key)
    if (key.environment, key.namespace_id, key.epoch) != (
        environment,
        namespace_id,
        epoch,
    ):
        raise ValueError('Shared observation does not match the supplied binding')
    return SharedChangeObservation(
        key=key,
        event_id=record.change_id,
        fence=record.fence,
        observation=replace(record.observation),
        previous=replace(record.previous) if record.previous is not None else None,
    )


def _shared_change(value):
    if type(value) is not SharedChangeObservation:
        raise ValueError('An exact private shared observation is required')
    value.__post_init__()


@dataclass(frozen=True, kw_only=True)
class SharedChangeAssessment:
    """Temporary per-key continuity, never delivery, authority or erasure proof."""

    state: str
    checkpoint: SharedChangeObservation | None = field(repr=False)

    def __post_init__(self):
        """Refuse unknown states and success without an admitted checkpoint."""
        if type(self.state) is not str or self.state not in (
            'accepted',
            'duplicate',
            'stale',
            'gap',
            'conflict',
        ):
            raise ValueError('An actual private shared continuity state is required')
        if self.checkpoint is None:
            if self.state not in ('gap', 'conflict'):
                raise ValueError('Successful continuity requires a real checkpoint')
        else:
            _shared_change(self.checkpoint)

    @property
    def reconciliation_required(self):
        """Missing or conflicting metadata needs an independently admitted scan."""
        return self.state in ('gap', 'conflict')


def assess_shared_change(previous, incoming):
    """Compare one exact key without persisting or acknowledging any delivery."""
    _shared_change(incoming)
    if previous is None:
        state = 'accepted' if incoming.fence == 1 else 'gap'
        return SharedChangeAssessment(
            state=state, checkpoint=incoming if state == 'accepted' else None
        )
    _shared_change(previous)
    if previous.key != incoming.key:
        raise ValueError('Shared observation continuity requires one exact key')
    if incoming.event_id == previous.event_id:
        state = 'duplicate' if incoming == previous else 'conflict'
    elif incoming.fence < previous.fence:
        state = 'stale'
    elif incoming.fence == previous.fence:
        state = 'conflict'
    elif incoming.fence != previous.fence + 1:
        state = 'gap'
    elif incoming.previous != previous.observation:
        state = 'conflict'
    else:
        state = 'accepted'
    return SharedChangeAssessment(
        state=state, checkpoint=incoming if state == 'accepted' else previous
    )


def _delivery_context(environment, worker_key, configuration, claim):
    if (
        type(shared_contracts.SHARED_SCHEMA) is not str
        or shared_contracts.SHARED_SCHEMA != 'aimms.shared-state.v1'
    ):
        raise ValueError('Only the admitted shared-state producer schema is supported')
    if (
        type(configuration) is not ConsumerConfiguration
        or type(claim) is not EventClaim
    ):
        raise ValueError('Exact S5 delivery configuration and claim are required')
    configuration.__post_init__()
    claim.__post_init__()
    shared_contracts.literal(environment, 128)
    shared_contracts.literal(worker_key, 128)
    if (
        claim.consumer_id != configuration.consumer_id
        or claim.namespace_epoch != configuration.namespace_epoch
        or claim.configuration_epoch != configuration.configuration_epoch
        or claim.worker_key != worker_key
    ):
        raise ValueError('Private delivery does not match its supplied binding')
    return (
        environment,
        worker_key,
        (
            configuration.consumer_id,
            configuration.namespace_id,
            configuration.namespace_epoch,
            configuration.configuration_epoch,
            configuration.consumer_key,
            configuration.aspects,
        ),
        (
            claim.consumer_id,
            claim.delivery_id,
            claim.change_id,
            claim.namespace_epoch,
            claim.configuration_epoch,
            claim.ownership_fence,
            claim.lease_token,
            claim.worker_key,
            claim.expires_at.isoformat(),
            claim.event_payload,
            claim.payload_digest,
        ),
    )


def _event_json(value):
    """Match the actual private S5 encoder without scalar or clock coercion."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('Private event metadata is not canonical JSON') from error


def _delivery_payload(environment, configuration, claim):
    try:
        payload = json.loads(claim.event_payload)
    except (ValueError, RecursionError) as error:
        raise ValueError('Private event metadata is not bounded JSON') from error
    if (
        type(payload) is not dict
        or set(payload)
        != {
            'schema',
            'change_id',
            'key',
            'revision',
            'operation',
            'previous',
            'current',
            'captured_at',
        }
        or payload['schema'] != 'aimms.shared-event.v1'
        or _event_json(payload) != claim.event_payload
    ):
        raise ValueError('The exact admitted private event v1 envelope is required')
    for value in (payload['change_id'], payload['revision']):
        shared_integer(value)
    if payload['change_id'] != claim.change_id:
        raise ValueError('The private claim identifies another event')
    values = payload['key']
    if type(values) is not dict or set(values) != {
        'schema',
        'namespace_id',
        'environment',
        'epoch',
        'entity_kind',
        'aspect',
        'key_type',
        'source_id',
        'declared_incarnation',
    }:
        raise ValueError('An exact private shared source key is required')
    if type(values['epoch']) is not str:
        raise ValueError('A literal source epoch is required')
    try:
        epoch = UUID(values['epoch'])
    except ValueError as error:
        raise ValueError('The source epoch is not an admitted UUID') from error
    key = SharedSourceKey(
        namespace_id=values['namespace_id'],
        environment=values['environment'],
        epoch=epoch,
        entity_kind=values['entity_kind'],
        aspect=values['aspect'],
        source_id=values['source_id'],
        declared_incarnation=values['declared_incarnation'],
    )
    if (
        _event_json(values) != _event_json(key_payload(key))
        or key.environment != environment
        or key.namespace_id != configuration.namespace_id
        or key.epoch != configuration.namespace_epoch
        or (key.entity_kind, key.aspect) not in configuration.aspects
    ):
        raise ValueError('The private key does not match its exact supplied binding')
    native = (key.entity_kind, key.aspect) == ('machine', 'placement')
    if native and key.declared_incarnation is not None:
        raise ValueError('Native placement has no admitted declared incarnation')
    if not native and payload['operation'] != 'observed':
        raise ValueError('Generic metadata does not declare a native operation')
    if type(payload['captured_at']) is not str:
        raise ValueError('The exact captured source clock is required')
    try:
        captured_at = datetime.fromisoformat(payload['captured_at'])
    except ValueError as error:
        raise ValueError('The captured source clock is unqualified') from error
    if captured_at.isoformat() != payload['captured_at']:
        raise ValueError('The original source clock cannot be normalized')
    return payload, key, captured_at


def _placement_payload(value):
    return (
        None
        if value is None
        else {
            'client_id': value.client_id,
            'location_id': value.location_id,
            'native_version': value.native_version,
        }
    )


def _delivery_projection(change, captured_at):
    """Explicitly project validated output, independently of received bytes."""
    if type(captured_at) is not datetime:
        raise ValueError('The exact original capture time is required')
    if type(change) is PlacementChange:
        _change(change)
        if change.captured_at.isoformat() != captured_at.isoformat():
            raise ValueError('The detached placement changed its capture time')
        key = SharedSourceKey(
            namespace_id=change.namespace_id,
            environment=change.identity.environment,
            epoch=change.identity.epoch,
            entity_kind=change.identity.entity_kind,
            aspect=change.identity.aspect,
            source_id=change.identity.source_id,
        )
        event_id, revision, operation = (
            change.event_id,
            change.revision,
            change.operation,
        )
        previous, current = (
            _placement_payload(value) for value in (change.previous, change.current)
        )
    elif type(change) is SharedChangeObservation:
        _shared_change(change)
        key = SharedSourceKey(
            namespace_id=change.key.namespace_id,
            environment=change.key.environment,
            epoch=change.key.epoch,
            entity_kind=change.key.entity_kind,
            aspect=change.key.aspect,
            source_id=change.key.source_id,
            declared_incarnation=change.key.declared_incarnation,
        )
        event_id, revision, operation = change.event_id, change.fence, 'observed'
        current = observation_payload(change.observation)
        previous = (
            None if change.previous is None else observation_payload(change.previous)
        )
    else:
        raise ValueError('An exact S7 native or generic change is required')
    return {
        'schema': 'aimms.shared-event.v1',
        'change_id': event_id,
        'key': key_payload(key),
        'revision': revision,
        'operation': operation,
        'previous': previous,
        'current': current,
        'captured_at': captured_at.isoformat(),
    }


@dataclass(frozen=True, kw_only=True)
class DeliveredChangeObservation:
    """Private decoded metadata, not proof of current ownership or authority."""

    environment: str = field(repr=False)
    worker_key: str = field(repr=False)
    configuration: ConsumerConfiguration = field(repr=False)
    claim: EventClaim = field(repr=False)
    change: PlacementChange | SharedChangeObservation = field(repr=False)
    captured_at: datetime
    ownership_verified: bool = field(default=False, init=False)
    native_authorized: bool = field(default=False, init=False)
    population_complete: bool = field(default=False, init=False)
    removal_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)
    erasure_complete: bool = field(default=False, init=False)
    tombstones: tuple = field(default=(), init=False)

    def __post_init__(self):
        """Validate the exact source-bound output without asserting current lease IO."""
        _delivery_context(
            self.environment, self.worker_key, self.configuration, self.claim
        )
        _delivery_payload(self.environment, self.configuration, self.claim)
        if (
            _event_json(_delivery_projection(self.change, self.captured_at))
            != self.claim.event_payload
        ):
            raise ValueError('The detached output differs from its received event')
        if (
            any(
                getattr(self, name) is not False
                for name in (
                    'ownership_verified',
                    'native_authorized',
                    'population_complete',
                    'removal_complete',
                    'deletion_applied',
                    'erasure_complete',
                )
            )
            or type(self.tombstones) is not tuple
            or self.tombstones
        ):
            raise ValueError(
                'Private delivery metadata cannot certify authority or erasure'
            )


# Bind the validated DTO type independently of its construction boundary.
_DELIVERED_CHANGE_TYPE = DeliveredChangeObservation


def delivery_change_from_claim(*, environment, worker_key, configuration, claim):
    """Decode metadata only; S5 alone verifies current lease ownership at ack."""
    expected_binding = _delivery_context(environment, worker_key, configuration, claim)
    payload, key, captured_at = _delivery_payload(environment, configuration, claim)
    if (key.entity_kind, key.aspect) == ('machine', 'placement'):
        change = placement_change_from_values(
            environment=environment,
            namespace_id=configuration.namespace_id,
            epoch=configuration.namespace_epoch,
            record={
                'id': payload['change_id'],
                'namespace_id': key.namespace_id,
                'epoch': key.epoch,
                'entity_kind': key.entity_kind,
                'aspect': key.aspect,
                'source_id': key.source_id,
                'revision': payload['revision'],
                'operation': payload['operation'],
                'previous': payload['previous'],
                'current': payload['current'],
                'captured_at': captured_at,
            },
        )
    else:
        change = shared_change_from_record(
            environment=environment,
            namespace_id=configuration.namespace_id,
            epoch=configuration.namespace_epoch,
            record=SharedChange(
                change_id=payload['change_id'],
                key=key,
                fence=payload['revision'],
                observation=observation_from_payload(payload['current']),
                previous=observation_from_payload(payload['previous'])
                if payload['previous'] is not None
                else None,
            ),
        )
    result = DeliveredChangeObservation(
        environment=environment,
        worker_key=worker_key,
        configuration=replace(configuration),
        claim=replace(claim),
        change=change,
        captured_at=captured_at,
    )
    if type(result) is not _DELIVERED_CHANGE_TYPE:
        raise ValueError('The delivered change constructor returned another type')
    result.__post_init__()
    if (
        _delivery_context(
            result.environment, result.worker_key, result.configuration, result.claim
        )
        != expected_binding
    ):
        raise ValueError('The returned delivery differs from its admitted intake')
    return result
