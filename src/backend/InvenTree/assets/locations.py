"""Scoped physical-location commands and temporal placement invariants."""

import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone

from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from tasks.scope import ScopeError, scope_for_actor

from users.permissions import check_user_role

from .models import (
    AssetLocation,
    AssetMachine,
    Client,
    LocationParentHistory,
    MachineLocationTransfer,
    MachinePlacementHistory,
)

MAX_DEPTH = 32
MAX_TRANSFER = 100


class LocationConflict(APIException):
    """The caller must refresh and review before retrying."""

    status_code = 409
    default_detail = 'This record changed. Refresh and review your changes.'
    default_code = 'location_conflict'


def client_ids(actor):
    """Resolve positive client grants without treating physical sites as grants."""
    try:
        ids = {
            s.client_id
            for s in scope_for_actor(actor)
            if s.client_id is not None and s.site_key is None
        }
    except ScopeError as exc:
        raise PermissionDenied('Your maintenance scope is unavailable.') from exc
    return set(
        Client.objects.filter(pk__in=ids, active=True).values_list('pk', flat=True)
    )


def locations_for(actor):
    """Return only locations in the actor's current client scope."""
    return AssetLocation.objects.filter(client_id__in=client_ids(actor))


def graph_for(actor):
    """Load location identities once per request, never a client's machine list."""
    return {node.pk: node for node in locations_for(actor)}


def ancestors(graph, node_id):
    """Resolve a bounded, cycle-checked path within the authorized graph."""
    path = []
    seen = set()
    while node_id is not None:
        node = graph.get(node_id)
        if node is None or node_id in seen or len(path) >= MAX_DEPTH:
            raise ValidationError('The location path is unavailable or invalid.')
        seen.add(node_id)
        path.append(node)
        node_id = node.parent_id
    return list(reversed(path))


def descendants(graph, node_id):
    """Include the selected node and each descendant once."""
    children = {}
    for row in graph.values():
        children.setdefault(row.parent_id, []).append(row.pk)
    result = set()
    pending = [node_id]
    while pending:
        pk = pending.pop()
        if pk in result:
            raise ValidationError('The location hierarchy contains a cycle.')
        result.add(pk)
        pending.extend(children.get(pk, []))
    return result


def location_data(node, graph):
    """Serialize current path and inherited timezone with explicit identity."""
    path = ancestors(graph, node.pk)
    return {
        'pk': node.pk,
        'client': node.client_id,
        'parent': node.parent_id,
        'name': node.name,
        'code': node.code,
        'kind': node.kind,
        'description': node.description,
        'timezone': node.timezone,
        'effective_timezone': next(
            (p.timezone for p in reversed(path) if p.timezone), None
        ),
        'archived': node.archived,
        'version': node.version,
        'path': [{'pk': p.pk, 'name': p.name} for p in path],
        'has_children': any(p.parent_id == node.pk for p in graph.values()),
        'created_at': node.created_at,
        'updated_at': node.updated_at,
    }


def lock_client(actor, pk):
    """Serialize all hierarchy/placement writers, including on SQLite.

    A real UPDATE obtains the write lock on SQLite, where select_for_update
    alone is ineffective. All structural reads follow this shared client guard.
    """
    if pk not in client_ids(actor):
        raise Http404
    Client.objects.filter(pk=pk).update(location_revision=F('location_revision') + 1)


def _validate_node(node, graph):
    path = ancestors(graph, node.parent_id)
    if any(p.client_id != node.client_id or p.archived for p in path):
        raise ValidationError({
            'parent': 'Choose an active parent in the same workspace.'
        })
    if node.pk and any(p.pk == node.pk for p in path):
        raise ValidationError({
            'parent': 'A location cannot be moved under itself or its descendants.'
        })
    if not node.parent_id and not node.timezone:
        raise ValidationError({
            'timezone': 'A top-level location requires an IANA timezone.'
        })
    if node.timezone:
        try:
            ZoneInfo(node.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError({
                'timezone': 'Enter a valid IANA timezone, such as Europe/London.'
            }) from exc
    if node.pk:
        changed_graph = {**graph, node.pk: node}
        for pk in descendants(graph, node.pk):
            ancestors(changed_graph, pk)
    elif len(path) >= MAX_DEPTH:
        raise ValidationError({'parent': 'The maximum location depth is 32.'})
    node.name = ' '.join(node.name.split())
    if not node.name:
        raise ValidationError({'name': 'A name is required.'})
    node.name_key = unicodedata.normalize('NFKC', node.name).casefold()
    if len(node.name_key) > 255:
        raise ValidationError({'name': 'The normalized name is too long.'})
    node.code = node.code.lower()
    node.sibling_key = str(node.parent_id) if node.parent_id else 'root'
    siblings = AssetLocation.objects.filter(client_id=node.client_id).exclude(
        pk=node.pk
    )
    if siblings.filter(code=node.code).exists():
        raise ValidationError({'code': 'This code is already used in the workspace.'})
    if siblings.filter(sibling_key=node.sibling_key, name_key=node.name_key).exists():
        raise ValidationError({
            'name': 'A location with this name already exists under this parent.'
        })


@transaction.atomic
def save_location(actor, values, *, pk=None):
    """Create/edit a node and its effective parent/metadata history atomically."""
    if not check_user_role(actor, 'work_order', 'add' if pk is None else 'change'):
        raise PermissionDenied()
    values = dict(values)
    reason = values.pop('reason')
    expected = values.pop('expected_version', None)
    if pk is None:
        client_id = values.pop('client')
        lock_client(actor, client_id)
        node = AssetLocation(client_id=client_id)
    else:
        existing = locations_for(actor).filter(pk=pk).first()
        if existing is None:
            raise Http404
        lock_client(actor, existing.client_id)
        node = AssetLocation.objects.select_for_update().get(pk=pk)
        if expected != node.version:
            raise LocationConflict()
        if 'client' in values and values.pop('client') != node.client_id:
            raise ValidationError({'client': 'A location cannot change workspace.'})
        node.version += 1
    graph = {
        row.pk: row for row in AssetLocation.objects.filter(client_id=node.client_id)
    }
    for key, value in values.items():
        setattr(node, 'parent_id' if key == 'parent' else key, value)
    _validate_node(node, graph)
    if node.archived and node.pk:
        ids = descendants(graph, node.pk)
        if (
            any(not graph[i].archived for i in ids if i != node.pk)
            or AssetMachine.objects.filter(
                physical_location_id__in=ids, active=True
            ).exists()
        ):
            raise ValidationError({
                'archived': 'Move active machines and archive child locations first.'
            })
    try:
        with transaction.atomic():
            node.save()
    except IntegrityError as exc:
        raise LocationConflict(
            'A location with this name or code already exists.'
        ) from exc
    now = timezone.now()
    LocationParentHistory.objects.filter(location=node, valid_to__isnull=True).update(
        valid_to=now
    )
    LocationParentHistory.objects.create(
        location=node,
        parent_id=node.parent_id,
        valid_from=now,
        actor=actor,
        reason=reason,
        snapshot={
            key: getattr(node, key)
            for key in ('name', 'code', 'kind', 'timezone', 'archived', 'version')
        },
    )
    return node


@transaction.atomic
def transfer_machines(actor, values):
    """Move a bounded same-client batch at server time, with idempotent replay."""
    if not check_user_role(actor, 'work_order', 'change'):
        raise PermissionDenied()
    values = dict(values)
    machine_requests = sorted(values['machines'], key=lambda m: m['machine_id'])
    ids = [m['machine_id'] for m in machine_requests]
    if len(ids) != len(set(ids)):
        raise ValidationError({'machines': 'Select each machine only once.'})
    first = AssetMachine.objects.filter(
        pk=ids[0], client_id__in=client_ids(actor)
    ).first()
    if first is None:
        raise Http404
    lock_client(actor, first.client_id)
    machines = list(
        AssetMachine.objects
        .select_for_update()
        .filter(pk__in=ids, client_id=first.client_id)
        .order_by('pk')
    )
    if len(machines) != len(ids):
        raise ValidationError({
            'machines': 'Every machine must be available in the same workspace.'
        })
    graph = {
        row.pk: row for row in AssetLocation.objects.filter(client_id=first.client_id)
    }
    destination = values['destination_location_id']
    if destination is not None:
        if destination not in graph:
            raise ValidationError({
                'destination_location_id': 'The destination is unavailable.'
            })
        if any(node.archived for node in ancestors(graph, destination)):
            raise ValidationError({
                'destination_location_id': 'Archived locations cannot receive machines.'
            })
    payload = {
        'machines': machine_requests,
        'destination_location_id': destination,
        'reason': values['reason'],
    }
    previous = MachineLocationTransfer.objects.filter(
        client_id=first.client_id,
        actor=actor,
        idempotency_key=values['idempotency_key'],
    ).first()
    if previous:
        if previous.payload != payload:
            raise LocationConflict(
                'This request key was already used for a different transfer.'
            )
        return {**previous.result, 'replayed': True}
    for machine, request in zip(machines, machine_requests, strict=True):
        if machine.placement_version != request['expected_placement_version']:
            raise LocationConflict()
    now = timezone.now()
    transfer = MachineLocationTransfer.objects.create(
        client_id=first.client_id,
        actor=actor,
        idempotency_key=values['idempotency_key'],
        payload=payload,
    )
    for machine in machines:
        MachinePlacementHistory.objects.filter(
            machine=machine, valid_to__isnull=True
        ).update(valid_to=now)
        MachinePlacementHistory.objects.create(
            machine=machine, location_id=destination, transfer=transfer, valid_from=now
        )
        machine.physical_location_id = destination
        machine.placement_version += 1
        machine.save(
            update_fields=['physical_location', 'placement_version', 'updated_at']
        )
    transfer.result = {
        'transfer_id': transfer.pk,
        'effective_at': now.isoformat(),
        'machines': [
            {'machine_id': m.pk, 'placement_version': m.placement_version}
            for m in machines
        ],
        'replayed': False,
    }
    transfer.save(update_fields=['result'])
    return transfer.result
