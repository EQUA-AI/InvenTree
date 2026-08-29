"""Registry-backed source inventory and revision resolution (S8a, §8.4).

Source-inventory questions ("what manuals do you have for the HX-200?",
"which revision is current?", "did the datasheet finish indexing?") are
answered from the REGISTRIES — ``ControlledDocument`` and
``AttachmentIngest`` joined to ``common.Attachment`` — never from semantic
similarity. Zero network calls happen on this path, which is what makes
``population_type="registry"`` / ``complete_population=True`` honest.

A11 source states are computed per row, honestly:

- ``applicable`` is **always False** with an ``applicability_unresolved``
  warning — verified applicability is S8b's normalized relation, which does
  not exist yet. An association shown here is ingest metadata (the stamped
  serial), never a verified claim that a document applies.
- ``attached`` for controlled documents means "carries an ingest-time asset
  stamp" (the denormalized ``asset_id`` text), stated as such.

Asset scope only ever narrows: requested machine ids are intersected with
the bound analysis scope (never widened), then re-authorized per id — a
model-supplied id is a candidate, never a grant. Site-wide (blank-stamp)
controlled documents are included and labeled, matching §8.4 step 4.

This module is sync, MAF-free, and lazily imports Django models — the
``ai_read``/``scope_context`` convention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ai.core.contracts.retrieval import build_envelope, coverage, record_envelope

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

#: The gateway's source-class vocabulary (filterable; §8.4).
SOURCE_CLASSES = (
    "controlled_document",
    "asset_attachment",
    "evidence_media",
    "thread_upload",
)

APPLICABILITY_UNRESOLVED = "applicability_unresolved"
#: Display cap per section. Truncates DISPLAY only, never the counts.
MAX_INVENTORY_ROWS = 50
#: Sanity bound on registry rows fetched for grouping.
_MAX_FETCH_ROWS = 2000


@dataclass(frozen=True, slots=True)
class AssetSet:
    """The frozen asset scope every gateway query and fallback step shares."""

    machines: tuple[tuple[int, str, str], ...]  # (pk, name, serial)
    serials: frozenset[str]
    serial_less: tuple[str, ...]  # names of machines without a stable serial
    warnings: tuple[str, ...]

    @property
    def machine_pks(self) -> tuple[int, ...]:
        return tuple(pk for pk, _, _ in self.machines)


def resolve_asset_set(user: Any, machine_ids: Sequence[int] | None = None) -> AssetSet:
    """Freeze the asset scope once: intersect, re-authorize, resolve serials.

    Under an explicit enforced analysis scope, requested ids are intersected
    with the scope (out-of-scope requests are counted, not disclosed) and a
    missing request means "the scope's machines". Every surviving id is then
    re-authorized via ``assets.ai_read.authorized_machine``; unauthorized or
    unknown ids drop silently (no existence disclosure).
    """
    from ai.core.analysis.scope_context import current_turn_scope

    warnings: list[str] = []
    scope = current_turn_scope()
    requested = None if machine_ids is None else [int(pk) for pk in machine_ids]

    if scope is not None and scope.explicit and scope.enforce:
        allowed = set(scope.machine_ids or ())
        if requested is None:
            candidates: list[int] = sorted(allowed)
        else:
            candidates = [pk for pk in requested if pk in allowed]
            excluded = len(requested) - len(candidates)
            if excluded:
                warnings.append(f"narrowed_to_analysis_scope:{excluded}_machines")
    else:
        candidates = requested or []

    machines: list[tuple[int, str, str]] = []
    serial_less: list[str] = []
    try:
        from assets.ai_read import authorized_machine
    except ImportError:  # pragma: no cover - assets app always present in prod
        authorized_machine = None
    if authorized_machine is not None:
        for pk in candidates:
            machine = authorized_machine(user, pk)
            if machine is None:
                continue
            serial = str(getattr(machine, "serial", "") or "").strip()
            machines.append((machine.pk, machine.name, serial))
            if not serial:
                serial_less.append(machine.name)
    if serial_less:
        warnings.append("serial_unresolved")

    return AssetSet(
        machines=tuple(machines),
        serials=frozenset(serial for _, _, serial in machines if serial),
        serial_less=tuple(serial_less),
        warnings=tuple(warnings),
    )


# --- A11 state helpers (shared with the corpora) --------------------------


def source_state_for_controlled_row(document: Any) -> dict[str, bool]:
    """A11 states for one ``ControlledDocument`` row, computed not asserted."""
    state = str(getattr(document, "state", "") or "")
    return {
        "registered": True,
        # "Attached" = carries an ingest-time asset stamp; the stamp is
        # provenance text, not a verified relation (S8b).
        "attached": bool(getattr(document, "asset_id", "")),
        "indexed": state == "indexed",
        "applicable": False,
        "searchable_now": bool(
            getattr(document, "is_current", False)
            and state == "indexed"
            and getattr(document, "search_index_name", "")
        ),
        "current": bool(getattr(document, "is_current", False)),
    }


def source_state_for_ingest_row(
    ingest: Any | None, *, actor_codes: frozenset[str] = frozenset()
) -> dict[str, bool]:
    """A11 states for one attachment/media registry row (or its absence)."""
    if ingest is None:
        return {
            "registered": False,
            "attached": True,
            "indexed": False,
            "applicable": False,
            "searchable_now": False,
            "current": True,
        }
    state = str(getattr(ingest, "state", "") or "")
    row_codes = frozenset(getattr(ingest, "client_codes", None) or ())
    return {
        "registered": True,
        "attached": True,
        "indexed": state == "indexed",
        "applicable": False,
        "searchable_now": bool(
            state == "indexed"
            and getattr(ingest, "search_index_name", "")
            and row_codes & actor_codes
        ),
        "current": state not in ("superseded", "deleted"),
    }


# --- controlled-document inventory ----------------------------------------


def controlled_document_inventory(
    *,
    scope_key: str,
    asset_set: AssetSet | None = None,
    include_superseded: bool = False,
    limit: int = MAX_INVENTORY_ROWS,
) -> dict[str, Any]:
    """Group the governed registry by document; per-document lifecycle rows.

    A blank scope key REFUSES (the ``corpus_filter`` rule): an unconfigured
    deployment boundary must never widen into "all documents".
    """
    if not scope_key:
        return {
            "unavailable": True,
            "code": "scope_unconfigured",
            "documents": [],
            "population_count": 0,
        }
    from aichat.models import ControlledDocument
    from django.db.models import Q

    rows_query = ControlledDocument.objects.filter(scope_key=scope_key)
    if asset_set is not None and asset_set.serials:
        # Site-wide (blank-stamp) documents are §8.4 step 4: included, labeled.
        rows_query = rows_query.filter(Q(asset_id__in=sorted(asset_set.serials)) | Q(asset_id=""))

    population_count = rows_query.values("document_id").distinct().count()
    document_ids = list(
        rows_query
        .order_by("document_id")
        .values_list("document_id", flat=True)
        .distinct()[: max(1, int(limit))]
    )
    rows = list(
        rows_query.filter(document_id__in=document_ids).order_by("document_id", "-created_at")[
            :_MAX_FETCH_ROWS
        ]
    )

    serial_names = {serial: name for _, name, serial in asset_set.machines} if asset_set else {}
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.document_id, []).append(row)

    documents: list[dict[str, Any]] = []
    for document_id in document_ids:
        revisions = grouped.get(document_id, [])
        if not revisions:
            continue
        current = next((row for row in revisions if row.is_current), None)
        newest = current or revisions[0]
        superseded = [
            row
            for row in revisions
            if not row.is_current and str(row.state) in ("superseded", "archived", "indexed")
        ]
        pending_or_failed = [
            row for row in revisions if str(row.state) in ("draft", "indexing", "failed")
        ]
        asset_stamp = str(newest.asset_id or "")
        entry: dict[str, Any] = {
            "document_id": document_id,
            "title": newest.title,
            "document_class": newest.document_class,
            "access_class": newest.access_class,
            "current": (
                {
                    "revision": current.revision,
                    "revision_date": current.revision_date.isoformat()
                    if current.revision_date
                    else None,
                    "state": str(current.state),
                    "indexed_at": current.indexed_at.isoformat() if current.indexed_at else None,
                    # Presence booleans ONLY — identities never appear (Q15).
                    "approved": bool(current.approved_by_id),
                    "source_sha256_prefix": (current.source_sha256 or "")[:12],
                }
                if current is not None
                else None
            ),
            "superseded_revision_count": len(superseded),
            "pending_or_failed": [
                {
                    "revision": row.revision,
                    "state": str(row.state),
                    "error_code": row.indexing_error_code or None,
                }
                for row in pending_or_failed[:3]
            ],
            "association": "ingest_asset_serial" if asset_stamp else "site_wide",
            "associated_assets": (
                [serial_names[asset_stamp]] if asset_stamp in serial_names else []
            ),
            "source_state": source_state_for_controlled_row(newest),
            "applicability": "unresolved",
        }
        if include_superseded:
            entry["superseded_revisions"] = [
                {
                    "revision": row.revision,
                    "revision_date": row.revision_date.isoformat() if row.revision_date else None,
                    "state": str(row.state),
                }
                for row in superseded[:3]
            ]
        documents.append(entry)

    return {
        "documents": documents,
        "population_count": population_count,
        "returned_count": len(documents),
        "display_truncated": population_count > len(documents),
    }


# --- attachment / media inventory ------------------------------------------


def _latest_ingest_by_attachment(
    machine_pks: Sequence[int], work_order_pks: Sequence[int]
) -> dict[int, Any]:
    """Latest registry row per attachment id (winner keys on ``claimed_at``)."""
    from aichat.models import AttachmentIngest
    from django.db.models import Q

    query = Q()
    if machine_pks:
        query |= Q(model_type="assetmachine", model_id__in=list(machine_pks))
    if work_order_pks:
        query |= Q(model_type="workorder", model_id__in=list(work_order_pks))
    if not query:
        return {}
    latest: dict[int, Any] = {}
    for ingest in AttachmentIngest.objects.filter(query).order_by("created_at")[:_MAX_FETCH_ROWS]:
        existing = latest.get(ingest.attachment_id)
        if existing is None:
            latest[ingest.attachment_id] = ingest
            continue
        key = ingest.claimed_at or ingest.created_at
        existing_key = existing.claimed_at or existing.created_at
        if key and (existing_key is None or key >= existing_key):
            latest[ingest.attachment_id] = ingest
    return latest


def attachment_inventory(
    *,
    user: Any,
    asset_set: AssetSet,
    pipelines: Sequence[str] = ("doc", "image", "video"),
    limit: int = MAX_INVENTORY_ROWS,
) -> dict[str, Any]:
    """Attachments/media for the asset set, joined to their ingest state.

    An ``Attachment`` with no registry row reports ``registered: False`` —
    the honesty case ("uploaded but never indexed") the plain attachments
    tool cannot express. Rows stamped for a foreign client are withheld
    entirely (fail-closed listing); empty-stamp rows (clientless machines)
    are listed but never searchable.
    """
    from ai.core.tools.diagnostics import fence_untrusted_content

    if not asset_set.machines:
        return {
            "attachments": [],
            "population_count": 0,
            "returned_count": 0,
            "warnings": ["asset_selection_required"],
        }

    from common.models import Attachment

    machine_pks = list(asset_set.machine_pks)
    work_order_pks: list[int] = []
    try:
        from tasks.models import WorkOrder

        work_order_pks = list(
            WorkOrder.objects.filter(machine_id__in=machine_pks).values_list("pk", flat=True)[
                :_MAX_FETCH_ROWS
            ]
        )
    except ImportError:  # pragma: no cover - tasks app always present in prod
        pass

    from django.db.models import Q

    owner_query = Q(model_type="assetmachine", model_id__in=machine_pks)
    if work_order_pks:
        owner_query |= Q(model_type="workorder", model_id__in=work_order_pks)
    attachment_rows = list(
        Attachment.objects.filter(owner_query).order_by("-upload_date", "-pk")[:_MAX_FETCH_ROWS]
    )
    population_count = len(attachment_rows)
    ingest_by_attachment = _latest_ingest_by_attachment(machine_pks, work_order_pks)

    actor_codes: frozenset[str] = frozenset()
    try:
        from tasks.scope import client_codes_for_actor

        actor_codes = frozenset(client_codes_for_actor(user))
    except Exception:
        # Fail-closed: with no resolvable codes nothing reports searchable.
        actor_codes = frozenset()

    wanted_pipelines = frozenset(pipelines)
    items: list[dict[str, Any]] = []
    withheld_count = 0
    for attachment in attachment_rows:
        if len(items) >= max(1, int(limit)):
            break
        ingest = ingest_by_attachment.get(attachment.pk)
        if ingest is not None:
            if str(ingest.pipeline) not in wanted_pipelines:
                continue
            row_codes = frozenset(ingest.client_codes or ())
            if row_codes and not (row_codes & actor_codes):
                withheld_count += 1
                continue
        basename = attachment.basename
        entry: dict[str, Any] = {
            "kind": "file" if basename else "link",
            "name": fence_untrusted_content(basename or "") or None,
            "comment": fence_untrusted_content(attachment.comment or ""),
            "uploaded_at": attachment.upload_date.isoformat() if attachment.upload_date else None,
            "control_class": "uncontrolled_attachment",
            "source_class": (
                "evidence_media"
                if ingest is not None and str(ingest.pipeline) in ("image", "video")
                else "asset_attachment"
            ),
            "ingest_state": str(ingest.state) if ingest is not None else "unregistered",
            "error_code": (
                (ingest.error_code or None)
                if ingest is not None and str(ingest.state) in ("failed", "skipped")
                else None
            ),
            "source_state": source_state_for_ingest_row(ingest, actor_codes=actor_codes),
            "applicability": "unresolved",
        }
        if ingest is not None and not (ingest.client_codes or ()):
            entry.setdefault("warnings", []).append("not_searchable_for_actor")
        items.append(entry)

    result: dict[str, Any] = {
        "attachments": items,
        "population_count": population_count,
        "returned_count": len(items),
        "display_truncated": population_count > len(items) + withheld_count,
    }
    if withheld_count:
        result["withheld_count"] = withheld_count
    return result


# --- top-level inventory ----------------------------------------------------


def inventory(
    user: Any,
    *,
    machine_ids: Sequence[int] | None = None,
    source_classes: Sequence[str] | None = None,
    include_superseded: bool = False,
    thread_files: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The registry answer to "what document sources exist, and their state".

    One §7.4 envelope per section (an envelope has ONE source class); the
    internal halves (scope hash, client codes, index names) ride the capture
    ledger only.
    """
    from ai.core.config import get_settings

    wanted = tuple(source_classes) if source_classes else SOURCE_CLASSES
    unknown = [cls for cls in wanted if cls not in SOURCE_CLASSES]
    if unknown:
        return {
            "unavailable": True,
            "code": "unknown_source_class",
            "allowed": list(SOURCE_CLASSES),
        }

    asset_set = resolve_asset_set(user, machine_ids)
    settings = get_settings()
    scope_key = str(getattr(settings, "single_site_policy_key", "") or "")
    sections: dict[str, Any] = {}
    warnings: list[str] = [APPLICABILITY_UNRESOLVED, *asset_set.warnings]

    if "controlled_document" in wanted:
        section = controlled_document_inventory(
            scope_key=scope_key,
            asset_set=asset_set,
            include_superseded=include_superseded,
        )
        if not section.get("unavailable"):
            envelope = build_envelope(
                source_class="controlled_document",
                population_type="registry",
                operation="source_inventory",
                filters={"asset_serials": sorted(asset_set.serials)},
                coverage=coverage(
                    population_count=section["population_count"],
                    returned_count=section["returned_count"],
                    complete_population=True,
                    display_truncated=section["display_truncated"],
                ),
                warnings=(APPLICABILITY_UNRESOLVED,),
            )
            section["retrieval"] = envelope
            record_envelope(
                "list_document_sources",
                envelope,
                index_name=str(getattr(settings, "azure_search_controlled_documents_index", "")),
            )
        sections["controlled_documents"] = section

    if "asset_attachment" in wanted or "evidence_media" in wanted:
        pipelines: list[str] = []
        if "asset_attachment" in wanted:
            pipelines.append("doc")
        if "evidence_media" in wanted:
            pipelines.extend(("image", "video"))
        section = attachment_inventory(user=user, asset_set=asset_set, pipelines=pipelines)
        envelope = build_envelope(
            source_class="asset_attachment",
            population_type="registry",
            operation="source_inventory",
            filters={"machine_ids": sorted(asset_set.machine_pks)},
            coverage=coverage(
                population_count=section["population_count"],
                returned_count=section["returned_count"],
                complete_population=True,
                display_truncated=bool(section.get("display_truncated")),
            ),
            warnings=(APPLICABILITY_UNRESOLVED,),
        )
        section["retrieval"] = envelope
        record_envelope(
            "list_document_sources",
            envelope,
            withheld_count=int(section.get("withheld_count") or 0),
        )
        sections["attachments"] = section

    if "thread_upload" in wanted:
        if thread_files:
            sections["thread_uploads"] = {
                "files": [
                    {
                        "name": str(entry.get("name") or entry.get("filename") or ""),
                        "control_class": "thread_upload",
                        "source_state": {
                            "registered": False,
                            "attached": False,
                            "indexed": False,
                            "applicable": False,
                            "searchable_now": False,
                            "current": True,
                        },
                    }
                    for entry in thread_files
                ],
                "note": "thread uploads are never controlled evidence",
            }
        else:
            sections["thread_uploads"] = {
                "available": "in_conversation_only",
                "note": "thread uploads are never controlled evidence",
            }

    return {
        "sections": sections,
        "asset_scope": {
            "machines": [name for _, name, _ in asset_set.machines],
            "serial_unresolved": list(asset_set.serial_less),
        },
        "warnings": warnings,
    }


# --- revision resolution + the §8.4 fallback orchestrator ------------------


@dataclass(frozen=True, slots=True)
class AmbiguousDocumentRef:
    """More than one current document matched; ask, never guess."""

    candidates: tuple[tuple[str, str, str], ...]  # (document_id, title, revision)


def resolve_selected_document(*, scope_key: str, document_ref: str):
    """Resolve a user/model-supplied document NAME to one current registry row.

    The sanctioned use of a model-supplied name: a lookup key into an
    already-scope-bound registry (narrowing), never the scope mechanism.
    Match order: exact ``document_id`` → case-insensitive exact title →
    unique title substring. Ambiguity returns the candidates.
    """
    if not scope_key or not str(document_ref or "").strip():
        return None
    from aichat.models import ControlledDocument

    current_rows = ControlledDocument.objects.filter(scope_key=scope_key, is_current=True)
    reference = str(document_ref).strip()

    exact_id = current_rows.filter(document_id=reference).first()
    if exact_id is not None:
        return exact_id
    exact_title = list(current_rows.filter(title__iexact=reference)[:5])
    if len(exact_title) == 1:
        return exact_title[0]
    if len(exact_title) > 1:
        return AmbiguousDocumentRef(
            candidates=tuple((row.document_id, row.title, row.revision) for row in exact_title)
        )
    partial = list(current_rows.filter(title__icontains=reference)[:5])
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        return AmbiguousDocumentRef(
            candidates=tuple((row.document_id, row.title, row.revision) for row in partial)
        )
    return None


def retrieve_manual_fact(
    user: Any,
    *,
    query: str,
    machine_ids: Sequence[int] | None = None,
    document_ref: str | None = None,
    top_k: int = 5,
    corpus_search=None,
    pinned_search=None,
    attachment_search=None,
) -> dict[str, Any]:
    """The §8.4 fallback orchestrator: pin → exact-asset → fleet-wide → attachments.

    The asset set is FROZEN once and passed to every step — fallback may
    change source class, never asset scope. Every attempt and outcome is
    recorded in ``attempts`` (also mirrored into the capture ledger by the
    tools each step calls). Zero hits everywhere is reported as "no relevant
    passage retrieved" — a retrieval outcome, never a claim about the source.

    The three ``*_search`` callables exist for tests; production defaults are
    the real corpus functions.
    """
    from ai.core.contracts.retrieval import NO_RELEVANT_PASSAGE

    attempts: list[dict[str, Any]] = []
    asset_set = resolve_asset_set(user, machine_ids)

    # A scoped machine without a stable serial cannot be mapped to documents:
    # applicability is unresolved and NO controlled search runs (zero network
    # calls) — narrowing, never widening.
    if asset_set.machines and not asset_set.serials:
        attempts.append({
            "step": "exact_asset_controlled",
            "outcome": "applicability_unresolved",
            "hit_count": 0,
        })
        return {
            "chunks": [],
            "applicability": "unresolved",
            "scope_miss": True,
            "attempts": attempts,
            "warnings": ["serial_unresolved", APPLICABILITY_UNRESOLVED],
        }

    if corpus_search is None:
        from ai.core.integrations.controlled_document_corpus import search_corpus

        corpus_search = search_corpus
    if pinned_search is None:
        from ai.core.integrations.controlled_document_search import (
            search_selected_document,
        )

        pinned_search = search_selected_document

    def _finish(result: dict[str, Any], *, source_class: str, labels: list[str]) -> dict[str, Any]:
        result = dict(result)
        result["source_class"] = source_class
        result["labels"] = labels
        result["attempts"] = attempts
        result.setdefault("warnings", [])
        return result

    settings_scope_key = ""
    try:
        from ai.core.config import get_settings

        settings_scope_key = str(getattr(get_settings(), "single_site_policy_key", "") or "")
    except Exception:  # pragma: no cover - config always importable in prod
        pass

    # Step 1 — explicitly selected current revision (the four-way pin).
    if document_ref:
        resolved = resolve_selected_document(
            scope_key=settings_scope_key, document_ref=document_ref
        )
        if isinstance(resolved, AmbiguousDocumentRef):
            attempts.append({
                "step": "pinned_revision",
                "outcome": "ambiguous",
                "hit_count": 0,
            })
            return {
                "chunks": [],
                "machine_filter": "ambiguous",
                "document_candidates": [
                    {"document_id": doc_id, "title": title, "revision": revision}
                    for doc_id, title, revision in resolved.candidates
                ],
                "attempts": attempts,
                "warnings": [],
            }
        if resolved is None:
            attempts.append({
                "step": "pinned_revision",
                "outcome": "pin_unresolved",
                "hit_count": 0,
            })
        else:
            try:
                pinned = pinned_search(document=resolved, query=query, top_k=top_k)
                chunk_count = len(pinned.get("chunks") or ())
                attempts.append({
                    "step": "pinned_revision",
                    "outcome": "hit" if chunk_count else "no_relevant_passage",
                    "hit_count": chunk_count,
                })
                if chunk_count:
                    return _finish(
                        dict(pinned),
                        source_class="controlled_document",
                        labels=["pinned_revision"],
                    )
            except Exception:
                attempts.append({
                    "step": "pinned_revision",
                    "outcome": "unavailable",
                    "hit_count": 0,
                })

    # Step 2/3 — exact-asset controlled search (model/config applicability is
    # honestly collapsed into this step until S8b exists).
    serials = tuple(sorted(asset_set.serials)) or None
    try:
        scoped = corpus_search(user=user, query=query, top_k=top_k, asset_ids=serials)
        hit_count = len(scoped.get("chunks") or ())
        attempts.append({
            "step": "exact_asset_controlled",
            "outcome": "hit" if hit_count else "no_relevant_passage",
            "hit_count": hit_count,
            "model_config_applicability": "unavailable_pre_s8b",
        })
        if hit_count:
            return _finish(scoped, source_class="controlled_document", labels=[])
    except Exception:
        attempts.append({
            "step": "exact_asset_controlled",
            "outcome": "search_failed",
            "hit_count": 0,
        })

    # Step 4 — fleet-wide controlled documents, clearly labeled. Only when a
    # narrower asset step actually ran (otherwise step 2 WAS site-wide).
    if serials:
        try:
            fleet = corpus_search(user=user, query=query, top_k=top_k, fleet_wide=True)
            hit_count = len(fleet.get("chunks") or ())
            attempts.append({
                "step": "fleet_wide_controlled",
                "outcome": "hit" if hit_count else "no_relevant_passage",
                "hit_count": hit_count,
            })
            if hit_count:
                return _finish(
                    fleet,
                    source_class="controlled_document",
                    labels=["fleet_wide_unverified_applicability"],
                )
        except Exception:
            attempts.append({
                "step": "fleet_wide_controlled",
                "outcome": "search_failed",
                "hit_count": 0,
            })

    # Step 5 — attachments for the SAME asset set (source class changes,
    # asset scope never). A serial-less set already returned above.
    if attachment_search is not None:
        try:
            attachments = attachment_search(
                user=user,
                query=query,
                scope_asset_ids=tuple(sorted(asset_set.serials)),
            )
            hit_count = len(attachments.get("chunks") or ())
            attempts.append({
                "step": "asset_attachments",
                "outcome": "hit" if hit_count else "no_relevant_passage",
                "hit_count": hit_count,
            })
            if hit_count:
                return _finish(
                    attachments,
                    source_class="asset_attachment",
                    labels=["uncontrolled_attachment"],
                )
        except Exception:
            attempts.append({
                "step": "asset_attachments",
                "outcome": "search_failed",
                "hit_count": 0,
            })
    else:
        attempts.append({"step": "asset_attachments", "outcome": "not_attempted"})

    # Step 6 — thread uploads are already in model context; never orchestrated.
    attempts.append({"step": "thread_uploads", "outcome": "not_attempted"})

    return {
        "chunks": [],
        "attempts": attempts,
        "warnings": [NO_RELEVANT_PASSAGE],
    }


__all__ = [
    "APPLICABILITY_UNRESOLVED",
    "MAX_INVENTORY_ROWS",
    "SOURCE_CLASSES",
    "AmbiguousDocumentRef",
    "AssetSet",
    "attachment_inventory",
    "controlled_document_inventory",
    "inventory",
    "resolve_asset_set",
    "resolve_selected_document",
    "retrieve_manual_fact",
    "source_state_for_controlled_row",
    "source_state_for_ingest_row",
]
