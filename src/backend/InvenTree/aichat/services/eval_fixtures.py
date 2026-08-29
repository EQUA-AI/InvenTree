"""Shared machinery for the evaluation-fixture seed commands (S6, WP-A5).

The S6 isolation move: every evaluation entity belongs to a dedicated
``eval-fixtures`` client that only the designated evaluation user is
granted (``assets.ClientScopeGrant`` + ``granted_client_scope_resolver``),
never the ordinary ``internal`` tenant — the default-client placement is
how the HX-200 fixture leaked into ordinary broad queries. ``eval-offlimits``
keeps its distinct adversarial role: a client NOBODY is granted.

Three seeder traps this module owns (recon findings):

- ``get_or_create`` defaults are CREATE-only — an existing machine row is
  never re-pointed by editing defaults, so re-pointing is an explicit
  repair branch that records a reversible manifest row and calls ``save()``
  (``QuerySet.update()`` would bypass the restamp receivers).
- The gasket Part carries no client of its own: ``derive_client_codes``
  falls back to ``['internal']`` for an uninstalled part, so the part must
  be LINKED to the eval machine (``MachinePart``) for its documents to
  follow the machine's client.
- The restamp receivers are async and flag-gated; the seeders call the
  restamp services synchronously after re-pointing, and the
  ``audit_eval_fixture_index`` command is the merge gate — not the
  seeder's own success output.

Names are never the control: the grant relation and the client filter are.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.management.base import CommandError

logger = logging.getLogger(__name__)

EVAL_FIXTURES_CODE = 'eval-fixtures'
EVAL_FIXTURES_NAME = 'RAG Evaluation Fixtures'
OFFLIMITS_CODE = 'eval-offlimits'
OFFLIMITS_NAME = 'RAG Eval Off-Limits Client'

#: Stable fixture keys (machine names are unique in this schema).
HX200_MACHINE_NAME = 'RAG Eval HX-200 Heat Exchanger'
HX200_VIDEO_MACHINE_NAME = 'RAG Eval HX-200 Video Heat Exchanger'
ZR9_MACHINE_NAME = 'RAG Eval ZR-9 Compressor'
GASKET_PART_NAME = 'RAG Eval HX-200 Gasket Set'


def refuse_production(*, break_glass: bool) -> None:
    """Refuse to seed a production-like deployment without the explicit flag.

    Production is inferred fail-closed: anything that is not DEBUG counts.
    ``--break-glass`` exists for the deliberate, runbook-driven data
    operation on ``aimms-experimental``.
    """
    from django.conf import settings

    if break_glass:
        return
    if not getattr(settings, 'DEBUG', False):
        raise CommandError(
            'Refusing to seed evaluation fixtures on a non-DEBUG deployment. '
            'Pass --break-glass for the deliberate runbook data operation.'
        )


def ensure_eval_clients(*, dry_run: bool):
    """Return (eval_fixtures, offlimits) clients; created unless dry-run."""
    from assets.models import Client

    if dry_run:
        return (
            Client.objects.filter(code=EVAL_FIXTURES_CODE).first(),
            Client.objects.filter(code=OFFLIMITS_CODE).first(),
        )
    eval_client, _ = Client.objects.get_or_create(
        code=EVAL_FIXTURES_CODE, defaults={'name': EVAL_FIXTURES_NAME, 'active': True}
    )
    offlimits, _ = Client.objects.get_or_create(
        code=OFFLIMITS_CODE, defaults={'name': OFFLIMITS_NAME, 'active': True}
    )
    return eval_client, offlimits


def repoint_machine(machine, client, manifest: list[dict[str, Any]]) -> bool:
    """Move one existing machine to ``client`` (explicit repair branch).

    Uses ``save()`` so the ``asset_machine_saved`` restamp receiver observes
    the change; records a reversible manifest row. Returns True on change.
    """
    if machine is None or client is None or machine.client_id == client.pk:
        return False
    manifest.append({
        'model': 'assets.AssetMachine',
        'pk': machine.pk,
        'name': machine.name,
        'field': 'client',
        'old': machine.client.code if machine.client_id else None,
        'new': client.code,
    })
    machine.client = client
    machine.save(update_fields=['client', 'updated_at'])
    return True


def ensure_gasket_link(part, machine, manifest: list[dict[str, Any]]) -> bool:
    """Install the gasket part on the eval machine so its docs follow it.

    Without this link ``derive_client_codes('part', ...)`` falls back to
    ``['internal']`` and the datasheet stays reachable from the default
    tenant — the exact leak S6 removes.
    """
    if part is None or machine is None:
        return False
    from assets.models import MachinePart

    _link, created = MachinePart.objects.get_or_create(
        machine=machine, part=part, defaults={'quantity': 1}
    )
    if created:
        manifest.append({
            'model': 'assets.MachinePart',
            'pk': _link.pk,
            'name': f'{machine.name} <- {part.name}',
            'field': 'created',
            'old': None,
            'new': 'installed',
        })
    return created


def restamp_fixture_scope(*, machine_pks=(), work_order_pks=()) -> list[str]:
    """Synchronously re-stamp ``client_codes`` after ownership moved.

    The receivers offload the same work asynchronously (and only when the
    RAG flags are on); the seed/data operation needs deterministic ordering,
    so it calls the services directly. Returns human-readable outcome lines;
    failures are collected, not raised — the audit command is the real gate.
    """
    from aichat.services import attachment_ingestion

    lines: list[str] = []
    for machine_pk in machine_pks:
        try:
            attachment_ingestion.restamp_machine_client_codes(machine_pk)
            lines.append(f'machine {machine_pk}: client_codes restamped')
        except Exception as exc:
            logger.warning('machine restamp failed pk=%s', machine_pk, exc_info=False)
            lines.append(f'machine {machine_pk}: RESTAMP FAILED ({type(exc).__name__})')
    for work_order_pk in work_order_pks:
        try:
            attachment_ingestion.restamp_work_order_media_client_codes(work_order_pk)
            lines.append(f'work order {work_order_pk}: media client_codes restamped')
        except Exception as exc:
            logger.warning('WO restamp failed pk=%s', work_order_pk, exc_info=False)
            lines.append(
                f'work order {work_order_pk}: RESTAMP FAILED ({type(exc).__name__})'
            )
    return lines


def render_manifest(manifest: list[dict[str, Any]]) -> str:
    """The reversible ownership-mapping manifest, one JSON document."""
    return json.dumps({'ownership_changes': manifest}, indent=2, sort_keys=True)


__all__ = [
    'EVAL_FIXTURES_CODE',
    'EVAL_FIXTURES_NAME',
    'GASKET_PART_NAME',
    'HX200_MACHINE_NAME',
    'HX200_VIDEO_MACHINE_NAME',
    'OFFLIMITS_CODE',
    'OFFLIMITS_NAME',
    'ZR9_MACHINE_NAME',
    'ensure_eval_clients',
    'ensure_gasket_link',
    'refuse_production',
    'render_manifest',
    'repoint_machine',
    'restamp_fixture_scope',
]
