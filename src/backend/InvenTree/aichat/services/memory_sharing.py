"""Client-superset checks for transcripts that can contain learned memories."""

from django.contrib.auth import get_user_model

from tasks.scope import ScopeError, client_codes_for_actor

from ai.core.analysis.scope import MODE_EXPLICIT, scope_from_stored
from aichat.models import ChatMessage
from assets.models import AssetMachine


def required_shared_clients(thread):
    """Use the conservative owner boundary unless a scope was confirmed once."""
    owner = get_user_model().objects.filter(pk=thread.owner_id, is_active=True).first()
    if owner is None:
        raise ScopeError('Thread sharing unavailable')
    scope = scope_from_stored(thread.analysis_scope)
    if scope.mode == MODE_EXPLICIT and thread.analysis_scope_version == 1:
        machines = list(
            AssetMachine.objects.filter(pk__in=scope.machine_ids).values_list(
                'pk', 'client__code'
            )
        )
        if len(machines) != len(scope.machine_ids) or any(
            not code for _pk, code in machines
        ):
            raise ScopeError('Thread sharing unavailable')
        required = {code for _pk, code in machines}
    else:
        required = set(client_codes_for_actor(owner))
    # Server-authored provenance survives later scope changes. Malformed state
    # is refused rather than silently discarding a possible client boundary.
    informed = ChatMessage.objects.filter(
        thread=thread, role='assistant', metadata__has_key='memory_informed_clients'
    ).values_list('metadata__memory_informed_clients', flat=True)
    for codes in informed.iterator(chunk_size=200):
        if not isinstance(codes, list) or any(
            not isinstance(code, str) or not code for code in codes
        ):
            raise ScopeError('Thread sharing unavailable')
        required.update(codes)
    if not required:
        raise ScopeError('Thread sharing unavailable')
    return frozenset(required)


def can_read_shared_thread(actor, thread):
    """Reauthorize current grants; a thread grant never creates client access."""
    if actor is None or not actor.is_active:
        return False
    try:
        return client_codes_for_actor(actor).issuperset(required_shared_clients(thread))
    except ScopeError:
        return False
