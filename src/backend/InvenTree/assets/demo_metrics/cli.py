"""Shared helpers for the demo metrics management commands.

Argument validation and help text live together with the behavior: unresolved
placeholders are rejected, the actor is resolved from the database (never from
a user-supplied ``approved_by`` string), and every failure surfaces as a stable
code via ``CommandError``.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError

from assets.demo_metrics_models import DemoMetricsSession

from . import cleanup, contract, history, planner, replay

#: Placeholder text that must never reach execution.
PLACEHOLDERS = ('<', '>')


class CliError(CommandError):
    """A command invocation is invalid; nothing was written."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        super().__init__(f'{code}: {message}')


def reject_placeholders(*values: str) -> None:
    """Refuse unresolved angle-bracket placeholders in execution arguments."""
    for value in values:
        if value and any(placeholder in value for placeholder in PLACEHOLDERS):
            raise CliError(
                'BAD_ARGUMENT', f'Unresolved placeholder in argument: {value}'
            )


def resolve_actor(username: str):
    """Resolve the trusted operator actor from the database."""
    if not username:
        raise CliError('BAD_ARGUMENT', '--actor is required')
    actor = get_user_model().objects.filter(username=username, is_active=True).first()
    if actor is None:
        raise CliError('ACTOR_UNRESOLVED', f'No active user {username!r}')
    return actor


def authorize_operator(actor) -> None:
    """Authorize the operator before any session, record or receipt is read.

    Shared command-entry boundary for every demo metrics command; the same
    rule is enforced inside the services (``cleanup`` raises ``CleanupError``
    for direct callers), so the command layer is never the only gate and a
    new entry point cannot quietly skip authorization.
    """
    try:
        cleanup._require_authorized_actor(actor)
    except cleanup.CleanupError as exc:
        raise CliError(exc.code, exc.message) from exc


def load_inputs(fixture_path: str, mapping_path: str, *, require_ready: bool):
    """Load and validate the fixture and resolved mapping."""
    reject_placeholders(fixture_path, mapping_path)
    try:
        fixture = contract.load_fixture(fixture_path)
    except contract.ContractError as exc:
        raise CliError(exc.code, str(exc)) from exc
    try:
        with open(mapping_path, encoding='utf-8') as stream:
            mapping_data = json.load(stream)
    except (OSError, ValueError) as exc:
        raise CliError('MAPPING_UNREADABLE', f'Cannot read mapping: {exc}') from exc
    try:
        mapping = contract.validate_mapping(
            mapping_data, fixture, require_ready=require_ready
        )
    except contract.ContractError as exc:
        raise CliError(exc.code, str(exc)) from exc
    return fixture, mapping


def resolve_session(session_key: str) -> DemoMetricsSession:
    """Load one session by its stable dataset/session key.

    Session identity is ``(dataset_key, session_key)``: the same slug may
    legitimately exist under two datasets, so a slug naming more than one
    session is ambiguous and fails closed (``SESSION_AMBIGUOUS``) — a silent
    first-row pick could stop or clean up the wrong session. Unknown slugs
    stay ``SESSION_UNKNOWN`` and unique slugs resolve unchanged.
    """
    reject_placeholders(session_key or '')
    if not session_key:
        raise CliError('BAD_ARGUMENT', '--session is required')
    matches = list(
        DemoMetricsSession.objects.filter(session_key=session_key).order_by('pk')[:2]
    )
    if not matches:
        raise CliError('SESSION_UNKNOWN', f'No demo session {session_key!r}')
    if len(matches) > 1:
        raise CliError(
            'SESSION_AMBIGUOUS',
            f'Session key {session_key!r} names sessions of multiple datasets',
        )
    return matches[0]


def dump(path: str, payload: dict) -> None:
    """Write a JSON artifact, refusing to silently overwrite."""
    reject_placeholders(path)
    with open(path, 'x', encoding='utf-8') as stream:
        json.dump(
            payload, stream, indent=2, sort_keys=True, default=str, allow_nan=False
        )
        stream.write('\n')


__all__ = [
    'CliError',
    'authorize_operator',
    'cleanup',
    'contract',
    'dump',
    'history',
    'load_inputs',
    'planner',
    'reject_placeholders',
    'replay',
    'resolve_actor',
    'resolve_session',
]
