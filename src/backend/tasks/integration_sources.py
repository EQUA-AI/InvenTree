"""Unwired, read-only Work source for an optional integration boundary.

Callers must supply a trusted authenticated actor and a server-configured
source environment. This module does not change native Work API behavior.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkSourceIdentity:
    """Identify a native Work record within its source environment."""

    environment: str
    pk: int

    def __post_init__(self):
        """Reject missing identity without coercing or normalizing inputs."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        if type(self.pk) is not int or self.pk < 1:
            raise ValueError('Work primary key must be a positive integer')


@dataclass(frozen=True)
class WorkSourceRecord:
    """Minimal Work fields without machine or other relationship disclosure."""

    identity: WorkSourceIdentity
    title: str
    status: str
    schema_version: str = field(default='aimms.work.basic.v1', init=False)


def _integration_scoped_work_orders(actor, *, environment=None, work_pk=None):
    """Withhold Work until a reviewed source can establish historical attribution.

    Current machine ownership and native visibility do not prove a Work
    record's historical client. Default-off/pre-epoch records remain withheld;
    explicit future enrollment admits only an exact two-user native attestation.
    """
    from assets import integration_attribution
    from assets.integration_authority import explicitly_granted_client_ids
    from tasks.scope import scoped_work_orders

    if integration_attribution.authority_enabled():
        if environment is None or work_pk is None:
            return scoped_work_orders(actor).none()
        return integration_attribution.attributed_work_orders(
            scoped_work_orders(actor).filter(pk=work_pk),
            actor=actor,
            environment=environment,
            source_pk=work_pk,
        )
    visible = scoped_work_orders(actor).filter(
        customer__isnull=True,
        machine__client_id__in=explicitly_granted_client_ids(actor),
    )
    return integration_attribution.attributed_work_orders(visible)


def read_work_order(actor, *, environment, work_pk):
    """Return minimal visible Work, or None if missing or inaccessible.

    Unproven history remains withheld, including natively visible Work on a
    granted machine. Optional future proof still requires current native scope,
    the applicable role and an explicit grant for every proven client. This
    record never authorizes relationships, evidence, or actions.
    """
    from django.contrib.auth import get_user_model

    from ai.core.integrations.retrieval_authority import fresh_role

    identity = WorkSourceIdentity(environment=environment, pk=work_pk)
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return None
    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or actor_pk < 1:
        return None

    work = None
    for _ in range(2):
        user = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
        if user is None or not fresh_role(user, 'work_order'):
            return None
        work = (
            _integration_scoped_work_orders(
                user, environment=environment, work_pk=identity.pk
            )
            .filter(pk=identity.pk)
            .first()
        )
        if work is None:
            return None

    assert work is not None
    return WorkSourceRecord(identity=identity, title=work.title, status=work.status)
