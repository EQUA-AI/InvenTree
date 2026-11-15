"""Native asset-view adapters for the default-off S2 read seam.

This module is not a Foundry transport or a strict integration export. The
existing native GET implementation remains the functional control.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from .integration_s2_provider import (
    AcceptedPage,
    ReadAuthorityError,
    ReadBinding,
    ReadError,
    ReadShape,
    ReadTimeoutError,
    ReadUnavailableError,
    route_native_read,
)


@dataclass(frozen=True, repr=False)
class NativeReadAdapter:
    """Trusted server policy, independent of provider claims and request data.

    Both field-policy callbacks are mandatory. No generic serializer-based
    default is supplied. Source/field/count/generation/collation qualification
    belongs to the adapter owner; a True-returning fake is not such proof.
    """

    environment: str
    plan_for: Callable
    authorize_fields: Callable
    authorize_page: Callable


def _shape(request, domain):
    """Describe only implemented native shapes; unknown requests remain native."""
    from rest_framework.exceptions import ValidationError

    from .location_api import LocationPagination, _boolean, _integer
    from .machine_profile import CRITICALITY_VALUES
    from .serializers import AssetMachineSerializer

    if domain == 'locations':
        return _location_shape(request)
    supported = {
        'limit',
        'offset',
        'active',
        'active_only',
        'unassigned',
        'location',
        'include_descendants',
        'machine',
        'search',
        'criticality',
        'has_alarms',
    }
    if domain != 'machines' or set(request.query_params) - supported:
        return None
    if any(
        len(values) != 1 or len(values[0]) > 1024
        for _, values in request.query_params.lists()
    ):
        return None
    params = request.query_params
    try:
        active = (
            str(_boolean(params.get('active'), 'active')).lower()
            if 'active' in params
            else 'any'
        )
        active_only = _boolean(params.get('active_only'), 'active_only')
        unassigned = _boolean(params.get('unassigned'), 'unassigned')
        include = _boolean(
            params.get('include_descendants'), 'include_descendants', default=True
        )
        location = (
            str(_integer(params['location'], 'location'))
            if params.get('location')
            else ''
        )
        machine = (
            str(_integer(params['machine'], 'machine')) if params.get('machine') else ''
        )
        criticality = params.get('criticality') or ''
        if (location and unassigned) or (
            criticality and criticality not in CRITICALITY_VALUES
        ):
            return None
        alarms = _boolean(params.get('has_alarms'), 'has_alarms')
    except ValidationError:
        # Preserve native validation order/status, including unavailable scope.
        return None
    pagination = LocationPagination()
    return ReadShape(
        domain=domain,
        filters=(
            ('active', active),
            ('active_only', str(active_only).lower()),
            ('unassigned', str(unassigned).lower()),
            ('include_descendants', str(include).lower()),
            ('location', location),
            ('machine', machine),
            ('search', params.get('search', '').strip()),
            ('criticality', criticality),
            ('has_alarms', str(alarms).lower()),
        ),
        ordering='native.machine.db-name-pk.v1',
        fields=tuple(
            sorted((
                *AssetMachineSerializer.Meta.fields,
                'physical_location',
                'placement_version',
                'open_work',
                'criticality',
                'condition',
            ))
        ),
        limit=pagination.get_limit(request),
        offset=pagination.get_offset(request),
    )


def _location_shape(request):
    """Preserve location-specific casefold, archive and optional-count semantics."""
    from rest_framework.exceptions import ValidationError

    from .location_api import LocationPagination, _boolean, _integer

    params = request.query_params
    if set(params) - {
        'limit',
        'offset',
        'parent',
        'client',
        'search',
        'active_only',
        'include_counts',
        'active',
    } or any(len(values) != 1 or len(values[0]) > 1024 for _, values in params.lists()):
        return None
    try:
        parent = params.get('parent')
        parent = (
            '*'
            if parent is None
            else 'root'
            if parent == 'root'
            else str(_integer(parent, 'parent'))
        )
        client = (
            str(_integer(params['client'], 'client')) if params.get('client') else ''
        )
        active_only = _boolean(params.get('active_only'), 'active_only')
        counts = _boolean(params.get('include_counts'), 'include_counts')
        active = (
            str(_boolean(params.get('active'), 'active')).lower()
            if counts and 'active' in params
            else 'any'
        )
    except ValidationError:
        return None
    pagination = LocationPagination()
    fields = (
        'pk',
        'client',
        'parent',
        'name',
        'code',
        'kind',
        'description',
        'timezone',
        'effective_timezone',
        'archived',
        'version',
        'path',
        'has_children',
        'created_at',
        'updated_at',
    )
    return ReadShape(
        domain='locations',
        filters=(
            ('parent', parent),
            ('client', client),
            ('search', params.get('search', '').strip().casefold()),
            ('active_only', str(active_only).lower()),
            ('include_counts', str(counts).lower()),
            ('active', active),
        ),
        ordering='native.location.casefold-name-pk.v1',
        fields=tuple(sorted((*fields, 'counts') if counts else fields)),
        limit=pagination.get_limit(request),
        offset=pagination.get_offset(request),
    )


def _request_scope(request, actor):
    """Fingerprint current request restrictions, never credential bytes."""
    from oauth2_provider.contrib.rest_framework.authentication import (
        OAuth2Authentication,
    )
    from oauth2_provider.models import get_access_token_model
    from rest_framework.authentication import BasicAuthentication, SessionAuthentication
    from rest_framework.request import ForcedAuthentication

    if isinstance(request.successful_authenticator, OAuth2Authentication):
        token = (
            get_access_token_model()
            .objects.filter(pk=request.auth.pk, user_id=actor.pk)
            .values('pk', 'scope', 'expires', 'application_id')
            .first()
        )
        if (
            token is None
            or type(token['scope']) is not str
            or len(token['scope']) > 10000
        ):
            return None
        coordinates = (
            actor.pk,
            token['pk'],
            sorted(token['scope'].split()),
            str(token['expires']),
            token['application_id'],
        )
        return (
            'native-ui.oauth.v1:'
            + hashlib.sha256(json.dumps(coordinates).encode()).hexdigest()
        )
    if request.auth is None and isinstance(
        request.successful_authenticator,
        (BasicAuthentication, SessionAuthentication, ForcedAuthentication),
    ):
        return 'native-ui.non-token.v1'
    return None


def _current_binding(request, shape, adapter):
    """Compose the current S1 request intake, native scope and explicit grants."""
    from rest_framework.exceptions import PermissionDenied

    from .integration_authority import (
        explicitly_granted_client_ids,
        integration_actor_from_request,
    )
    from .locations import client_ids

    if shape is None:
        return None
    actor = integration_actor_from_request(request, required_role='work_order')
    if actor is None:
        return None
    request_scope = _request_scope(request, actor)
    if request_scope is None:
        return None
    try:
        native_ids = client_ids(actor)
    except PermissionDenied:
        return None
    grants = {row['client_id'] for row in explicitly_granted_client_ids(actor)}
    # Replacing a broader native UI population by a narrower export would
    # change native counts. Unsupported native shapes remain native instead.
    if not native_ids or not native_ids.issubset(grants) or len(native_ids) > 10000:
        return None
    binding = ReadBinding(
        shape=shape,
        environment=adapter.environment,
        actor_pk=actor.pk,
        client_ids=tuple(sorted(native_ids)),
        authority='s1.current-request.native-scope.explicit-grant.v1',
        request_scope=request_scope,
    )
    if adapter.authorize_fields(request, binding) is not True:
        return None
    # The selected location/parent is an independently authorized link,
    # not merely a filter string that a remote capability may bless.
    filters = dict(shape.filters)
    linked = filters.get('location' if shape.domain == 'machines' else 'parent')
    if linked and linked not in ('root', '*'):
        from .models import AssetLocation

        if not AssetLocation.objects.filter(
            pk=int(linked), client_id__in=native_ids
        ).exists():
            return None
    return binding


def _authorize_rows(request, binding, page):
    """A source proof cannot replace current local row/client authority."""
    from tasks.scope import scoped_machines

    from .integration_authority import integration_actor_from_request
    from .locations import locations_for

    actor = integration_actor_from_request(request, required_role='work_order')
    if actor is None:
        raise ReadAuthorityError()
    ids = {row['pk'] for row in page.rows}
    rows = (
        scoped_machines(actor)
        if binding.shape.domain == 'machines'
        else locations_for(actor)
    )
    allowed = set(
        rows.filter(pk__in=ids, client_id__in=binding.client_ids).values_list(
            'pk', flat=True
        )
    )
    if allowed != ids:
        raise ReadAuthorityError()


def serve_native_read(view, request, *, domain, native):
    """Use no optional provider/policy/authority work when integration is off.

    A configured server object is not client-selectable. Its policy callbacks
    must independently authorize the complete shape and page, including
    linked fields, searched properties, counts, Work and health. No such real
    adapter is configured or qualified here; the local controls use fakes.
    """
    from django.conf import settings

    from rest_framework.response import Response

    from .location_api import LocationPagination

    adapter = getattr(settings, 'AIMMS_ASSET_READ_ADAPTER', None)
    if adapter is None:
        return route_native_read(native=native, binding_factory=lambda: None)
    try:
        if type(adapter) is not NativeReadAdapter or not all(
            callable(fn)
            for fn in (
                adapter.plan_for,
                adapter.authorize_fields,
                adapter.authorize_page,
            )
        ):
            raise ReadError()
        shape = _shape(request, domain)
        initial = None

        def binding_factory():
            nonlocal initial
            try:
                current = _current_binding(request, shape, adapter)
                if initial is None:
                    initial = current
                return current
            except ReadError:
                raise
            except TimeoutError:
                raise ReadTimeoutError() from None
            except Exception:
                raise ReadUnavailableError() from None

        def select_plan(binding):
            try:
                return adapter.plan_for(binding)
            except ReadError:
                raise
            except TimeoutError:
                raise ReadTimeoutError() from None
            except Exception:
                raise ReadUnavailableError() from None

        result = route_native_read(
            native=native, binding_factory=binding_factory, provider_factory=select_plan
        )
        if type(result) is not AcceptedPage:
            return result
        if shape is None or initial is None:
            raise ReadError()
        try:
            _authorize_rows(request, initial, result)
            if adapter.authorize_page(request, initial, result) is not True:
                raise ReadAuthorityError()
            pagination = LocationPagination()
            pagination.limit = shape.limit
            pagination.offset = shape.offset
            pagination.count = result.total
            pagination.request = request
            response = pagination.get_paginated_response(result.rows)
            # Policy and response construction can perform dependent work.
            # Neither a row transfer nor a changed grant may outlive that work.
            _authorize_rows(request, initial, result)
            if binding_factory() != initial:
                raise ReadAuthorityError()
            return response
        except ReadError:
            raise
        except TimeoutError:
            raise ReadTimeoutError() from None
        except Exception:
            raise ReadUnavailableError() from None
    except ReadError as exc:
        return Response(
            {'code': exc.code, 'detail': str(exc)},
            status=403 if exc.code == 'denied' else 503,
        )
