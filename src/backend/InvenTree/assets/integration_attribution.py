"""Fail-closed attribution filters for the optional integration readers.

Native visibility, current machine ownership and a client grant do not prove
historical attribution. No reviewed source can establish that attribution yet,
so existing records still return no rows. The owner-approved future-only native
attestation service is a separate, default-off source of exact revision proof.
It never backfills old rows, infers a client from today's owner, or substitutes
for independent current native visibility and explicit client grants.
"""


def authority_enabled():
    """Keep default native readers independent of optional state app imports."""
    from django.apps import apps
    from django.conf import settings

    return getattr(
        settings, 'AIMMS_INTEGRATION_AUTHORITY_ENABLED', False
    ) is True and apps.is_installed('integration_state')


def _attributed(queryset, kind, *, actor=None, environment=None, source_pk=None):
    if (
        not authority_enabled()
        or actor is None
        or environment is None
        or source_pk is None
    ):
        return queryset.none()
    from django.contrib.auth import get_user_model

    from integration_state.native_authority import (
        NativeAuthorityError,
        qualified_client_ids,
    )

    actor_pk = getattr(actor, 'pk', None)
    if type(actor_pk) is not int or not getattr(actor, 'is_authenticated', False):
        return queryset.none()
    current = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if current is None:
        return queryset.none()
    try:
        clients = qualified_client_ids(
            environment=environment, kind=kind, source_pk=source_pk, actor=current
        )
    except NativeAuthorityError:
        return queryset.none()
    # A direct-read proof is never converted to a population/page claim.
    return queryset.filter(pk=source_pk) if clients else queryset.none()


def attributed_work_orders(queryset, **context):
    """Admit only an exact future Work revision with native reviewed proof."""
    return _attributed(queryset, 'work', **context)


def attributed_maintenance_records(queryset, **context):
    """Withhold old or unproven maintenance; native scope remains independent."""
    return _attributed(queryset, 'maintenance', **context)


def attributed_machine_attachments(queryset, **context):
    """Require direct attachment revision/byte proof, not just its machine."""
    return _attributed(queryset, 'attachment', **context)


def attributed_attachments(queryset, **context):
    """Require direct Work attachment proof in addition to its owner proof."""
    return _attributed(queryset, 'attachment', **context)
