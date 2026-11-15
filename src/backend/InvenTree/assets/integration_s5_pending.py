"""Optional current-request placement metadata; native reads remain authoritative.

No default adapter, remote client or reader enrollment is installed here.
Request labels and headers are never configuration or proof authority.
"""

import json
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass, replace

from django.db import DatabaseError, connection

from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from assets.integration_authority import integration_actor_from_request
from assets.integration_machine_location import (
    _authorized_machine,
    read_machine_placement,
)
from assets.integration_s5_ack import (
    _binding_snapshot,
    _locked_context,
    _metadata_transaction,
    _observation_snapshot,
)
from assets.integration_s5_contracts import PlacementServingAdapter
from assets.locations import client_ids
from assets.models import AssetMachine
from integration_state.models import (
    PlacementAcknowledgement,
    ProjectionEnrollment,
    SourceNamespace,
)
from integration_state.services import (
    PlacementStateError,
    _literal,
    _positive_id,
    _require_default_routing,
)

SCHEMA = 'aimms.placement.sync.v1'


def placement_read_failure(request):
    """Withhold the native page and refresh authority after a reached store fault."""
    try:
        actor = integration_actor_from_request(request, required_role='work_order')
        if actor is None or not client_ids(actor):
            raise PermissionDenied('Placement read is no longer authorized')
        if integration_actor_from_request(request, required_role='work_order') is None:
            raise PermissionDenied('Placement request authority changed')
    except DatabaseError:
        # An unavailable authorization store is not a successful data response.
        pass
    return Response(
        {
            'code': 'unavailable',
            'detail': 'Placement read authority or state is unavailable.',
        },
        status=503,
    )


class PlacementMetadataResponse(Response):
    """Recheck after actual encoding/post-render work, including its error path."""

    def __init__(self, original, data, guard):
        """Preserve the native response envelope, not just its JSON fields."""
        super().__init__(
            data,
            status=original.status_code,
            template_name=original.template_name,
            headers=dict(original.items()),
            exception=original.exception,
            content_type=original.content_type,
        )
        self.cookies = copy(original.cookies)
        self._post_render_callbacks = list(original._post_render_callbacks)
        self._placement_guard = guard

    def _failure(self, status):
        """Replace all old bytes with a fixed non-disclosing failure envelope."""
        self.status_code = status
        self.exception = True
        self.data = {
            'code': 'denied' if status == 403 else 'unavailable',
            'detail': 'Placement read authority or state is unavailable.',
        }
        self['Content-Type'] = 'application/json'
        self.content = json.dumps(self.data, separators=(',', ':')).encode('utf-8')
        return self

    def render(self):
        """No stale native row or optional metadata survives a reached refusal."""
        try:
            self._placement_guard()
            result = super().render()
            self._placement_guard()
            if result is not self:
                raise PlacementStateError('Unqualified replacement response')
            return result
        except Exception as exc:
            try:
                self._placement_guard()
            except PermissionDenied:
                return self._failure(403)
            except (PlacementStateError, DatabaseError):
                return self._failure(503)
            if isinstance(exc, PermissionDenied):
                return self._failure(403)
            if isinstance(exc, (PlacementStateError, DatabaseError)):
                return self._failure(503)
            raise


def _native_snapshot(request, adapter, rows):
    """Fence only the native placement/population this response already contains."""
    actor = integration_actor_from_request(request, required_role='work_order')
    if actor is None or actor.pk != adapter.actor_pk:
        raise PermissionDenied('Placement read is no longer authorized')
    identifiers = []
    for row in rows:
        identifier = row.get('pk')
        _positive_id(identifier)
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise PlacementStateError('Duplicate native placement row')
    scope = client_ids(actor)
    source = {
        machine.pk: (
            machine.client_id,
            machine.physical_location_id,
            machine.placement_version,
        )
        for machine in AssetMachine.objects.filter(
            pk__in=identifiers, client_id__in=scope
        ).only('pk', 'client_id', 'physical_location_id', 'placement_version')
    }
    if set(source) != set(identifiers):
        raise PermissionDenied('Native placement population is no longer authorized')
    for row in rows:
        client, location, version = source[row['pk']]
        node = row.get('physical_location')
        if (
            row.get('client') != client
            or row.get('placement_version') != version
            or (node is not None and node.get('pk') != location)
        ):
            raise PlacementStateError('Native placement changed during response work')
    current_actor = integration_actor_from_request(request, required_role='work_order')
    if current_actor is None or current_actor.pk != actor.pk:
        raise PermissionDenied('Placement request authority changed')
    return frozenset(scope), source


def select_placement_adapter(request, *, factory):
    """Select the native-only placement capability before either data reader."""
    if not callable(factory):
        raise PlacementStateError('A server placement adapter factory is required')
    adapter = factory(request)
    if adapter is not None and type(adapter) is not PlacementReadAdapter:
        raise PlacementStateError('A server-selected placement adapter is required')
    if adapter is not None:
        adapter.__post_init__()
        _require_default_routing()
    if integration_actor_from_request(request, required_role='work_order') is None:
        raise PermissionDenied('Placement request authority changed during selection')
    return adapter


def append_placement_metadata(request, response, *, adapter):
    """Add only server-selected metadata, preserving native page/count semantics."""
    if response.status_code != 200:
        return response
    adapter.__post_init__()
    data = response.data
    if (
        not isinstance(data, dict)
        or type(data.get('results')) not in (list, tuple)
        or len(data['results']) > 100
    ):
        raise PlacementStateError('Placement metadata requires a bounded native page')
    native = _native_snapshot(request, adapter, data['results'])
    rows = []
    for row in data['results']:
        if not isinstance(row, dict):
            raise PlacementStateError(
                'Placement metadata requires native row identities'
            )
        current = {key: value for key, value in row.items() if key != 'placement_sync'}
        metadata = metadata_for_machine(request, row.get('pk'), adapter=adapter)
        if metadata is not None:
            current['placement_sync'] = metadata
        rows.append(current)

    def guard():
        if _native_snapshot(request, adapter, rows) != native:
            raise PlacementStateError('Native placement changed before emission')
        for row in rows:
            if 'placement_sync' not in row:
                continue
            current = metadata_for_machine(request, row['pk'], adapter=adapter)
            if current is None:
                raise PermissionDenied('Placement metadata is no longer authorized')
            if current != row['placement_sync']:
                raise PlacementStateError(
                    'Placement visibility changed before emission'
                )
        if _native_snapshot(request, adapter, rows) != native:
            raise PlacementStateError('Native placement changed after metadata work')

    guard()
    return PlacementMetadataResponse(response, {**data, 'results': rows}, guard)


@dataclass(frozen=True, repr=False)
class PlacementReadAdapter:
    """Server-selected per-reader opt-in plus independent metadata field policy."""

    actor_pk: int
    namespace_id: int
    projection: str
    authorize_metadata: Callable
    serving: PlacementServingAdapter | None

    def __post_init__(self):
        """Selection never derives from caller labels or permissive scalar aliases."""
        _positive_id(self.actor_pk)
        _positive_id(self.namespace_id)
        _literal(self.projection, 128)
        if not callable(self.authorize_metadata):
            raise PlacementStateError(
                'An explicit placement metadata policy is required'
            )


def _authorized_source(request, adapter, machine_pk):
    """Compose actual request/OAuth, native role/scope and explicit source grants."""
    actor = integration_actor_from_request(request, required_role='work_order')
    if actor is None or actor.pk != adapter.actor_pk:
        return None
    machine = _authorized_machine(actor.pk, machine_pk)
    if machine is None:
        return None
    return actor, machine


def metadata_for_machine(request, machine_pk, *, adapter):
    """Return scoped placement metadata, or withhold it from a non-enrolled reader."""
    if adapter is None:
        return None
    if type(adapter) is not PlacementReadAdapter:
        raise PlacementStateError(
            'A server-selected placement read adapter is required'
        )
    adapter.__post_init__()
    _positive_id(machine_pk)
    _require_default_routing()
    authorized = _authorized_source(request, adapter, machine_pk)
    if authorized is None:
        return None
    actor, machine = authorized
    namespace = SourceNamespace.objects.filter(
        pk=adapter.namespace_id, active=True
    ).first()
    if namespace is None:
        return None
    enrollment = ProjectionEnrollment.objects.filter(
        namespace=namespace,
        namespace_epoch=namespace.epoch,
        projection=adapter.projection,
        client_id=machine.client_id,
        active=True,
        bootstrapped=True,
        bootstrap_status='ready',
    ).first()
    if enrollment is None:
        return None
    with _metadata_transaction():
        binding, expected, enrollment = _locked_context(enrollment.pk, machine_pk)
    placement = read_machine_placement(
        actor, environment=namespace.environment, machine_pk=machine_pk
    )
    if placement is None or placement.state == 'unavailable':
        return None
    if (
        placement.location != expected.location
        or placement.placement_version != expected.native_version
        or adapter.authorize_metadata(request, binding, expected) is not True
    ):
        return None
    state = 'pending'
    accepted = PlacementAcknowledgement.objects.filter(
        enrollment=enrollment,
        source_id=machine_pk,
        namespace_epoch=expected.namespace_epoch,
        source_revision=expected.fence,
        client_id=expected.client.pk,
        location_id=None if expected.location is None else expected.location.pk,
        native_version=expected.native_version,
        serving_generation=enrollment.served_generation,
    ).exists()
    if accepted and enrollment.generation_known:
        if adapter.serving is None or connection.in_atomic_block:
            state = 'unqualified'
        else:
            try:
                observation = adapter.serving.observe_placement(
                    _binding_snapshot(binding), replace(expected.machine)
                )
                snapshot = _observation_snapshot(observation)
                verification = _observation_snapshot(snapshot)
                verified = adapter.serving.verify_observation(verification)
                if (
                    verified is not True
                    or _observation_snapshot(verification) != snapshot
                    or _observation_snapshot(observation) != snapshot
                    or snapshot.binding != binding
                ):
                    state = 'unqualified'
                elif (
                    snapshot.placement == expected
                    and snapshot.generation == enrollment.served_generation
                ):
                    state = 'current'
            except (ValueError, OSError):
                state = 'unqualified'
    with _metadata_transaction():
        final_binding, final_expected, final_enrollment = _locked_context(
            enrollment.pk, machine_pk
        )
    current = _authorized_source(request, adapter, machine_pk)
    if current is None or (
        current[1].client_id,
        current[1].physical_location_id,
        current[1].placement_version,
    ) != (
        expected.client.pk,
        None if expected.location is None else expected.location.pk,
        expected.native_version,
    ):
        return None
    if (
        final_binding != binding
        or final_expected != expected
        or final_enrollment.observation_revision != enrollment.observation_revision
        or final_enrollment.generation_known != enrollment.generation_known
        or final_enrollment.served_generation != enrollment.served_generation
    ):
        return {'schema_version': SCHEMA, 'state': 'unqualified'}
    return {
        'schema_version': SCHEMA,
        'state': state,
        'machine': str(machine_pk),
        'client': str(expected.client.pk),
        'location': None if expected.location is None else str(expected.location.pk),
        'native_version': str(expected.native_version),
        'source_fence': str(expected.fence),
    }
