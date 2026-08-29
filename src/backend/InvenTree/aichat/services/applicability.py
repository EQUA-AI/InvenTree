"""The S8b applicability verification workflow (append-only in spirit).

Nothing automated ever reaches ``verified``: proposals (human, model
inference, or the serial backfill) create ``proposed`` rows; a human
holding ``aichat.verify_document_applicability`` verifies; model and
configuration kinds additionally need a DISTINCT engineering countersign
(``aichat.countersign_document_applicability``) before the state machine
activates the row. The database enforces the two-party rule and the
countersign implication; this module enforces them a second time with
readable errors, and owns everything the constraints cannot say (config
payloads, effective windows, byte-anchored liveness).

Byte anchoring: a claim copies its document's ``source_sha256`` at
proposal time. ``applicability_for`` only ever returns rows whose stored
hash still equals the document's current bytes — a re-ingest with
different content silently invalidates every old verification, which is
the A6 contract ("revision/content hash", never a name).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied
from django.utils import timezone

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger('inventree')


class ApplicabilityError(Exception):
    """Base for applicability workflow failures."""


class ApplicabilityNotFound(ApplicabilityError):  # noqa: N818
    """The referenced claim does not exist."""


class ApplicabilityStateConflict(ApplicabilityError):  # noqa: N818
    """The requested transition is not legal from the current state."""


def _require_actor(actor) -> None:
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied('applicability workflow requires an authenticated user')


def _require_perm(actor, perm: str) -> None:
    _require_actor(actor)
    if not actor.has_perm(perm):
        raise PermissionDenied(f'applicability workflow requires {perm}')


def _claim(claim_id: int):
    from aichat.models import ControlledDocumentApplicability

    row = (
        ControlledDocumentApplicability.objects
        .select_related('document')
        .filter(pk=claim_id)
        .first()
    )
    if row is None:
        raise ApplicabilityNotFound(f'applicability claim {claim_id} does not exist')
    return row


def propose(
    *,
    document,
    kind: str,
    actor,
    basis: str,
    target_machine_id: int = 0,
    target_serial: str = '',
    target_model: str = '',
    target_config: dict[str, Any] | None = None,
    effective_from=None,
    effective_to=None,
):
    """Create one ``proposed`` claim; never anything more.

    Validates the per-kind target shape with readable errors (the DB
    constraints back them up) including the one the DB cannot check: a
    ``firmware_config`` claim must actually carry a configuration payload.
    """
    from aichat.models import ApplicabilityKind, ControlledDocumentApplicability

    _require_actor(actor)
    kind_value = str(kind)
    if kind_value not in ApplicabilityKind.values:
        raise ApplicabilityError(f'unknown applicability kind {kind_value!r}')
    if not str(basis or '').strip():
        raise ApplicabilityError('a proposal requires its evidence basis')

    config = dict(target_config or {})
    if kind_value == ApplicabilityKind.EXACT_MACHINE and target_machine_id <= 0:
        raise ApplicabilityError('exact_machine requires a machine id target')
    if (
        kind_value
        in (ApplicabilityKind.INVERTER_MODEL, ApplicabilityKind.FIRMWARE_CONFIG)
        and not str(target_model or '').strip()
    ):
        raise ApplicabilityError(f'{kind_value} requires a model target')
    if kind_value == ApplicabilityKind.FIRMWARE_CONFIG and not config:
        raise ApplicabilityError(
            'firmware_config requires the configuration constraints payload'
        )
    if kind_value == ApplicabilityKind.FLEET_WIDE and (
        target_machine_id or str(target_serial or '') or str(target_model or '')
    ):
        raise ApplicabilityError('fleet_wide names no equipment target')
    if not str(document.source_sha256 or ''):
        raise ApplicabilityError(
            'the document carries no content hash; ingest it before claiming '
            'applicability'
        )

    row = ControlledDocumentApplicability.objects.create(
        document=document,
        document_content_sha256=document.source_sha256,
        kind=kind_value,
        target_machine_id=int(target_machine_id or 0),
        target_serial=str(target_serial or ''),
        target_model=str(target_model or ''),
        target_config=config,
        proposed_by=actor,
        proposal_basis=str(basis),
        effective_from=effective_from,
        effective_to=effective_to,
    )
    logger.info(
        'applicability.proposed claim=%s document=%s kind=%s',
        row.pk,
        document.pk,
        kind_value,
    )
    return row


def _maybe_activate(row) -> None:
    """Proposed → verified once every required human record exists."""
    from aichat.models import ApplicabilityKind, ApplicabilityState

    needs_countersign = row.kind in (
        ApplicabilityKind.INVERTER_MODEL,
        ApplicabilityKind.FIRMWARE_CONFIG,
    )
    if row.verified_by_id is None:
        return
    if needs_countersign and row.countersigned_by_id is None:
        return
    row.state = ApplicabilityState.VERIFIED
    row.save(update_fields=['state', 'updated_at'])
    logger.info('applicability.verified claim=%s kind=%s', row.pk, row.kind)


def verify(claim_id: int, *, actor):
    """Record the maintenance-management verification on one claim."""
    from aichat.models import ApplicabilityState

    _require_perm(actor, 'aichat.verify_document_applicability')
    row = _claim(claim_id)
    if row.state != ApplicabilityState.PROPOSED:
        raise ApplicabilityStateConflict(f'claim {row.pk} is {row.state}, not proposed')
    if row.proposed_by_id == actor.pk:
        raise PermissionDenied('the proposer can never verify their own claim')
    row.verified_by = actor
    row.verified_at = timezone.now()
    row.save(update_fields=['verified_by', 'verified_at', 'updated_at'])
    _maybe_activate(row)
    return row


def countersign(claim_id: int, *, actor):
    """Record the engineering countersign (model/configuration kinds)."""
    from aichat.models import ApplicabilityKind, ApplicabilityState

    _require_perm(actor, 'aichat.countersign_document_applicability')
    row = _claim(claim_id)
    if row.kind not in (
        ApplicabilityKind.INVERTER_MODEL,
        ApplicabilityKind.FIRMWARE_CONFIG,
    ):
        raise ApplicabilityStateConflict(f'{row.kind} claims take no countersign')
    if row.state != ApplicabilityState.PROPOSED:
        raise ApplicabilityStateConflict(f'claim {row.pk} is {row.state}, not proposed')
    if actor.pk in (row.proposed_by_id, row.verified_by_id):
        raise PermissionDenied('the countersign must come from a distinct engineer')
    row.countersigned_by = actor
    row.countersigned_at = timezone.now()
    row.save(update_fields=['countersigned_by', 'countersigned_at', 'updated_at'])
    _maybe_activate(row)
    return row


def revoke(claim_id: int, *, actor, reason: str):
    """Revoke one claim; the row stays as its own audit record."""
    from aichat.models import ApplicabilityState

    _require_perm(actor, 'aichat.verify_document_applicability')
    if not str(reason or '').strip():
        raise ApplicabilityError('a revocation requires its reason')
    row = _claim(claim_id)
    if row.state in (ApplicabilityState.REVOKED, ApplicabilityState.SUPERSEDED):
        raise ApplicabilityStateConflict(f'claim {row.pk} is already {row.state}')
    row.state = ApplicabilityState.REVOKED
    row.revoke_reason = str(reason)
    row.revoked_at = timezone.now()
    row.save(update_fields=['state', 'revoke_reason', 'revoked_at', 'updated_at'])
    logger.info('applicability.revoked claim=%s', row.pk)
    return row


def supersede(old_claim_id: int, new_claim_id: int, *, actor):
    """Mark one claim superseded by another (same document lineage)."""
    from aichat.models import ApplicabilityState

    _require_perm(actor, 'aichat.verify_document_applicability')
    old = _claim(old_claim_id)
    new = _claim(new_claim_id)
    if old.pk == new.pk:
        raise ApplicabilityError('a claim cannot supersede itself')
    if old.state == ApplicabilityState.SUPERSEDED:
        raise ApplicabilityStateConflict(f'claim {old.pk} is already superseded')
    old.state = ApplicabilityState.SUPERSEDED
    old.superseded_by = new
    old.save(update_fields=['state', 'superseded_by', 'updated_at'])
    return old


def _effective_window(rows, on_date=None):
    from django.db.models import Q

    day = on_date or timezone.now().date()
    return rows.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=day),
        Q(effective_to__isnull=True) | Q(effective_to__gte=day),
    )


def applicability_for(document, *, on_date=None) -> QuerySet:
    """LIVE verified claims for one document row, byte-anchored.

    Only rows whose copied content hash still equals the document's
    CURRENT bytes qualify — re-ingested content invalidates old
    verifications without touching them.
    """
    from aichat.models import ApplicabilityState, ControlledDocumentApplicability

    rows = ControlledDocumentApplicability.objects.filter(
        document=document,
        state=ApplicabilityState.VERIFIED,
        document_content_sha256=document.source_sha256 or '__missing__',
    )
    return _effective_window(rows, on_date)


def verified_claims_for_targets(
    *, machine_ids=(), serials=(), models=(), on_date=None
) -> QuerySet:
    """LIVE verified claims matching any of the given equipment targets.

    The C9 resolver: corpus hits and gateway rows ask "is any of MY
    machines/serials/models verifiably covered by this document?". Hash
    anchoring against the CURRENT document row is applied via a join.
    """
    from django.db.models import F, Q

    from aichat.models import ApplicabilityState, ControlledDocumentApplicability

    target = Q(pk__in=[])
    machine_id_list = [int(pk) for pk in machine_ids if int(pk) > 0]
    if machine_id_list:
        target |= Q(kind='exact_machine', target_machine_id__in=machine_id_list)
    serial_list = [str(serial) for serial in serials if str(serial or '').strip()]
    if serial_list:
        target |= Q(kind='exact_machine', target_serial__in=serial_list)
    model_list = [str(model) for model in models if str(model or '').strip()]
    if model_list:
        target |= Q(
            kind__in=['inverter_model', 'firmware_config'], target_model__in=model_list
        )
    target |= Q(kind='fleet_wide')

    rows = ControlledDocumentApplicability.objects.filter(
        target,
        state=ApplicabilityState.VERIFIED,
        document_content_sha256=F('document__source_sha256'),
    )
    return _effective_window(rows, on_date)


def safety_eligible(document, *, on_date=None) -> bool:
    """The stricter S8b predicate for safety/procedure pointers (§8.4).

    Current + indexed + human-approved + non-superseded + at least one
    live, byte-anchored verified applicability claim. ``is_current`` alone
    is NEVER safety approval.
    """
    from aichat.models import ControlledDocumentState

    if not document.is_current:
        return False
    if document.state != ControlledDocumentState.INDEXED:
        return False
    if document.approved_by_id is None:
        return False
    return applicability_for(document, on_date=on_date).exists()


__all__ = [
    'ApplicabilityError',
    'ApplicabilityNotFound',
    'ApplicabilityStateConflict',
    'applicability_for',
    'countersign',
    'propose',
    'revoke',
    'safety_eligible',
    'supersede',
    'verified_claims_for_targets',
    'verify',
]
