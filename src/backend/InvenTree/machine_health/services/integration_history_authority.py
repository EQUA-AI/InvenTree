"""Unwired current-actor fence for one reviewed historical signal mapping.

This is a necessary authorization check, not an enabled history reader. The
caller binds the authenticated principal and server environment, independently
qualifies adapter generation, historical mapping effective-time and completeness,
and reauthorizes every resumed request. The source callback remains trusted
internal code, not user-supplied authority. No connector is constructed here.
"""

from machine_health.services.integration_history import (
    HistoryAuthorizationError,
    HistoryReadContext,
    HistoryWindow,
)


def _context_key(context, window):
    """Validate and detach exact invocation coordinates, without coercion."""
    if type(context) is not HistoryReadContext or type(window) is not HistoryWindow:
        return None
    if (
        type(context.environment) is not str
        or not context.environment.strip()
        or type(context.generation) is not str
        or not context.generation.strip()
        or any(
            type(value) is not int or value < 1
            for value in (
                context.actor_pk,
                context.machine_pk,
                context.binding_pk,
                context.source_pk,
            )
        )
        or type(window.start_ms) is not int
        or type(window.end_ms) is not int
        or window.start_ms >= window.end_ms
    ):
        return None
    return (
        context.environment,
        context.actor_pk,
        context.machine_pk,
        context.binding_pk,
        context.source_pk,
        context.generation,
        window.start_ms,
        window.end_ms,
    )


def _authorized_snapshot(key):
    """Resolve only the selected actor, machine, source and reviewed binding."""
    from django.contrib.auth import get_user_model

    from tasks.scope import enforce_machine_scope

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets.activation import point_hash
    from assets.health_models import HealthSource, MachineSignalBinding
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.models import AssetMachine
    from machine_health.connectors.cosmos_pumphouse import CosmosPumphouseConnector

    _, actor_pk, machine_pk, binding_pk, source_pk, _, _, _ = key
    user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if user is None or not fresh_role(user, 'work_order'):
        return None
    machine = (
        AssetMachine.objects
        .select_related('parent')
        .filter(
            pk=machine_pk,
            active=True,
            client_id__in=explicitly_granted_client_ids(user),
        )
        .first()
    )
    if machine is None:
        return None
    enforce_machine_scope(user, machine)
    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    if (
        station is None
        or station.asset_type != 'pumphouse'
        or not station.active
        or station.client_id != machine.client_id
        or not station.source_entity_uuid
    ):
        return None
    enforce_machine_scope(user, station)

    # Select only the station-list JSON key; never project source configuration
    # or credential references into this boundary.
    source = (
        HealthSource.objects
        .filter(
            pk=source_pk,
            active=True,
            client_id=machine.client_id,
            connector_type=CosmosPumphouseConnector.key,
        )
        .values('pk', 'config__stations')
        .first()
    )
    if source is None:
        return None
    stations = source['config__stations']
    if isinstance(stations, str):
        stations = [stations]
    if (
        not isinstance(stations, list)
        or str(station.source_entity_uuid) not in stations
    ):
        return None

    binding = (
        MachineSignalBinding.objects
        .select_related('dictionary_point')
        .filter(
            pk=binding_pk,
            machine_id=machine_pk,
            source_id=source_pk,
            dictionary_point__station_id=station.pk,
            dictionary_point__machine_id=machine_pk,
            dictionary_point__status='approved',
            dictionary_point__unit_status__in=['verified', 'unitless'],
        )
        .exclude(dictionary_point__data_type='unknown')
        .first()
    )
    if binding is None:
        return None
    point = binding.dictionary_point
    if (
        point.path != binding.external_key
        or point.unit != binding.unit
        or not binding.dictionary_hash
        or binding.dictionary_hash != point_hash(point)
    ):
        return None
    return (
        machine.pk,
        machine.client_id,
        station.pk,
        str(station.source_entity_uuid),
        binding.pk,
        binding.source_id,
        binding.dictionary_point_id,
        binding.external_key,
        binding.unit,
        binding.dictionary_hash,
    )


def make_history_authorizer(actor, context, window):
    """Bind current scope and mapping to a single trusted history invocation.

    Rechecks narrow in-read changes but are not an atomic snapshot or a proof
    of historical attribution, generation, complete source coverage or current
    rights after the last check. Initial failures have one generic error.
    """
    key = _context_key(context, window)
    if (
        key is None
        or actor is None
        or not getattr(actor, 'is_authenticated', False)
        or type(getattr(actor, 'pk', None)) is not int
        or actor.pk != key[1]
    ):
        raise HistoryAuthorizationError('Current history authority is unavailable')
    try:
        initial = _authorized_snapshot(key)
    except Exception:
        initial = None
    if initial is None:
        raise HistoryAuthorizationError('Current history authority is unavailable')

    def authorize(current_context, current_window):
        if _context_key(current_context, current_window) != key:
            return False
        try:
            return _authorized_snapshot(key) == initial
        except Exception:
            return False

    return authorize
