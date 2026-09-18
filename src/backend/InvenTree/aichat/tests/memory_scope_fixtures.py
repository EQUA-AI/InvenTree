"""Explicit client grants for transcript-sharing fixtures."""

from django.test import override_settings

from tasks.scope import MaintenanceScope

from assets.models import Client, ClientScopeGrant


def fixture_client_resolver(actor):
    """Read fixture grants without implying global or role-based access."""
    if actor is None or not actor.is_active:
        return set()
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=pk)
        for pk in ClientScopeGrant.objects.filter(
            user=actor, client__active=True
        ).values_list('client_id', flat=True)
    }


def grant_shared_fixture_client(case, *users):
    """Give the named fixture actors one common client and an explicit resolver."""
    client, _ = Client.objects.get_or_create(
        code='shared-fixture', defaults={'name': 'Shared fixture'}
    )
    for user in users:
        ClientScopeGrant.objects.get_or_create(user=user, client=client)
    case.enterContext(
        override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver)
    )
    return client
