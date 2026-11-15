"""Minimal authenticated OSDK pilot, independent of native list/AI serving."""

import hashlib
import json
from dataclasses import dataclass

from django.conf import settings
from django.urls import path

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from integration_state.palantir_transport import (
    FIELDS,
    TransportError,
    config_fingerprint,
    fetch_object,
    validate_config,
)


@dataclass(frozen=True, repr=False)
class NativeBinding:
    """Private coordinates, including both object and client incarnations."""

    client_pk: int
    client_incarnation: str
    incarnation: str
    fingerprint: str


def _binding(request, config, domain, pk):
    """Rehydrate native role, token restrictions, scope, grant and incarnation."""
    from tasks.scope import scoped_machines

    from assets.integration_authority import (
        explicitly_granted_client_ids,
        integration_actor_from_request,
    )
    from assets.integration_s2_native import _request_scope
    from assets.locations import locations_for
    from InvenTree.restore_hold import restore_hold_enabled

    if restore_hold_enabled() or settings.DATABASE_ROUTERS:
        raise TransportError()

    actor = integration_actor_from_request(request, required_role='work_order')
    if actor is None:
        raise TransportError('denied')
    scope = _request_scope(request, actor)
    if scope is None:
        raise TransportError('denied')
    grants = {row['client_id'] for row in explicitly_granted_client_ids(actor)}
    rows = scoped_machines(actor) if domain == 'machines' else locations_for(actor)
    row = rows.filter(pk=pk, client_id__in=grants).first()
    if row is None or config.authorize_fields(request, domain, pk, FIELDS) is not True:
        raise TransportError('denied')
    client = row.client
    incarnation = str(row.uuid) if domain == 'machines' else row.created_at.isoformat()
    coordinates = {
        'actor': actor.pk,
        'scope': scope,
        'pk': row.pk,
        'client': client.pk,
        'client_created': client.created_at.isoformat(),
        'client_code': client.code,
        'incarnation': incarnation,
        'name': row.name,
        'state': [row.active, row.placement_version]
        if domain == 'machines'
        else [row.archived, row.version],
        'configuration': config_fingerprint(config),
    }
    digest = hashlib.sha256(
        json.dumps(coordinates, sort_keys=True).encode()
    ).hexdigest()
    return NativeBinding(
        row.client_id, client.created_at.isoformat(), incarnation, digest
    )


def read_object(request, *, domain, pk):
    """Check authority before credentials/I/O and again before returning bytes.

    Sequential observations detect changes at these checks, not ABA changes or
    revocation after the final check. No result is cached or used to authorize
    another object, a source population, historical evidence or an AI answer.
    """
    config = getattr(settings, 'AIMMS_PALANTIR_TRANSPORT', None)
    if config is None:
        return None
    validate_config(config)
    if (
        type(domain) is not str
        or domain not in ('machines', 'locations')
        or type(pk) is not int
        or not 1 <= pk <= (1 << 63) - 1
    ):
        raise TransportError('invalid')
    mapping = next((m for m in config.mappings if m.domain == domain), None)
    if mapping is None:
        return None
    before = _binding(request, config, domain, pk)
    result = fetch_object(
        config,
        mapping,
        pk=pk,
        client_pk=before.client_pk,
        client_incarnation=before.client_incarnation,
        incarnation=before.incarnation,
    )
    if (
        getattr(settings, 'AIMMS_PALANTIR_TRANSPORT', None) is not config
        or _binding(request, config, domain, pk) != before
    ):
        raise TransportError('denied')
    return {
        'schema_version': 'aimms.palantir.object.v1',
        'source': 'palantir_osdk',
        'consistency': 'single_observation',
        'object': result,
    }


class PalantirObjectView(APIView):
    """Authenticated GET only; no SDK names, filters or credentials from callers."""

    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'options']

    def get(self, request, domain, pk):
        """Use fixed status/errors and suppress intermediary/browser caching."""
        try:
            result = read_object(request, domain=domain, pk=pk)
            response = (
                Response(result, status=200)
                if result is not None
                else Response({'detail': 'Not found.'}, status=404)
            )
        except TransportError as error:
            status = {'denied': 403, 'timeout': 504, 'invalid': 502, 'budget': 502}.get(
                error.code, 503
            )
            response = Response({'detail': str(error)}, status=status)
        except Exception:
            response = Response(
                {'detail': 'Palantir read refused: unavailable'}, status=503
            )
        response['Cache-Control'] = 'private, no-store'
        return response


urlpatterns = [
    path(
        'machines/<int:pk>/',
        PalantirObjectView.as_view(),
        {'domain': 'machines'},
        name='palantir-machine',
    ),
    path(
        'locations/<int:pk>/',
        PalantirObjectView.as_view(),
        {'domain': 'locations'},
        name='palantir-location',
    ),
]
