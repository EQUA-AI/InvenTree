"""Authenticated source-local recorded-history intake; no enabled live reader.

Only a trusted internal caller can supply the explicit synthetic fixture adapter.
Nothing registers a production HTTP route, opens a source, widens native scope,
changes ingestion, or turns this result into a durable cursor/authority grant.
"""

from dataclasses import dataclass, field

from assets.integration_authority import (
    integration_actor_from_current_ai,
    integration_actor_from_request,
)
from machine_health.services import integration_s6_wire as wire
from machine_health.services.integration_history import (
    MAX_HISTORY_READS,
    MAX_HISTORY_SAMPLES_PER_READ,
    MAX_HISTORY_SAMPLES_TOTAL,
    HistoryAdapterError,
    HistoryAuthorizationError,
    HistoryProtocolError,
    HistoryReadContext,
)
from machine_health.services.integration_history_authority import (
    make_history_authorizer,
)
from machine_health.services.integration_s6_adapter import (
    FixtureHistoryAdapter,
    HistoryCapabilityError,
    context_key,
    window_key,
)


@dataclass(frozen=True)
class S6ReadOutcome:
    """One observation only; unavailable is neither empty history nor deletion."""

    state: str
    payload: bytes | None = field(default=None, repr=False)
    population_complete: bool = field(default=False, init=False)
    revision_complete: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.s6.read-result.v1', init=False)

    def __post_init__(self):
        """Reject coercion, unsupported state and false completeness claims."""
        if type(self.state) is not str or self.state not in (
            'available',
            'unavailable',
            'unsupported',
        ):
            raise ValueError('An explicit S6 read state is required')
        if (
            type(self.schema_version) is not str
            or self.schema_version != 'aimms.s6.read-result.v1'
        ):
            raise ValueError('An explicit S6 read schema is required')
        if self.state == 'available':
            if (
                type(self.payload) is not bytes
                or not 1 <= len(self.payload) <= wire.MAX_HISTORY_BYTES
            ):
                raise ValueError('A bounded source observation is required')
        elif self.payload is not None:
            raise ValueError('An unavailable observation has no payload')
        if (
            type(self.population_complete) is not bool
            or self.population_complete is not False
            or type(self.revision_complete) is not bool
            or self.revision_complete is not False
        ):
            raise ValueError(
                'An observation cannot claim population or revision completeness'
            )


_OUTCOME_TYPE = S6ReadOutcome


def _negative(state):
    result = S6ReadOutcome(state=state)
    if (
        type(result) is not _OUTCOME_TYPE
        or result.state != state
        or result.payload is not None
    ):
        raise wire.SourceSerializationError('Recorded observation is unavailable')
    _OUTCOME_TYPE.__post_init__(result)
    return result


def _actor(request):
    try:
        return integration_actor_from_request(request, required_role='work_order')
    except MemoryError:
        raise
    except Exception:
        return None


def _mapping_matches(adapter, context):
    from assets.health_models import MachineSignalBinding

    mapping = adapter.mapping.as_dict()
    row = (
        MachineSignalBinding.objects
        .filter(
            pk=context.binding_pk,
            machine_id=context.machine_pk,
            source_id=context.source_pk,
        )
        .values(
            'machine__client_id',
            'dictionary_point__station_id',
            'dictionary_point_id',
            'dictionary_point__station__source_entity_uuid',
            'external_key',
            'unit',
            'dictionary_hash',
        )
        .first()
    )
    if row is None:
        return False
    return (
        mapping['client_pk'] == row['machine__client_id']
        and mapping['station_pk'] == row['dictionary_point__station_id']
        and mapping['dictionary_point_pk'] == row['dictionary_point_id']
        and mapping['station_uuid']
        == str(row['dictionary_point__station__source_entity_uuid'])
        and mapping['external_key'] == row['external_key']
        and mapping['unit'] == row['unit']
        and mapping['dictionary_hash'] == row['dictionary_hash']
    )


def read_recorded_history(
    request,
    *,
    environment,
    machine_pk,
    source_pk,
    binding_pk,
    window,
    adapter=None,
    max_samples_per_read=MAX_HISTORY_SAMPLES_PER_READ,
    max_samples_total=MAX_HISTORY_SAMPLES_TOTAL,
    max_reads=MAX_HISTORY_READS,
    split_recoverable_errors=False,
    max_elapsed_seconds=30,
):
    """Compose actual request/current authority and bounded synthetic history.

    Adapter and environment are server-owned, not HTTP actor/tag/generation
    claims. The absence of an admitted fixture returns unsupported, never native
    fallback, complete-empty success or a real-source availability claim.
    """
    return _read_recorded_history(
        lambda: _actor(request),
        environment=environment,
        machine_pk=machine_pk,
        source_pk=source_pk,
        binding_pk=binding_pk,
        window=window,
        adapter=adapter,
        max_samples_per_read=max_samples_per_read,
        max_samples_total=max_samples_total,
        max_reads=max_reads,
        split_recoverable_errors=split_recoverable_errors,
        max_elapsed_seconds=max_elapsed_seconds,
    )


class _AIReadObservation:
    """Private invocation-local source observation, not a transport grant."""

    __slots__ = ('_check', '_outcome')

    def __init__(self, outcome, check):
        """Keep the source result and independent check private to this call."""
        self._outcome, self._check = outcome, check

    @property
    def outcome(self):
        """Return the private original result, never a model projection."""
        return self._outcome

    def reauthorize(self):
        """Refresh authentication, native source authority and independent bytes."""
        try:
            return self._check is not None and self._check() is True
        except MemoryError:
            raise
        except Exception:
            return False


_AI_OBSERVATION_TYPE = _AIReadObservation


def read_recorded_history_for_current_ai(
    *,
    environment,
    machine_pk,
    source_pk,
    binding_pk,
    window,
    adapter=None,
    max_samples_per_read=MAX_HISTORY_SAMPLES_PER_READ,
    max_samples_total=MAX_HISTORY_SAMPLES_TOTAL,
    max_reads=MAX_HISTORY_READS,
    split_recoverable_errors=False,
    max_elapsed_seconds=30,
):
    """Enter S6 through the live ASGI boundary and fresh native S1 actor.

    No request-shaped adapter, actor parameter, public authorization callback
    or cached result is accepted. The private returned check is valid only in
    this original authenticated invocation and does not confer source access.
    """
    from ai.core.auth import get_current_principal

    original = get_current_principal()

    def resolve_actor():
        try:
            if original is None or get_current_principal() is not original:
                return None
            actor = integration_actor_from_current_ai(required_role='work_order')
            return actor if get_current_principal() is original else None
        except MemoryError:
            raise
        except Exception:
            return None

    observations = []
    outcome = _read_recorded_history(
        resolve_actor,
        environment=environment,
        machine_pk=machine_pk,
        source_pk=source_pk,
        binding_pk=binding_pk,
        window=window,
        adapter=adapter,
        max_samples_per_read=max_samples_per_read,
        max_samples_total=max_samples_total,
        max_reads=max_reads,
        split_recoverable_errors=split_recoverable_errors,
        max_elapsed_seconds=max_elapsed_seconds,
        _observation=observations,
    )
    check = observations[0] if outcome.state == 'available' else None
    result = _AIReadObservation(outcome, check)
    if (
        type(result) is not _AI_OBSERVATION_TYPE
        or result.outcome is not outcome
        or result._check is not check
    ):
        raise wire.SourceSerializationError('Recorded observation is unavailable')
    if check is not None and check() is not True:
        return _AI_OBSERVATION_TYPE(_negative('unavailable'), None)
    return result


def _read_recorded_history(
    resolve_actor,
    *,
    environment,
    machine_pk,
    source_pk,
    binding_pk,
    window,
    adapter,
    max_samples_per_read,
    max_samples_total,
    max_reads,
    split_recoverable_errors,
    max_elapsed_seconds,
    _observation=None,
):
    """Share the existing source pipeline, not either intake's credentials."""
    if type(environment) is not str or not environment.strip():
        raise ValueError('A literal trusted source environment is required')
    for value in (machine_pk, source_pk, binding_pk):
        if type(value) is not int or value < 1:
            raise ValueError('Positive native source coordinates are required')
    window_key(window)
    for value, maximum in (
        (max_samples_per_read, MAX_HISTORY_SAMPLES_PER_READ),
        (max_samples_total, MAX_HISTORY_SAMPLES_TOTAL),
        (max_reads, MAX_HISTORY_READS),
        (max_elapsed_seconds, 30),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError('A bounded positive source budget is required')
    if type(split_recoverable_errors) is not bool:
        raise ValueError('An explicit retry policy is required')
    actor = resolve_actor()
    if actor is None:
        return _negative('unavailable')
    if type(adapter) is not FixtureHistoryAdapter:
        return _negative('unsupported')
    try:
        context = HistoryReadContext(
            environment=environment,
            actor_pk=actor.pk,
            machine_pk=machine_pk,
            source_pk=source_pk,
            binding_pk=binding_pk,
            generation=adapter.generation,
        )
        key, span = context_key(context), window_key(window)
        authorize = make_history_authorizer(actor, context, window)
    except MemoryError:
        raise
    except Exception:
        return _negative('unavailable')

    def current(current_context, requested):
        try:
            if context_key(current_context) != key or window_key(requested) != span:
                return False
            fresh = resolve_actor()
            if fresh is None or fresh.pk != key[1]:
                return False
            if authorize(current_context, requested) is not True:
                return False
            if _mapping_matches(adapter, current_context) is not True:
                return False
            # Mapping reads can change point/source/grant as well as OAuth state.
            if authorize(current_context, requested) is not True:
                return False
            fresh = resolve_actor()
            return fresh is not None and fresh.pk == key[1]
        except MemoryError:
            raise
        except Exception:
            return False

    try:
        if current(context, window) is not True:
            return _negative('unavailable')
        if not adapter.supports(context, window):
            if current(context, window) is not True:
                return _negative('unavailable')
            return _negative('unsupported')
        captured = adapter.read_history(
            context,
            window,
            authorize=current,
            max_samples_per_read=max_samples_per_read,
            max_samples_total=max_samples_total,
            max_reads=max_reads,
            split_recoverable_errors=split_recoverable_errors,
            max_elapsed_seconds=max_elapsed_seconds,
        )
        payload = wire.encode_history_read(
            adapter, captured, expected_context=context, expected_window=window
        )
        wire.check_history_payload(
            payload, adapter, captured, expected_context=context, expected_window=window
        )
        result = S6ReadOutcome(state='available', payload=payload)
        if (
            type(result) is not _OUTCOME_TYPE
            or type(result.state) is not str
            or result.state != 'available'
            or type(result.payload) is not bytes
            or result.payload != payload
        ):
            raise wire.SourceSerializationError('Recorded observation is unavailable')
        _OUTCOME_TYPE.__post_init__(result)
        wire.check_history_payload(
            result.payload,
            adapter,
            captured,
            expected_context=context,
            expected_window=window,
        )
        if current(context, window) is not True:
            return _negative('unavailable')
        if _observation is not None:

            def reauthorize():
                if current(context, window) is not True:
                    return False
                _OUTCOME_TYPE.__post_init__(result)
                wire.check_history_payload(
                    result.payload,
                    adapter,
                    captured,
                    expected_context=context,
                    expected_window=window,
                )
                return current(context, window) is True

            _observation.append(reauthorize)
            if current(context, window) is not True:
                return _negative('unavailable')
        return result
    except MemoryError:
        raise
    except Exception as error:
        # Preserve source faults only while both request and source remain current.
        if current(context, window) is not True:
            return _negative('unavailable')
        if isinstance(error, HistoryAuthorizationError):
            return _negative('unavailable')
        if isinstance(error, HistoryCapabilityError):
            return _negative('unsupported')
        if isinstance(error, wire.SourceSerializationError):
            raise wire.SourceSerializationError(
                'Recorded observation is unavailable'
            ) from None
        if isinstance(error, HistoryProtocolError):
            raise HistoryProtocolError('Invalid recorded history') from None
        raise HistoryAdapterError('History source failed') from None
