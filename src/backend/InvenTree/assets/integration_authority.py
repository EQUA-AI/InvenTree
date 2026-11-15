"""Additional client-grant boundary for unwired integration sources.

Native scope remains necessary: a grant alone is not authorization. No
existing AIMMS resolver, API or write path is changed by this module.
"""


def explicitly_granted_client_ids(actor):
    """Return current active client IDs explicitly granted to the fresh actor."""
    from assets.models import ClientScopeGrant

    actor_pk = getattr(actor, 'pk', None)
    if (
        getattr(actor, 'is_authenticated', False) is not True
        or type(actor_pk) is not int
        or actor_pk < 1
    ):
        return ClientScopeGrant.objects.none().values('client_id')
    return ClientScopeGrant.objects.filter(
        user_id=actor_pk, user__is_active=True, client__active=True
    ).values('client_id')


def integration_actor_from_request(request, *, required_role):
    """Resolve a current read actor without granting any client or source access.

    The caller supplies an already authenticated DRF request, never a remote
    actor label. OAuth uses the current stored token and native granular read
    scope; this can be stricter than a legacy generic read-token surface.
    The source environment and additional request restrictions remain trusted
    caller responsibilities. Sequential checks are not atomic revocation.
    """
    from types import SimpleNamespace

    from django.contrib.auth import get_user_model

    from oauth2_provider.contrib.rest_framework.authentication import (
        OAuth2Authentication,
    )
    from oauth2_provider.contrib.rest_framework.permissions import (
        TokenMatchesOASRequirements,
    )
    from oauth2_provider.models import get_access_token_model
    from rest_framework.request import Request

    from ai.core.integrations.retrieval_authority import fresh_role
    from InvenTree.permissions import map_scope

    if type(required_role) is not str or required_role not in ('admin', 'work_order'):
        raise ValueError('A supported native core read role is required')
    if not isinstance(request, Request) or request.method != 'GET':
        return None
    principal = request.user
    actor_pk = getattr(principal, 'pk', None)
    if (
        not getattr(principal, 'is_authenticated', False)
        or type(actor_pk) is not int
        or actor_pk < 1
        or request.successful_authenticator is None
    ):
        return None
    actor = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if actor is None or not fresh_role(actor, required_role):
        return None
    if isinstance(request.successful_authenticator, OAuth2Authentication):
        token = request.auth
        token_pk = getattr(token, 'pk', None)
        if type(token_pk) is not int or token_pk < 1 or token.user_id != actor_pk:
            return None
        current_token = (
            get_access_token_model()
            .objects.filter(pk=token_pk, user_id=actor_pk)
            .first()
        )
        if current_token is None:
            return None
        view = SimpleNamespace(
            required_alternate_scopes=map_scope(roles=[required_role])
        )
        current_request = SimpleNamespace(auth=current_token, method='GET')
        if not TokenMatchesOASRequirements().has_permission(current_request, view):
            return None
    actor = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if actor is None or not fresh_role(actor, required_role):
        return None
    return actor


def integration_actor_from_current_ai(*, required_role):
    """Resolve a real, reauthenticated ASGI actor, not an AI identity label.

    This does not construct a DRF request or confer client/source access.
    Native role, explicit grant and source/mapping authority remain independent.
    The synchronous source consumer must enter from its native async thread hop.
    """
    from django.contrib.auth import get_user_model

    from asgiref.sync import async_to_sync

    from ai.core.auth import get_current_principal, revalidate_current_ai_principal
    from ai.core.integrations.retrieval_authority import fresh_role

    if type(required_role) is not str or required_role not in ('admin', 'work_order'):
        raise ValueError('A supported native core read role is required')
    principal = async_to_sync(revalidate_current_ai_principal)()
    if principal is None:
        return None
    value = principal.user_pk
    if (
        type(value) is not str
        or not 1 <= len(value) <= 19
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith('0')
    ):
        return None
    actor_pk = int(value)
    if actor_pk > (1 << 63) - 1:
        return None
    actor = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if actor is None or not fresh_role(actor, required_role):
        return None
    if async_to_sync(revalidate_current_ai_principal)() is not principal:
        return None
    actor = get_user_model().objects.filter(pk=actor_pk, is_active=True).first()
    if (
        actor is None
        or not fresh_role(actor, required_role)
        or get_current_principal() is not principal
    ):
        return None
    return actor
