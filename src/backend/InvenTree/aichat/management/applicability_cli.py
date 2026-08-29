"""Shared plumbing for the S8b applicability commands.

Lives beside (not inside) ``management/commands`` so Django does not load
it as a command. The ``--by`` convention follows the house pattern
(``pilot_stop``) with one deliberate hardening: a missing permission is a
``CommandError``, not a warning — verification is a control, not a
procedural guard.
"""

from __future__ import annotations

from django.core.management.base import CommandError


def resolve_actor(username: str | None):
    """Resolve ``--by`` to a real user; the workflow refuses anonymity."""
    if not username:
        raise CommandError('--by <username> is required: the workflow records WHO')
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise CommandError(f'unknown user {username!r}')
    return user


def resolve_document(*, scope_key: str, document_id: str, revision: str):
    """Address one exact document revision row."""
    from aichat.models import ControlledDocument

    row = ControlledDocument.objects.filter(
        scope_key=scope_key, document_id=document_id, revision=revision
    ).first()
    if row is None:
        raise CommandError(
            f'no controlled document {document_id!r} revision {revision!r} '
            f'in scope {scope_key!r}'
        )
    return row


def claim_row(claim) -> dict:
    """One claim as a JSON-safe report row (identities as usernames)."""
    return {
        'claim': claim.pk,
        'document_id': claim.document.document_id,
        'revision': claim.document.revision,
        'kind': claim.kind,
        'state': claim.state,
        'target_machine_id': claim.target_machine_id or None,
        'target_serial': claim.target_serial or None,
        'target_model': claim.target_model or None,
        'proposed_by': claim.proposed_by.username,
        'verified_by': claim.verified_by.username if claim.verified_by_id else None,
        'countersigned_by': (
            claim.countersigned_by.username if claim.countersigned_by_id else None
        ),
        'effective_from': (
            claim.effective_from.isoformat() if claim.effective_from else None
        ),
        'effective_to': claim.effective_to.isoformat() if claim.effective_to else None,
    }


__all__ = ['claim_row', 'resolve_actor', 'resolve_document']
