"""Unwired current location-parent edges with bounded independent checks.

The caller must establish a trusted actor and a server-configured environment.
A result does not authorize history, descendant lists, counts or placement.
"""

from dataclasses import dataclass, field
from typing import Literal

from assets.integration_contracts import ClientSourceIdentity, LocationSourceIdentity
from assets.integration_sources import _authorized_client, _authorized_location
from assets.locations import MAX_DEPTH


@dataclass(frozen=True)
class LocationParentSourceRecord:
    """One current authorized location edge or a confirmed root."""

    location: LocationSourceIdentity
    state: Literal['root', 'available']
    parent: LocationSourceIdentity | None
    schema_version: str = field(default='aimms.location.parent.v1', init=False)

    def __post_init__(self):
        """Reject impossible parent envelopes and cross-source identities."""
        if not isinstance(self.location, LocationSourceIdentity):
            raise ValueError('Location source identity is required')
        if type(self.state) is not str:
            raise ValueError('Unrecognized location parent state')
        if self.state == 'root':
            if self.parent is not None:
                raise ValueError('A root cannot disclose a parent')
        elif self.state == 'available':
            if not isinstance(self.parent, LocationSourceIdentity):
                raise ValueError('An available edge requires a parent identity')
            if self.parent.environment != self.location.environment:
                raise ValueError('Linked identities must share a source environment')
            if self.parent.pk == self.location.pk:
                raise ValueError('A location cannot be its own parent')
        else:
            raise ValueError('Unrecognized location parent state')


@dataclass(frozen=True)
class LocationClientSourceRecord:
    """Current independently authorized endpoints, not client detail authority."""

    location: LocationSourceIdentity
    client: ClientSourceIdentity
    schema_version: str = field(default='aimms.location.client.v1', init=False)

    def __post_init__(self):
        """Reject malformed or cross-source endpoint coordinates."""
        if (
            type(self.location) is not LocationSourceIdentity
            or type(self.client) is not ClientSourceIdentity
            or self.location.environment != self.client.environment
        ):
            raise ValueError('Invalid location-client edge')


def read_location_client(actor, *, environment, location_pk):
    """Read a client edge only with current location and admin client authority.

    This deliberately differs from the Work-only native location context.
    Recheck the source association after each independent target observation;
    sequential checks do not establish atomicity or post-check revocation.
    """
    identity = LocationSourceIdentity(environment=environment, pk=location_pk)
    node = _authorized_location(actor, identity)
    if node is None:
        return None
    client = ClientSourceIdentity(environment=environment, pk=node.client_id)
    for _ in range(2):
        if _authorized_client(actor, client) is None:
            return None
        current = _authorized_location(actor, identity)
        if current is None or current.client_id != client.pk:
            return None
    return LocationClientSourceRecord(identity, client)


@dataclass(frozen=True)
class LocationHierarchyNode:
    """Authorized observed path values, not client or descendant authority."""

    identity: LocationSourceIdentity
    name: str = field(repr=False)
    timezone: str
    archived: bool
    version: int

    def __post_init__(self):
        """Validate native scalar types without normalizing stored values."""
        if (
            type(self.identity) is not LocationSourceIdentity
            or type(self.name) is not str
            or type(self.timezone) is not str
            or type(self.archived) is not bool
            or type(self.version) is not int
            or self.version < 0
        ):
            raise ValueError('Invalid location path values')
        self.identity.__post_init__()


@dataclass(frozen=True)
class LocationHierarchySourceRecord:
    """A bounded current path and visible child hint, never a complete tree."""

    location: LocationSourceIdentity
    path: tuple[LocationHierarchyNode, ...] = field(repr=False)
    effective_timezone: str | None
    has_children: bool
    population_complete: bool = field(default=False, init=False)
    revision_complete: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.location.hierarchy.v1', init=False)

    def __post_init__(self):
        """Refuse malformed, cyclic, cross-source or inconsistent path payloads."""
        if (
            type(self.location) is not LocationSourceIdentity
            or type(self.path) is not tuple
            or not 1 <= len(self.path) <= MAX_DEPTH
            or type(self.has_children) is not bool
            or (
                self.effective_timezone is not None
                and type(self.effective_timezone) is not str
            )
        ):
            raise ValueError('Invalid location hierarchy envelope')
        self.location.__post_init__()
        seen = set()
        for node in self.path:
            if type(node) is not LocationHierarchyNode:
                raise ValueError('A typed location path node is required')
            node.__post_init__()
            if (
                node.identity.environment != self.location.environment
                or node.identity.pk in seen
            ):
                raise ValueError('Invalid location path identity')
            seen.add(node.identity.pk)
        expected_timezone = next(
            (node.timezone for node in reversed(self.path) if node.timezone), None
        )
        if (
            self.path[-1].identity != self.location
            or self.effective_timezone != expected_timezone
        ):
            raise ValueError('Location path does not match its source or timezone')


def _authorized_child_hint(actor, identity, client_pk):
    """Bounded existence in current native/granted scope, not a child count."""
    from django.contrib.auth import get_user_model

    from rest_framework.exceptions import PermissionDenied

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.locations import locations_for

    actor_pk = getattr(actor, 'pk', None)
    if (
        not getattr(actor, 'is_authenticated', False)
        or type(actor_pk) is not int
        or actor_pk < 1
    ):
        return None
    user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if user is None or not fresh_role(user, 'work_order'):
        return None
    try:
        visible = locations_for(user).filter(
            client_id=client_pk, client_id__in=explicitly_granted_client_ids(user)
        )
        if not visible.filter(pk=identity.pk).exists():
            return None
        return visible.filter(parent_id=identity.pk).exists()
    except PermissionDenied:
        return None


def read_location_hierarchy(actor, *, environment, location_pk):
    """Read independently authorized names/timezones and an observed child hint.

    Compare all exported path values and associations, not just IDs, across two
    bounded walks. This is sequential evidence, not a snapshot/change feed or
    post-check revocation guarantee. A false hint is not deletion evidence.
    """
    identity = LocationSourceIdentity(environment=environment, pk=location_pk)
    first = _authorized_path(actor, identity, include_values=True)
    if first is None:
        return None
    has_children = _authorized_child_hint(actor, identity, first[0][1])
    if has_children is None:
        return None
    current_hint = _authorized_child_hint(actor, identity, first[0][1])
    if current_hint is None or current_hint != has_children:
        return None
    current = _authorized_path(actor, identity, include_values=True)
    if current is None or current != first:
        return None
    path = tuple(
        LocationHierarchyNode(
            identity=LocationSourceIdentity(environment=environment, pk=row[0]),
            name=row[3],
            timezone=row[4],
            archived=row[5],
            version=row[6],
        )
        for row in reversed(first)
    )
    return LocationHierarchySourceRecord(
        location=identity,
        path=path,
        effective_timezone=next(
            (node.timezone for node in reversed(path) if node.timezone), None
        ),
        has_children=has_children,
    )


def _authorized_path(actor, identity, *, include_values=False):
    """Resolve a same-client, bounded acyclic path using fresh authority per node."""
    seen = set()
    path = []
    current = identity.pk
    client_id = None
    while current is not None:
        if current in seen or len(path) >= MAX_DEPTH:
            return None
        seen.add(current)
        node = _authorized_location(
            actor, LocationSourceIdentity(environment=identity.environment, pk=current)
        )
        if node is None:
            return None
        if client_id is None:
            client_id = node.client_id
        elif node.client_id != client_id:
            return None
        row = (node.pk, node.client_id, node.parent_id)
        if include_values:
            row += (node.name, node.timezone, node.archived, node.version)
        path.append(row)
        current = node.parent_id
    return tuple(path)


def read_location_parent(actor, *, environment, location_pk):
    """Return a current direct edge only if the full path can be validated.

    Missing/denied nodes, cross-client links, cycles, over-depth paths and
    changes between the two walks return None; they are not represented as a
    known root. Every path node has native location and explicit-grant checks.
    Sequential rechecks are not an atomic snapshot or a revocation guarantee.
    """
    identity = LocationSourceIdentity(environment=environment, pk=location_pk)
    first = _authorized_path(actor, identity)
    if first is None:
        return None
    second = _authorized_path(actor, identity)
    if second is None or second != first:
        return None
    parent_pk = first[0][2]
    if parent_pk is None:
        return LocationParentSourceRecord(identity, 'root', None)
    parent = LocationSourceIdentity(environment=environment, pk=parent_pk)
    return LocationParentSourceRecord(identity, 'available', parent)
