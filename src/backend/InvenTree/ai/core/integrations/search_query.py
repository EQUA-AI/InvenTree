"""Shared Azure AI Search query construction for every RAG retrieval site.

One leaf module owns the semantic/vector wiring names and the two query
shapes, so the index definition (``create_rag_search_indexes`` imports these
constants) and the four retrieval call sites cannot drift apart silently.

Two builders:

- :func:`semantic_hybrid_kwargs` — the invariant hybrid BM25+vector+semantic
  shape every corpus uses. It REFUSES blank or wildcard search text: Azure's
  L2 reranker reads ``search_text``, so pairing ``query_type='semantic'``
  with ``'*'`` silently degrades every ranking; that misuse is now
  unconstructible rather than merely discouraged.
- :func:`filter_only_kwargs` — the fetch shape for server-side expansions
  (adjacent media segments): pure filter, no ranking, no ``query_type``.

Build kwargs OUTSIDE the call site's ``try`` block: a guard refusal or a
programmer error here must surface as itself, never mislabelled as the
site's provider-fault error code.

Known limitation, decided R5: ``@search.semanticPartialResponseReason``
(the ranker's partial-degradation signal under the service-default
``semantic_error_mode=partial``) is not reachable through the public
surface of ``azure-search-documents`` 12.0.0 (``SearchItemPaged`` exposes
only by_page/get_answers/get_count/get_coverage/get_facets), and reading
private attributes would couple retrieval to SDK internals. Revisit when
the SDK exposes it.
"""

from collections.abc import Sequence
from typing import Any

#: Names shared with the index builder (create_rag_search_indexes imports
#: these — the coupling is deliberate and now explicit).
SEMANTIC_CONFIG = "semantic-default"
VECTOR_PROFILE = "vector-default"
HNSW_NAME = "hnsw-default"


def semantic_hybrid_kwargs(
    *,
    query: str,
    vector: Sequence[float],
    vector_field: str,
    filter_expression: str,
    select: Sequence[str],
    top: int,
) -> dict[str, Any]:
    """The one hybrid query shape every RAG corpus issues.

    ``top`` doubles as ``k_nearest_neighbors`` — the four pre-R5 call sites
    all paired them and the builder preserves that exactly.
    """
    if not query or not query.strip() or query.strip() == "*":
        raise ValueError(
            "semantic queries need real search text; use filter_only_kwargs "
            "for filter-shaped fetches"
        )
    from azure.search.documents.models import VectorizedQuery

    return {
        "search_text": query,
        "vector_queries": [
            VectorizedQuery(
                vector=list(vector),
                k_nearest_neighbors=top,
                fields=vector_field,
            )
        ],
        "vector_filter_mode": "preFilter",
        "filter": filter_expression,
        "top": top,
        "query_type": "semantic",
        "semantic_configuration_name": SEMANTIC_CONFIG,
        "select": select,
    }


def filter_only_kwargs(
    *,
    filter_expression: str,
    select: Sequence[str],
    top: int,
    order_by: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Filter-shaped fetch: wildcard text, NO query_type, NO ranking.

    Every document a filter-only query returns carries
    ``@search.score == 1.0`` — callers must never propagate that score into
    ranking or ledger fields.
    """
    kwargs: dict[str, Any] = {
        "search_text": "*",
        "filter": filter_expression,
        "top": top,
        "select": select,
    }
    if order_by is not None:
        kwargs["order_by"] = list(order_by)
    return kwargs
