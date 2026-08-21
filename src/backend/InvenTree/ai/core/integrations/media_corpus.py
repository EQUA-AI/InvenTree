"""Client-scoped Azure AI Search retrieval over the evidence-media corpus (R3).

The retrieval twin of ``MediaSearchProjection`` (the write-side) and the
media-space sibling of ``attachment_corpus``. It answers "what does the
evidence photo show?" over auto-ingested work-order, step-execution and
machine media — the ``evidence_recording`` trust tier, structurally separate
from both document corpora:

* ``scope_key eq '<AIMMS_SINGLE_SITE_POLICY_KEY>'`` — the deployment's own
  site key, never caller input. An empty key refuses rather than widening.
* ``is_current eq true`` — superseded revisions never answer.
* ``access_class eq 'evidence_recording'`` — pinned; evidence media must
  never satisfy a ``maintenance_authorized`` (or ``attachment_uploaded``)
  filter, and vice versa.
* ``search.in(model_type, ...)`` — the server-side owner allow-list. All
  three owners sit under the single ``work_order:view`` arm (spec §7.3);
  part-owned media never ingests, so no part arm exists.
* ``client_codes/any(...)`` — the actor's resolved client grants, derived
  server-side via ``tasks.scope.client_codes_for_actor``. Fail-closed: an
  unresolved scope or an empty grant set refuses, indistinguishable from an
  empty corpus (denial == nonexistence). Codes stay in the filter expression
  only — never in the payload, citations or errors.

Work-order and machine narrowing are resolved server-side (the model supplies
a reference or name, never an id literal) and degrade to the broad query when
unresolved — narrowing is a precision feature, not the authorization
boundary. Unlike the document tool's part/machine pair, ``work_order`` and
``machine`` legitimately combine: a WO photo carries both coordinates.
Excerpts (caption + OCR + transcript) and the free-text citation fields are
wrapped in the ``[UNTRUSTED-CONTENT]`` fence: the pixels are attacker-
writable by anyone holding the upload roles, and captions/OCR are
model-authored over those pixels. Results never include image bytes or
storage paths: the stored thumbnail path embeds the uploader-chosen
filename, so the UI resolves thumbnails from ``attachment_id`` via the
authenticated attachment API instead.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from ai.core.integrations.controlled_document_search import (
    EmbeddingClient,
    SearchClient,
    _odata_literal,
)
from ai.core.maf_compat import ai_function
from ai.core.tools.diagnostics import fence_untrusted_content
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

#: The single access class this tool may read. Server-side constant; both
#: document corpora's classes are deliberately not readable here.
_ACCESS_CLASS = "evidence_recording"

#: The server-side owner allow-list. Constant, not role-derived: every owner
#: is granted by the one ``work_order:view`` arm (machine photos are evidence
#: too — same tier as ``get_machine_attachments``).
_MEDIA_MODEL_TYPES = ("workorder", "workorderstepexecution", "assetmachine")

#: Media types accepted as a narrowing argument (``MediaSegmentType``).
#: ``video_segment`` stays legal pre-R4: an empty result degrade-retries and
#: each chunk carries its own ``media_type``, so the model sees what actually
#: returned. Anything else is refused (the argument comes from the model).
_MEDIA_TYPE_ALLOWLIST = ("image", "video_segment")

#: Retrieval projection of the index. Never ``client_codes``, ``scope_key``,
#: ``source_sha256`` or the vector — authorization coordinates and content
#: identity stay server-side.
_SELECT_FIELDS = [
    "id",
    "attachment_id",
    "media_type",
    "model_type",
    "model_id",
    "work_order_id",
    "step_execution_id",
    "asset_id",
    "machine_name",
    "segment_index",
    "segment_count",
    "timecode_start_s",
    "timecode_end_s",
    "duration_s",
    "caption",
    "ocr_text",
    "transcript",
    "source_file_name",
    "recorded_at",
    "uploaded_at",
    "indexed_at",
    "access_class",
]

#: Excerpts are truncated to this length BEFORE hashing and fencing, matching
#: the document corpora's bound.
_EXCERPT_MAX_CHARS = 8000


class MediaRetrievalError(Exception):
    """A stable evidence-media retrieval failure with a value-free code."""

    code = "MEDIA_SEARCH_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def evidence_media_filter(
    *,
    scope_key: str,
    client_codes: frozenset[str] | set[str],
    model_types: tuple[str, ...] | list[str],
    work_order_id: int | None = None,
    step_execution_id: int | None = None,
    asset_id: str | None = None,
    media_type: str | None = None,
) -> str:
    """Build the non-negotiable media filter from server-side values only.

    Clause order is fixed (tests pin the exact string). ``client_codes`` and
    ``model_types`` are mandatory: an empty set refuses rather than widening,
    mirroring ``machine_scope_filter``'s ``pk__in=[]`` reasoning — a missing
    clause would read as "everyone's".
    """
    if not scope_key:
        raise MediaRetrievalError(
            "Evidence-media site scope is not configured",
            code="MEDIA_SCOPE_UNCONFIGURED",
        )
    if not model_types:
        raise MediaRetrievalError("No media owner is authorized", code="MEDIA_SCOPE_UNRESOLVED")
    if not client_codes:
        raise MediaRetrievalError("Actor scope names no client", code="MEDIA_SCOPE_UNRESOLVED")
    for code in client_codes:
        # Client.code is an immutable SlugField, so these characters are
        # unreachable — but the filter builder must not trust that.
        if "," in code or "'" in code:
            raise MediaRetrievalError("Actor client scope is invalid", code="MEDIA_SCOPE_INVALID")

    clauses = [
        f"scope_key eq '{_odata_literal(scope_key)}'",
        "is_current eq true",
        f"access_class eq '{_ACCESS_CLASS}'",
    ]
    if len(model_types) == 1:
        clauses.append(f"model_type eq '{_odata_literal(model_types[0])}'")
    else:
        joined = ",".join(_odata_literal(item) for item in model_types)
        clauses.append(f"search.in(model_type, '{joined}', ',')")
    joined_codes = ",".join(_odata_literal(code) for code in sorted(client_codes))
    clauses.append(f"client_codes/any(c: search.in(c, '{joined_codes}', ','))")
    if work_order_id is not None:
        clauses.append(f"work_order_id eq {int(work_order_id)}")
    if step_execution_id is not None:
        clauses.append(f"step_execution_id eq {int(step_execution_id)}")
    if asset_id:
        clauses.append(f"asset_id eq '{_odata_literal(asset_id)}'")
    if media_type:
        clauses.append(f"media_type eq '{_odata_literal(media_type)}'")
    return " and ".join(clauses)


def _granted(user) -> bool:
    """Whether the acting user's roles grant the single media arm."""
    if getattr(user, "is_superuser", False):
        return True

    from users.permissions import check_user_role

    return bool(check_user_role(user, "work_order", "view"))


def _display_title(row: dict[str, Any]) -> str:
    """Friendly media name for a citation, from the source file name."""
    stem = str(row.get("source_file_name") or "photo")
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return stem.replace("_", " ").strip() or "photo"


def search_corpus_media(
    *,
    user,
    query: str,
    work_order: str | None = None,
    machine: str | None = None,
    media_type: str | None = None,
    top_k: int = 5,
    search_client: SearchClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    machine_resolver=None,
    work_order_resolver=None,
) -> dict[str, Any]:
    """Search current evidence media and cite fenced caption/OCR excerpts.

    ``machine_resolver``/``work_order_resolver`` exist for tests; production
    resolution goes through ``assets.ai_read.machines_in_scope`` and
    ``tasks.ai_read`` under the acting user. Every refusal happens before any
    network call — flag, validation, site scope, arm grant and client codes
    are all checked before the query is embedded.
    """
    from ai.core.config import get_settings

    settings = get_settings()
    if not settings.feature_media_rag_retrieval:
        raise MediaRetrievalError(
            "Evidence-media retrieval is not enabled",
            code="MEDIA_RETRIEVAL_DISABLED",
        )
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise MediaRetrievalError("query is invalid", code="MEDIA_QUERY_INVALID")
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise MediaRetrievalError("top_k is invalid", code="MEDIA_QUERY_INVALID")
    top_k = max(1, min(top_k, 5))
    if media_type is not None and media_type not in _MEDIA_TYPE_ALLOWLIST:
        raise MediaRetrievalError("media_type is not recognised", code="MEDIA_QUERY_INVALID")
    # Unlike the document tool's part/machine pair, work_order and machine
    # combine legitimately: a WO photo carries both coordinates, so ANDing
    # both clauses narrows honestly instead of guaranteeing zero hits.

    scope_key = settings.single_site_policy_key or ""
    if not scope_key:
        # Checked before any network call: a blank policy key must refuse
        # without embedding the query or touching the search service.
        raise MediaRetrievalError(
            "Evidence-media site scope is not configured",
            code="MEDIA_SCOPE_UNCONFIGURED",
        )

    if not _granted(user):
        raise MediaRetrievalError("No media owner is authorized", code="MEDIA_SCOPE_UNRESOLVED")
    model_types = _MEDIA_MODEL_TYPES

    from tasks.scope import ScopeError, client_codes_for_actor

    try:
        client_codes = client_codes_for_actor(user)
    except ScopeError as exc:
        # Denial is indistinguishable from an empty corpus downstream; the
        # code (never the client identity) is the whole diagnostic.
        raise MediaRetrievalError(
            "Actor scope is unresolved", code="MEDIA_SCOPE_UNRESOLVED"
        ) from exc

    work_order_id: int | None = None
    work_order_filter = "not_requested"
    if work_order:
        work_order_filter = "not_applied"
        resolver = work_order_resolver
        if resolver is None:

            def resolver(actor, hint):
                from tasks import ai_read

                hint_text = str(hint).strip()[:100]
                if hint_text.isdigit():
                    row = ai_read.authorized_work_order(actor, hint_text)
                    rows = [row] if row is not None else []
                else:
                    rows = ai_read.work_orders_in_scope(actor, query=hint_text, limit=3)
                return [
                    {
                        "work_order_id": row.pk,
                        "reference": row.reference or "",
                        "title": fence_untrusted_content(str(row.title or "")[:255]),
                        "machine": fence_untrusted_content(str(row.machine.name)[:255])
                        if row.machine_id
                        else None,
                    }
                    for row in rows
                ]

        candidates = resolver(user, str(work_order)[:100])
        if len(candidates) == 1:
            work_order_id = int(candidates[0]["work_order_id"])
            work_order_filter = "applied"
        elif len(candidates) > 1:
            _record_search_outcome(
                user=user,
                query=query,
                hit_count=0,
                top_score=None,
                machine_filter="not_requested",
                document_class=media_type,
                scope_key=scope_key,
                corpus="media",
                # The WO narrowing outcome rides the ledger's part_filter
                # column for corpus='media' (documented convention; a
                # dedicated column is a deferred dark-safe migration).
                part_filter="ambiguous",
            )
            return {
                "chunks": [],
                "total": 0,
                "machine_filter": "not_requested",
                "work_order_filter": "ambiguous",
                "work_order_candidates": candidates,
            }

    asset_id: str | None = None
    machine_filter = "not_requested"
    if machine:
        machine_filter = "not_applied"
        resolver = machine_resolver
        if resolver is None:

            def resolver(actor, name):
                from assets import ai_read

                rows = ai_read.machines_in_scope(actor, query=name, limit=3)
                return [
                    {
                        "machine_id": row.pk,
                        "name": row.name,
                        "serial": row.serial or "",
                    }
                    for row in rows
                ]

        candidates = resolver(user, str(machine)[:100])
        if len(candidates) == 1 and candidates[0].get("serial"):
            asset_id = str(candidates[0]["serial"])
            machine_filter = "applied"
        elif len(candidates) > 1:
            _record_search_outcome(
                user=user,
                query=query,
                hit_count=0,
                top_score=None,
                machine_filter="ambiguous",
                document_class=media_type,
                scope_key=scope_key,
                corpus="media",
                part_filter=work_order_filter,
            )
            _propose_machine_question(candidates)
            return {
                "chunks": [],
                "total": 0,
                "machine_filter": "ambiguous",
                "machine_candidates": candidates,
                "work_order_filter": work_order_filter,
            }

    if embedding_client is None:
        embedding_client = _default_embedding_client()
    if search_client is None:
        search_client = _default_search_client()

    try:
        vector = embedding_client.embed_query(query)
    except Exception as exc:
        raise MediaRetrievalError(
            "Evidence-media query embedding failed",
            code="MEDIA_QUERY_EMBEDDING_FAILED",
        ) from exc

    def _run(type_filter: str | None):
        filter_expression = evidence_media_filter(
            scope_key=scope_key,
            client_codes=client_codes,
            model_types=model_types,
            work_order_id=work_order_id,
            asset_id=asset_id,
            media_type=type_filter,
        )
        try:
            from azure.search.documents.models import VectorizedQuery

            return list(
                search_client.search(
                    search_text=query,
                    vector_queries=[
                        VectorizedQuery(
                            vector=vector,
                            k_nearest_neighbors=top_k,
                            fields="media_vector",
                        )
                    ],
                    vector_filter_mode="preFilter",
                    filter=filter_expression,
                    top=top_k,
                    query_type="semantic",
                    semantic_configuration_name="semantic-default",
                    select=_SELECT_FIELDS,
                )
            )
        except MediaRetrievalError:
            raise
        except Exception as exc:
            raise MediaRetrievalError(
                "Evidence-media Search query failed",
                code="MEDIA_SEARCH_FAILED",
            ) from exc

    rows = _run(media_type)
    if not rows and media_type:
        # Type narrowing is a precision hint, exactly like work-order and
        # machine narrowing — never the reason a corpus that HAS the answer
        # reports none. Degrade to the un-narrowed result.
        rows = _run(None)

    chunks: list[dict[str, Any]] = []
    for row in rows:
        parts = [
            str(row.get(field) or "").strip() for field in ("caption", "ocr_text", "transcript")
        ]
        text = "\n".join(part for part in parts if part)[:_EXCERPT_MAX_CHARS]
        chunks.append({
            # Hash over the raw truncation (pre-fence) so the grounding
            # auditor's excerpt identity is independent of fence markers.
            "excerpt": fence_untrusted_content(text),
            "score": row.get("@search.score", 0),
            "citation": {
                # The free-text citation fields — display title, file name —
                # come from the uploader's filename, the same attacker-
                # writable tier as the pixels, and get the same fence.
                # Server-stamped coordinates (ids, tier, serial, timecodes,
                # dates) stay raw. Thumbnails are deliberately NOT returned:
                # the stored path embeds the uploader-chosen filename
                # (thumb_{basename}), which would put attacker-authored text
                # into the payload unfenced. The UI resolves the thumbnail
                # from attachment_id via the authenticated attachment API.
                "document": fence_untrusted_content(_display_title(row)[:255]),
                "source_file_name": fence_untrusted_content(
                    str(row.get("source_file_name") or "")[:255]
                ),
                "attachment_id": int(row.get("attachment_id") or 0),
                "model_type": str(row.get("model_type") or ""),
                "model_id": int(row.get("model_id") or 0),
                "media_type": str(row.get("media_type") or ""),
                "work_order_id": row.get("work_order_id"),
                "step_execution_id": row.get("step_execution_id"),
                "segment_index": int(row.get("segment_index") or 0),
                "timecode_start_s": row.get("timecode_start_s"),
                "timecode_end_s": row.get("timecode_end_s"),
                "chunk_id": str(row.get("id") or ""),
                # The media index has no as_of field; the citation's temporal
                # anchor is the upload date, falling back to indexing time.
                "as_of": str(row.get("uploaded_at") or row.get("indexed_at") or ""),
                "recorded_at": str(row.get("recorded_at") or ""),
                # Evidence tier, surfaced so the model and the grounding rail
                # can distinguish it from both document corpora.
                "access_class": str(row.get("access_class") or _ACCESS_CLASS),
                # The indexed machine identity (serial) so downstream
                # grounding can fence a citation from the WRONG machine's
                # evidence. Empty when the owner has no machine.
                "asset_id": str(row.get("asset_id") or ""),
                "excerpt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
        })
    _record_search_outcome(
        user=user,
        query=query,
        hit_count=len(chunks),
        top_score=max((float(row["score"] or 0) for row in chunks), default=None),
        machine_filter=machine_filter,
        document_class=media_type,
        scope_key=scope_key,
        corpus="media",
        part_filter=work_order_filter,
    )
    return {
        "chunks": chunks,
        "total": len(chunks),
        "machine_filter": machine_filter,
        "work_order_filter": work_order_filter,
    }


def _record_search_outcome(**kwargs) -> None:
    """Write the A7 ledger row; the writer itself is fail-soft (S16)."""
    from aichat.services.retrieval_misses import record_search

    record_search(**kwargs)


def _propose_machine_question(candidates: list[dict[str, Any]]) -> None:
    """Propose a structured machine-disambiguation question (S22).

    Cloned from the document corpora: candidates came from the RBAC-scoped
    resolver, so the options inherit its scope. Fail-soft and flag-gated.
    """
    try:
        from ai.core.config import get_settings

        if not get_settings().feature_question_cards:
            return
        from ai.core.questions.promotion import (
            promote_machine_candidates,
            set_question_proposal,
        )

        options = promote_machine_candidates(candidates, modality="text")
        if len(options) < 2:
            return
        set_question_proposal({
            "source": "evidence_media_ambiguity",
            "question_text": "Which machine do you mean?",
            "options": options,
        })
    except Exception:
        logger.warning("Machine question proposal failed; ambiguity stays textual")


def _default_embedding_client():
    """Build the Gemini client lazily so tests can inject fakes.

    ``embed_query`` is symmetric (the unified cross-modal space makes a text
    query legal against image vectors) — no input-type asymmetry to preserve,
    unlike the Cohere document path.
    """
    from ai.core.integrations.embeddings_gemini import GeminiEmbeddingClient

    return GeminiEmbeddingClient.from_settings()


def _default_search_client():
    """Reuse the projection's accessor: alias guard + key-or-MI credentials."""
    from ai.core.integrations.attachment_search import MediaSearchProjection

    return MediaSearchProjection.from_settings().client()


def _current_user():
    """Resolve the acting Django user from the authenticated ASGI boundary."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    return get_user_model().objects.filter(pk=principal.user_pk).first()


@ai_function
async def search_evidence_media(
    query: str,
    work_order: str | None = None,
    machine: str | None = None,
    media_type: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    Search evidence photos captured on work orders and machines at this site.

    This corpus holds automatically ingested evidence media — photos of
    nameplates, gauges, damage and completed work uploaded to work orders,
    procedure steps and machines. Its excerpts are each photo's caption and
    OCR text, fenced as untrusted content. Use it for "what does the photo
    show", "what evidence was captured on this job" or "what does the
    nameplate read" — NOT for specifications or procedures, which live in
    search_manuals and search_attachment_docs. Results never include the
    image itself. Cite the photo's file name and its work order (and when
    it was taken, if known) in every answer drawn from them.

    Args:
      query: What to look for, in natural language.
      work_order: Optional work-order reference, title or id to narrow to
                  that job's evidence. May be combined with machine. An
                  ambiguous reference returns work_order_candidates;
                  unresolvable ones degrade to the broad search.
      machine: Optional machine name to narrow to that asset's evidence.
               May be combined with work_order. Unresolvable names degrade
               to the broad search.
      media_type: Optional filter: image or video_segment. A type with no
                  hits degrades to the un-narrowed result.
      top_k: Maximum results to return (default 5, max 5).

    Returns:
      Dictionary with 'chunks' (each a fenced caption/OCR excerpt with a
      citation naming the source file and owning work order/machine),
      'total', 'machine_filter' and
      'work_order_filter' (applied / not_applied / ambiguous — ambiguous
      includes candidates to pick from).
    """

    @sync_to_async
    def _run():
        user = _current_user()
        if user is None:
            return {
                "success": False,
                "error": "No authenticated user is available for this search.",
            }
        try:
            return search_corpus_media(
                user=user,
                query=query,
                work_order=work_order,
                machine=machine,
                media_type=media_type,
                top_k=top_k,
            )
        except MediaRetrievalError as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger,
                "evidence media search failed",
                exc,
                stage="media_corpus_search",
                level=logging.WARNING,
            )
            return {"success": False, "error": str(exc), "code": exc.code}

    return await _run()


EVIDENCE_MEDIA_TOOLS = [search_evidence_media]

__all__ = [
    "EVIDENCE_MEDIA_TOOLS",
    "MediaRetrievalError",
    "evidence_media_filter",
    "search_corpus_media",
    "search_evidence_media",
]
