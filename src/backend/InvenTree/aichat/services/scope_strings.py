"""Shared, fail-closed maintenance scope derivation for touch and voice."""

import hashlib


def scope_strings(user) -> tuple[str, str]:
    """Re-read the authenticated actor's current business scope."""
    from tasks.scope import ScopeError, scope_for_actor

    from aichat.services.proposals import ProposalError

    if not user.is_active:
        raise ProposalError('actor is inactive')
    try:
        scopes = scope_for_actor(user)
    except ScopeError as exc:
        raise ProposalError('scope unresolved') from exc
    if not scopes:
        raise ProposalError('scope unresolved')
    key = '|'.join(sorted(repr(scope) for scope in scopes))
    return key, hashlib.sha256(key.encode('utf-8')).hexdigest()
