"""Unwired, independently authorized maintenance-to-Work relationship.

The caller must establish the authenticated principal and source environment.
This reader does not change native endpoints or authorize related content.
"""

from dataclasses import dataclass, field
from typing import Literal

from tasks.integration_sources import WorkSourceIdentity, read_work_order

from assets.integration_contracts import MaintenanceSourceIdentity


@dataclass(frozen=True)
class MaintenanceWorkLinkSourceRecord:
    """An authorized maintenance edge, with absence distinct from withholding."""

    maintenance: MaintenanceSourceIdentity
    state: Literal['none', 'unknown', 'available']
    work: WorkSourceIdentity | None
    schema_version: str = field(default='aimms.maintenance.work_link.v1', init=False)

    def __post_init__(self):
        """Reject inconsistent states and identities from another source."""
        if type(self.maintenance) is not MaintenanceSourceIdentity:
            raise ValueError('Maintenance source identity is required')
        MaintenanceSourceIdentity.__post_init__(self.maintenance)
        if (
            type(self.schema_version) is not str
            or self.schema_version != 'aimms.maintenance.work_link.v1'
        ):
            raise ValueError('Unrecognized link schema')
        if type(self.state) is not str or self.state not in (
            'none',
            'unknown',
            'available',
        ):
            raise ValueError('Unrecognized link state')
        if self.state == 'available':
            if type(self.work) is not WorkSourceIdentity:
                raise ValueError('Available links require a Work identity')
            WorkSourceIdentity.__post_init__(self.work)
            if self.work.environment != self.maintenance.environment:
                raise ValueError('Linked identities must share a source environment')
        elif self.work is not None:
            raise ValueError('Absent or unknown links cannot disclose Work identity')


def _authorized_record(actor_pk, record_pk, environment=None):
    """Resolve a current authorized row, not a cached actor or record."""
    from django.contrib.auth import get_user_model

    from tasks.scope import ScopeError, maintenance_record_scope_filter

    from ai.core.integrations.retrieval_authority import fresh_role
    from assets import integration_attribution
    from assets.integration_authority import explicitly_granted_client_ids
    from assets.models import AssetMaintenanceRecord

    user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if user is None or not fresh_role(user, 'work_order'):
        return None
    try:
        scope = maintenance_record_scope_filter(user)
    except ScopeError:
        return None
    visible = AssetMaintenanceRecord.objects.select_related('machine').filter(
        scope, pk=record_pk, machine__client_id__in=explicitly_granted_client_ids(user)
    )
    return integration_attribution.attributed_maintenance_records(
        visible,
        **(
            {'actor': user, 'environment': environment, 'source_pk': record_pk}
            if integration_attribution.authority_enabled()
            else {}
        ),
    ).first()


def read_maintenance_work_link(actor, *, environment, record_pk):
    """Read one separately authorized edge, or None when its source is denied.

    ``unknown`` covers an existing linked Work that the current integration
    Work reader cannot establish as visible and explicitly granted. It does
    not expose the linked identity. Until maintenance attribution is proven,
    the source itself and therefore every link envelope is withheld. Sequential
    checks narrow, but cannot eliminate, concurrent changes after the last check.
    """
    identity = MaintenanceSourceIdentity(environment=environment, pk=record_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    initial = _authorized_record(actor_pk, record_pk, environment)
    if initial is None:
        return None
    observed_edge = (
        initial.machine_id,
        initial.machine.client_id,
        initial.work_order_id,
    )
    work = None
    if initial.work_order_id is not None:
        work = read_work_order(
            actor, environment=environment, work_pk=initial.work_order_id
        )

    current = _authorized_record(actor_pk, record_pk, environment)
    if (
        current is None
        or (current.machine_id, current.machine.client_id, current.work_order_id)
        != observed_edge
    ):
        return None

    def still_current():
        """Refresh both endpoints after final projection, then the source again."""
        source = _authorized_record(actor_pk, record_pk, environment)
        if (
            source is None
            or (source.machine_id, source.machine.client_id, source.work_order_id)
            != observed_edge
        ):
            return False
        if initial.work_order_id is not None:
            target = read_work_order(
                actor, environment=environment, work_pk=initial.work_order_id
            )
            if (work is None) != (target is None):
                return False
            if target is not None and (
                work is None or target.identity != work.identity
            ):
                return False
            source = _authorized_record(actor_pk, record_pk, environment)
            if (
                source is None
                or (source.machine_id, source.machine.client_id, source.work_order_id)
                != observed_edge
            ):
                return False
        return True

    def project(state, target):
        """Neither a successful constructor nor a validation fault grants access."""
        try:
            result = MaintenanceWorkLinkSourceRecord(identity, state, target)
        except (TypeError, ValueError):
            if not still_current():
                return None
            raise
        return result if still_current() else None

    if initial.work_order_id is None:
        return project('none', None)
    if work is None:
        return project('unknown', None)

    # Recheck the target after the source check and the source again afterward.
    if (
        read_work_order(actor, environment=environment, work_pk=initial.work_order_id)
        is None
    ):
        return None
    current = _authorized_record(actor_pk, record_pk, environment)
    if (
        current is None
        or (current.machine_id, current.machine.client_id, current.work_order_id)
        != observed_edge
    ):
        return None
    return project('available', work.identity)
