"""Site-scoped Azure AI Search retrieval over the controlled-document corpus.

The corpus twin of ``controlled_document_search``. That module's contract is
"registry-row coordinates only"; this one deliberately answers the open
question -- "what does the manual say about X?" -- so its filter is built from
server-side deployment constants instead:

* ``scope_key eq '<AIMMS_SINGLE_SITE_POLICY_KEY>'`` -- the deployment's own
  site key, never caller input. An empty key refuses rather than widening.
* ``is_current eq true`` -- superseded revisions never answer.
* an ``access_class`` allow-list tied to the pack's permission.

Machine narrowing is resolved server-side through ``assets.ai_read`` (the
model supplies a name, never an ``asset_id`` literal) and degrades to the
site-wide query when the machine cannot be resolved -- narrowing is a
precision feature, not the authorization boundary.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from ai.core.integrations.controlled_document_search import (
    AzureSelectedDocumentSearch,
    ControlledDocumentSearchError,
    EmbeddingClient,
    SearchClient,
    _odata_literal,
    _query_vector,
)
from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

#: Access classes a maintenance-authorized reader may see. Server-side
#: constant; grows only by decision.
_READABLE_ACCESS_CLASSES = ("maintenance_authorized",)

#: Document classes accepted as a narrowing argument. Anything else is refused
#: (the argument comes from the model).
_DOCUMENT_CLASS_ALLOWLIST = (
    "technical_manual",
    "controlled_o_and_m",
    "procedure",
    "specification",
    "knowledge_base",
    # The live corpus's actual class (found 2026-08-06 when every allowlisted
    # narrowing filtered out the whole manual). "Grows only by decision" —
    # this is that decision; retire it after the manual is re-classed to
    # controlled_o_and_m at its next re-ingestion.
    "controlled_operations_maintenance_diagnostics_repair_knowledge",
)

_SELECT_FIELDS = [
    "id",
    "chunk_id",
    "document_id",
    "document_revision",
    "source_file_name",
    "section_id",
    "section_path",
    "heading_1",
    "heading_2",
    "heading_3",
    "chunk",
    "as_of",
    "access_class",
    "asset_id",
    "document_class",
]


def corpus_filter(
    *,
    scope_key: str,
    asset_id: str | None = None,
    document_class: str | None = None,
    asset_ids: tuple[str, ...] | None = None,
    fleet_wide: bool = False,
) -> str:
    """Build the non-negotiable corpus filter from server-side values only.

    ``asset_ids`` (S5 enforce mode) narrows to the analysis scope's
    server-resolved machine serials — a multi-valued alternative to the
    single resolver-derived ``asset_id``; passing both is a programming
    error and the multi-valued clause wins. ``fleet_wide`` (S8a §8.4 step 4)
    selects ONLY blank-stamp site-wide documents — a source-class change,
    never an asset-scope widening, because a blank-stamp document has no
    asset by construction.
    """
    if not scope_key:
        raise ControlledDocumentSearchError(
            "Controlled-document site scope is not configured",
            code="CONTROLLED_DOCUMENT_SCOPE_UNCONFIGURED",
        )
    clauses = [
        f"scope_key eq '{_odata_literal(scope_key)}'",
        "is_current eq true",
    ]
    if len(_READABLE_ACCESS_CLASSES) == 1:
        clauses.append(f"access_class eq '{_odata_literal(_READABLE_ACCESS_CLASSES[0])}'")
    else:  # pragma: no cover - grows only by decision
        quoted = ",".join(_odata_literal(item) for item in _READABLE_ACCESS_CLASSES)
        clauses.append(f"search.in(access_class, '{quoted}', ',')")
    if fleet_wide:
        clauses.append("asset_id eq ''")
    elif asset_ids:
        joined = ",".join(_odata_literal(item) for item in sorted(asset_ids))
        clauses.append(f"search.in(asset_id, '{joined}', ',')")
    elif asset_id:
        clauses.append(f"asset_id eq '{_odata_literal(asset_id)}'")
    if document_class:
        clauses.append(f"document_class eq '{_odata_literal(document_class)}'")
    return " and ".join(clauses)


def _display_title(row: dict[str, Any], *, scope_key: str) -> str:
    """Best-effort friendly document name for a citation.

    The registry is authoritative when present, but it does not exist on
    every deployment's database (and is empty on ones whose documents were
    indexed before the table was migrated), so the fallback derives a title
    from the source file name.

    ``scope_key`` is part of the lookup (S8a, §8.4): an identical
    document_id/revision registered under ANOTHER deployment boundary must
    never supply this boundary's labels or locators.
    """
    document_id = str(row.get("document_id") or "")
    revision = str(row.get("document_revision") or "")
    try:
        from aichat.models import ControlledDocument

        registered = (
            ControlledDocument.objects
            .filter(scope_key=scope_key, document_id=document_id, revision=revision)
            .values_list("title", flat=True)
            .first()
        )
        if registered:
            return str(registered)
    except Exception:
        pass

    stem = str(row.get("source_file_name") or document_id or "document")
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    friendly = stem.replace("_", " ").strip() or document_id or "document"
    return f"{friendly} (rev {revision})" if revision else friendly


def search_corpus(
    *,
    user,
    query: str,
    machine: str | None = None,
    document_class: str | None = None,
    top_k: int = 5,
    search_client: SearchClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    embedding_dimensions: int = 3072,
    machine_resolver=None,
    asset_ids: tuple[str, ...] | None = None,
    fleet_wide: bool = False,
) -> dict[str, Any]:
    """Search the current, site-scoped controlled documents and cite chunks.

    ``machine_resolver`` exists for tests; production resolution always goes
    through ``assets.ai_read.machines_in_scope`` under the acting user.

    ``asset_ids`` and ``fleet_wide`` are SERVER-SIDE ONLY (never exposed on
    the ``search_manuals`` model schema): the S8a fallback orchestrator
    passes the frozen asset-set serials explicitly, or requests the labeled
    blank-stamp site-wide step. An explicit ``asset_ids`` wins over both the
    bound scope context and machine-name resolution.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise ControlledDocumentSearchError(
            "query is invalid", code="CONTROLLED_DOCUMENT_QUERY_INVALID"
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise ControlledDocumentSearchError(
            "top_k is invalid", code="CONTROLLED_DOCUMENT_QUERY_INVALID"
        )
    top_k = max(1, min(top_k, 5))
    if document_class is not None and document_class not in _DOCUMENT_CLASS_ALLOWLIST:
        raise ControlledDocumentSearchError(
            "document_class is not recognised",
            code="CONTROLLED_DOCUMENT_QUERY_INVALID",
        )

    from ai.core.config import get_settings

    scope_key = get_settings().single_site_policy_key or ""
    if not scope_key:
        # Checked before any network call: a blank policy key must refuse
        # without embedding the query or touching the search service.
        raise ControlledDocumentSearchError(
            "Controlled-document site scope is not configured",
            code="CONTROLLED_DOCUMENT_SCOPE_UNCONFIGURED",
        )

    # S5 (WP-A3): under an ENFORCED explicit analysis scope, the asset
    # filter comes from the scope's server-resolved serials — the
    # model-supplied machine name is never the scope mechanism (§8.4), and
    # the site-wide name-degrade below is structurally unreachable. A scoped
    # machine without a stable serial cannot be mapped to documents:
    # applicability is unresolved, and the answer is a typed miss, never a
    # fleet-wide search.
    from ai.core.analysis.scope_context import current_turn_scope

    scope_context = current_turn_scope()
    scope_asset_ids: tuple[str, ...] | None = None
    if asset_ids is not None or fleet_wide:
        # S8a orchestrator call: the caller already froze the asset set (or
        # asked for the labeled site-wide step); nothing here may widen it.
        scope_asset_ids = tuple(sorted(asset_ids)) if asset_ids else None
    elif scope_context is not None and scope_context.explicit and scope_context.enforce:
        if scope_context.machine_serials:
            scope_asset_ids = tuple(sorted(scope_context.machine_serials))
        else:
            _record_search_outcome(
                user=user,
                query=query,
                hit_count=0,
                top_score=None,
                machine_filter="scope_unresolved",
                document_class=document_class,
                scope_key=scope_key,
                **_scope_ledger_fields(scope_context, enforced=True),
            )
            return {
                "chunks": [],
                "returned_count": 0,
                "machine_filter": "scope_unresolved",
                "applicability": "unresolved",
                "scope_miss": True,
            }

    asset_id: str | None = None
    machine_filter = "not_requested"
    if fleet_wide:
        machine_filter = "fleet_wide"
    elif scope_asset_ids is not None:
        machine_filter = "scope_applied" if asset_ids is None else "explicit_asset_set"
    elif machine:
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
                document_class=document_class,
                scope_key=scope_key,
            )
            _propose_machine_question(candidates)
            return {
                "chunks": [],
                "returned_count": 0,
                "machine_filter": "ambiguous",
                "machine_candidates": candidates,
            }

    if embedding_client is None:
        embedding_client = _default_embedding_client()
    if search_client is None:
        search_client = AzureSelectedDocumentSearch.from_settings().client()

    vector = _query_vector(
        query=query, embedding_client=embedding_client, dimensions=embedding_dimensions
    )

    def _run(class_filter: str | None):
        filter_expression = corpus_filter(
            scope_key=scope_key,
            asset_id=asset_id,
            document_class=class_filter,
            asset_ids=scope_asset_ids,
            fleet_wide=fleet_wide,
        )
        try:
            from azure.search.documents.models import VectorizedQuery

            return list(
                search_client.search(
                    search_text=query,
                    vector_queries=[
                        VectorizedQuery(
                            vector=vector, k_nearest_neighbors=top_k, fields="text_vector"
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
        except ControlledDocumentSearchError:
            raise
        except Exception as exc:
            raise ControlledDocumentSearchError(
                "Controlled-document Search query failed",
                code="CONTROLLED_DOCUMENT_SEARCH_FAILED",
            ) from exc

    rows = _run(document_class)
    if not rows and document_class:
        # Class narrowing is a precision hint, exactly like machine narrowing
        # above — never the reason a corpus that HAS the answer reports none.
        # The live corpus carries class values outside the request allowlist
        # (found 2026-08-06: every chunk classed
        # ``controlled_operations_maintenance_diagnostics_repair_knowledge``),
        # so an allowlisted narrowing filtered out everything and the model
        # honestly reported an empty manual. Degrade to the class-free result.
        # S5: under an enforced scope this is SOURCE-CLASS fallback only —
        # ``scope_asset_ids`` still rides ``_run``, so the retry can widen
        # the document class but never the asset scope (the no-broadening
        # invariant).
        rows = _run(None)
    if scope_context is not None and scope_context.explicit and scope_context.shadow:
        # Shadow evidence: whether the legacy result would have changed under
        # the scope's serial filter. Content-free — counts only.
        out_of_scope_rows = sum(
            1
            for row in rows
            if str(row.get("asset_id") or "")
            and str(row.get("asset_id")) not in scope_context.machine_serials
        )
        if out_of_scope_rows and not scope_context.enforce:
            logger.info("scope.shadow.manuals out_of_scope=%d of=%d", out_of_scope_rows, len(rows))
    else:
        out_of_scope_rows = 0

    from ai.core.tools.diagnostics import fence_untrusted_content

    chunks: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("chunk") or "")[:8000]
        chunks.append({
            # S5: the one previously-unfenced retrieval text. The citation's
            # excerpt_hash stays over the RAW truncated text (the grounding
            # auditor's excerpt identity), only the model-visible excerpt is
            # fenced.
            "excerpt": fence_untrusted_content(text),
            "score": row.get("@search.score", 0),
            "citation": {
                "document": _display_title(row, scope_key=scope_key),
                "document_id": str(row.get("document_id") or ""),
                "revision": str(row.get("document_revision") or ""),
                "section_id": str(row.get("section_id") or ""),
                "section_path": str(row.get("section_path") or ""),
                "chunk_id": str(row.get("chunk_id") or row.get("id") or ""),
                "as_of": str(row.get("as_of") or ""),
                # The indexed machine identity (serial), so downstream
                # grounding can fence a citation from the WRONG machine's
                # manual (P8-W0a). Empty for site-wide documents.
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
        document_class=document_class,
        scope_key=scope_key,
        out_of_scope_hits=out_of_scope_rows,
        **_scope_ledger_fields(scope_context, enforced=scope_asset_ids is not None),
    )
    # S5 §7.4: semantic retrieval never evaluates a population, so
    # complete_population is ALWAYS False here — zero hits mean "no relevant
    # passage retrieved", the strongest absence statement this surface makes.
    from ai.core.contracts.retrieval import (
        NO_RELEVANT_PASSAGE,
        build_envelope,
        record_envelope,
    )

    envelope = build_envelope(
        source_class="controlled_document",
        population_type="document_chunks",
        operation="semantic_search",
        filters={
            "machine_filter": machine_filter,
            "document_class": document_class or None,
        },
        coverage={
            "population_count": len(chunks),
            "returned_count": len(chunks),
            "complete_population": False,
            "display_truncated": False,
            "cursor": None,
        },
        source_state={
            "registered": True,
            "indexed": True,
            "searchable_now": True,
            "current": True,
            # S8a: computed, not asserted — "attached" means the hits carry
            # an ingest-time asset stamp; ``applicable`` stays False (the
            # build_envelope default) until S8b's verified relation exists.
            "attached": any(bool(row.get("asset_id")) for row in rows),
        },
        warnings=() if chunks else (NO_RELEVANT_PASSAGE,),
    )
    record_envelope("search_manuals", envelope)
    return {
        "chunks": chunks,
        "returned_count": len(chunks),
        "machine_filter": machine_filter,
        "retrieval": envelope,
    }


def search_pinned_document(
    *,
    user,
    document_row,
    query: str,
    top_k: int = 5,
    search_client: SearchClient | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> dict[str, Any]:
    """Exact-revision search adapted to the corpus result shape (S8a).

    ``document_row`` is a SERVER-resolved ``ControlledDocument`` registry
    row (``source_gateway.resolve_selected_document``); this wires the
    four-way pin — scope_key + document_id + revision + content hash —
    back into production, with excerpts fenced exactly like the corpus
    path (the hash stays over the raw truncated text).
    """
    from ai.core.integrations.controlled_document_search import (
        search_selected_document,
    )
    from ai.core.tools.diagnostics import fence_untrusted_content

    kwargs: dict[str, Any] = {}
    if search_client is not None:
        kwargs["search_client"] = search_client
    if embedding_client is not None:
        kwargs["embedding_client"] = embedding_client
    pinned = search_selected_document(document=document_row, query=query, top_k=top_k, **kwargs)

    chunks: list[dict[str, Any]] = []
    for row in pinned.get("chunks") or ():
        citation = dict(row.get("citation") or {})
        citation.setdefault("document", document_row.title)
        citation.setdefault("revision", document_row.revision)
        citation["pinned"] = True
        chunks.append({
            "excerpt": fence_untrusted_content(str(row.get("chunk") or "")),
            "score": row.get("score", 0),
            "citation": citation,
        })

    _record_search_outcome(
        user=user,
        query=query,
        hit_count=len(chunks),
        top_score=max((float(row["score"] or 0) for row in chunks), default=None),
        machine_filter="document_pinned",
        document_class=None,
        scope_key=document_row.scope_key,
    )
    from ai.core.contracts.retrieval import (
        NO_RELEVANT_PASSAGE,
        build_envelope,
        record_envelope,
    )

    envelope = build_envelope(
        source_class="controlled_document",
        population_type="document_chunks",
        operation="pinned_search",
        filters={"document_id": document_row.document_id, "revision": document_row.revision},
        coverage={
            "population_count": len(chunks),
            "returned_count": len(chunks),
            "complete_population": False,
            "display_truncated": False,
            "cursor": None,
        },
        source_state={
            "registered": True,
            "indexed": True,
            "searchable_now": True,
            "current": True,
            "attached": bool(document_row.asset_id),
        },
        warnings=() if chunks else (NO_RELEVANT_PASSAGE,),
    )
    record_envelope(
        "search_manuals",
        envelope,
        pinned_sha_prefix=(document_row.source_sha256 or "")[:12],
    )
    return {
        "chunks": chunks,
        "returned_count": len(chunks),
        "machine_filter": "document_pinned",
        "pinned_revision": document_row.revision,
        "retrieval": envelope,
    }


def _record_search_outcome(**kwargs) -> None:
    """Write the A7 ledger row; the writer itself is fail-soft (S16)."""
    from aichat.services.retrieval_misses import record_search

    record_search(**kwargs)


def _scope_ledger_fields(scope_context, *, enforced: bool) -> dict[str, Any]:
    """The S5 scope columns for a RetrievalMiss row; empty when unscoped."""
    if scope_context is None:
        return {}
    return {
        "scope_hash": scope_context.scope_hash,
        "scope_mode": scope_context.mode,
        "scope_enforced": bool(enforced),
    }


def _propose_machine_question(candidates: list[dict[str, Any]]) -> None:
    """Propose a structured machine-disambiguation question (S22).

    The candidates came from the RBAC-scoped resolver, so the options inherit
    its scope. Fail-soft and flag-gated: with the flag off (or on any error)
    the tool's return value is the only signal, exactly as before.
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
            "source": "manual_search_ambiguity",
            "question_text": "Which machine do you mean?",
            "options": options,
        })
    except Exception:
        logger.warning("Machine question proposal failed; ambiguity stays textual")


def _default_embedding_client() -> EmbeddingClient:
    """Build the embedding client lazily so tests can inject fakes."""
    from ai.core.integrations.controlled_document_indexing import (
        AzureOpenAIEmbeddingClient,
    )

    return AzureOpenAIEmbeddingClient.from_settings()


def _current_user():
    """Resolve the acting Django user from the authenticated ASGI boundary."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    return get_user_model().objects.filter(pk=principal.user_pk).first()


@ai_function
async def search_manuals(
    query: str,
    machine: str | None = None,
    document_class: str | None = None,
    document: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    Search the controlled technical manuals and knowledge bases for this site.

    Use this whenever the user asks what the manual, O&M documentation or
    knowledge base says -- e.g. repair boundaries, procedures, torque values,
    approved parts. Results carry section citations.

    Args:
      query: What to look for, in natural language.
      machine: Optional machine name to narrow results to that asset's
               documents. If it cannot be resolved the search still runs
               site-wide.
      document_class: Optional filter: technical_manual, controlled_o_and_m,
                      procedure, specification, knowledge_base or
                      controlled_operations_maintenance_diagnostics_repair_knowledge.
                      A class with no hits degrades to the site-wide result.
      document: ONLY when the user names a specific document ("the HX-200
                manual", an exact document id): pin the search to that
                document's current controlled revision. An unresolved name
                falls back to the normal search with the miss recorded;
                an ambiguous name returns document_candidates to pick from.
      top_k: Maximum passages to return (default 5, max 5).

    Returns:
      Dictionary with 'chunks' (each an excerpt with a citation naming the
      document, section and revision), 'total' and 'machine_filter'
      (applied / not_applied / ambiguous / document_pinned -- ambiguous
      includes machine_candidates to pick from).
    """

    @sync_to_async
    def _run():
        user = _current_user()
        if user is None:
            return {
                "success": False,
                "error": "No authenticated user is available for this search.",
            }
        pin_attempted = None
        if document:
            # S8a revision pinning BEFORE semantic retrieval: the name is a
            # registry lookup key inside scope_key — narrowing only.
            from ai.core.analysis.source_gateway import (
                AmbiguousDocumentRef,
                resolve_selected_document,
            )
            from ai.core.config import get_settings

            scope_key = get_settings().single_site_policy_key or ""
            resolved = resolve_selected_document(scope_key=scope_key, document_ref=document)
            if isinstance(resolved, AmbiguousDocumentRef):
                return {
                    "chunks": [],
                    "returned_count": 0,
                    "machine_filter": "ambiguous",
                    "document_candidates": [
                        {"document_id": doc_id, "title": title, "revision": revision}
                        for doc_id, title, revision in resolved.candidates
                    ],
                }
            if resolved is not None:
                try:
                    return search_pinned_document(
                        user=user, document_row=resolved, query=query, top_k=top_k
                    )
                except ControlledDocumentSearchError:
                    pin_attempted = "unavailable"
            else:
                pin_attempted = "unresolved"
        try:
            result = search_corpus(
                user=user,
                query=query,
                machine=machine,
                document_class=document_class,
                top_k=top_k,
            )
        except ControlledDocumentSearchError as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger,
                "controlled-document corpus search failed",
                exc,
                stage="corpus_search",
                level=logging.WARNING,
            )
            return {"success": False, "error": str(exc), "code": exc.code}
        if pin_attempted:
            # The degrade is visible, not silent: the model must not present
            # a corpus hit as the pinned document's content.
            result = dict(result)
            result["pin_attempted"] = pin_attempted
        return result

    return await _run()


CONTROLLED_CORPUS_TOOLS = [search_manuals]

__all__ = [
    "CONTROLLED_CORPUS_TOOLS",
    "ControlledDocumentSearchError",
    "corpus_filter",
    "search_corpus",
    "search_manuals",
    "search_pinned_document",
]
