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
    *, scope_key: str, asset_id: str | None = None, document_class: str | None = None
) -> str:
    """Build the non-negotiable corpus filter from server-side values only."""
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
    if asset_id:
        clauses.append(f"asset_id eq '{_odata_literal(asset_id)}'")
    if document_class:
        clauses.append(f"document_class eq '{_odata_literal(document_class)}'")
    return " and ".join(clauses)


def _display_title(row: dict[str, Any]) -> str:
    """Best-effort friendly document name for a citation.

    The registry is authoritative when present, but it does not exist on
    every deployment's database (and is empty on ones whose documents were
    indexed before the table was migrated), so the fallback derives a title
    from the source file name.
    """
    document_id = str(row.get("document_id") or "")
    revision = str(row.get("document_revision") or "")
    try:
        from aichat.models import ControlledDocument

        registered = (
            ControlledDocument.objects
            .filter(document_id=document_id, revision=revision)
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
) -> dict[str, Any]:
    """Search the current, site-scoped controlled documents and cite chunks.

    ``machine_resolver`` exists for tests; production resolution always goes
    through ``assets.ai_read.machines_in_scope`` under the acting user.
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
            return {
                "chunks": [],
                "total": 0,
                "machine_filter": "ambiguous",
                "machine_candidates": candidates,
            }

    if embedding_client is None:
        embedding_client = _default_embedding_client()
    if search_client is None:
        search_client = AzureSelectedDocumentSearch.from_settings().client()

    filter_expression = corpus_filter(
        scope_key=scope_key, asset_id=asset_id, document_class=document_class
    )
    vector = _query_vector(
        query=query, embedding_client=embedding_client, dimensions=embedding_dimensions
    )
    try:
        from azure.search.documents.models import VectorizedQuery

        rows = search_client.search(
            search_text=query,
            vector_queries=[
                VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="text_vector")
            ],
            vector_filter_mode="preFilter",
            filter=filter_expression,
            top=top_k,
            query_type="semantic",
            semantic_configuration_name="semantic-default",
            select=_SELECT_FIELDS,
        )
    except ControlledDocumentSearchError:
        raise
    except Exception as exc:
        raise ControlledDocumentSearchError(
            "Controlled-document Search query failed",
            code="CONTROLLED_DOCUMENT_SEARCH_FAILED",
        ) from exc

    chunks: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("chunk") or "")[:8000]
        chunks.append({
            "excerpt": text,
            "score": row.get("@search.score", 0),
            "citation": {
                "document": _display_title(row),
                "document_id": str(row.get("document_id") or ""),
                "revision": str(row.get("document_revision") or ""),
                "section_id": str(row.get("section_id") or ""),
                "section_path": str(row.get("section_path") or ""),
                "chunk_id": str(row.get("chunk_id") or row.get("id") or ""),
                "as_of": str(row.get("as_of") or ""),
                "excerpt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
        })
    return {
        "chunks": chunks,
        "total": len(chunks),
        "machine_filter": machine_filter,
    }


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
      document_class: Optional filter: technical_manual, procedure,
                      specification or knowledge_base.
      top_k: Maximum passages to return (default 5, max 5).

    Returns:
      Dictionary with 'chunks' (each an excerpt with a citation naming the
      document, section and revision), 'total' and 'machine_filter'
      (applied / not_applied / ambiguous -- ambiguous includes
      machine_candidates to pick from).
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
            return search_corpus(
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

    return await _run()


CONTROLLED_CORPUS_TOOLS = [search_manuals]

__all__ = [
    "CONTROLLED_CORPUS_TOOLS",
    "ControlledDocumentSearchError",
    "corpus_filter",
    "search_corpus",
    "search_manuals",
]
