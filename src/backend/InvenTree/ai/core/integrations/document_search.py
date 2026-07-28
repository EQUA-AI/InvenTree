"""
Search indexed part documents (manuals, datasheets, specs) in Azure AI Search.

This module provides an AI-callable tool that performs hybrid (keyword + vector)
search against an Azure AI Search index populated via the Foundry portal
"Add your data" feature or the automated ingestion pipeline.

The tool is designed to be registered alongside INVENTORY_TOOLS in WF8.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-initialised clients (created on first call)
# ---------------------------------------------------------------------------
_search_client = None
_openai_client = None


def _get_clients():
    """Lazily create Azure AI Search + OpenAI embedding clients."""
    global _search_client, _openai_client

    if _search_client is not None:
        return _search_client, _openai_client

    from ai.core.config import get_settings

    settings = get_settings()

    index_name = settings.azure_search_documents_index
    if not index_name:
        raise RuntimeError(
            "AZURE_SEARCH_DOCUMENTS_INDEX is not set. "
            "Upload a PDF via Azure AI Foundry 'Add your data' first, "
            "then set this env var to the created index name."
        )

    # --- Search client ---
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient

    _search_client = SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(settings.azure_search_api_key),
    )

    # --- OpenAI client (for query embedding) ---
    try:
        from openai import AzureOpenAI

        _openai_client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
    except Exception:
        logger.warning("OpenAI client unavailable — vector search disabled, using keyword only")
        _openai_client = None

    return _search_client, _openai_client


def _embed_query(text: str) -> list[float] | None:
    """Embed a query string using Azure OpenAI. Returns None on failure."""
    _, openai_client = _get_clients()
    if openai_client is None:
        return None
    try:
        from ai.core.config import get_settings

        settings = get_settings()
        resp = openai_client.embeddings.create(
            model=settings.azure_openai_embedding_deployment,
            input=[text],
        )
        return resp.data[0].embedding
    except Exception as e:
        logger.warning("Embedding failed, falling back to keyword search: %s", e)
        return None


# ---------------------------------------------------------------------------
# AI Tool
# ---------------------------------------------------------------------------


@ai_function
async def search_part_documents(  # noqa: RUF029 - async is the tool-call contract
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    Search indexed part documentation (user manuals, datasheets,
    technical specs) to answer questions about equipment operation,
    troubleshooting, error codes, maintenance, and wiring.

    USE THIS TOOL when the user asks about:
    - Troubleshooting steps or error/fault codes
    - Maintenance procedures, calibration, or wiring diagrams
    - Technical specifications from a manufacturer manual
    - Operating instructions or safety warnings
    - Any question that would normally require reading the equipment manual

    This searches across ALL indexed documents. Results include the
    relevant text chunk, source filename, and page reference.

    Args:
        query: Natural-language search query — describe the symptom,
               error code, procedure name, or topic.
        top_k: Number of results to return (default 5, max 10).

    Returns:
        dict with "results" list (each has content, title, filepath,
        chunk_id) and "total" count.
    """
    try:
        search_client, _ = _get_clients()
    except RuntimeError as e:
        return {"success": False, "error": str(e), "results": []}

    top_k = min(max(top_k, 1), 10)

    # Build search kwargs
    search_kwargs: dict[str, Any] = {
        "search_text": query,
        "top": top_k,
        "query_type": "simple",
    }

    # Try hybrid search (keyword + vector)
    vector = _embed_query(query)
    if vector is not None:
        try:
            from azure.search.documents.models import VectorizedQuery

            search_kwargs["vector_queries"] = [
                VectorizedQuery(
                    vector=vector,
                    k_nearest_neighbors=top_k * 2,
                    fields="text_vector",
                )
            ]
        except ImportError:
            logger.debug("VectorizedQuery not available, keyword search only")

    # Execute search
    try:
        results = search_client.search(**search_kwargs)

        chunks = []
        for r in results:
            chunk: dict[str, Any] = {}
            # Foundry "Add your data" index field names
            chunk["content"] = r.get("content") or r.get("chunk") or ""
            chunk["title"] = r.get("title") or r.get("source_file_name") or r.get("filename") or ""
            chunk["filepath"] = r.get("filepath") or r.get("source_blob_path") or r.get("url") or ""
            chunk["chunk_id"] = r.get("chunk_id") or r.get("id") or ""
            chunk["score"] = r.get("@search.score", 0)
            chunks.append(chunk)

        if not chunks:
            return {
                "success": True,
                "results": [],
                "total": 0,
                "message": f"No matching documentation found for: '{query}'",
            }

        return {
            "success": True,
            "results": chunks,
            "total": len(chunks),
        }

    except Exception as e:
        logger.exception("Document search failed")
        return {"success": False, "error": str(e), "results": []}


# ---------------------------------------------------------------------------
# Exported tool list (mirrors pattern of EMAIL_TOOLS, KANBAN_TOOLS, etc.)
# ---------------------------------------------------------------------------

DOCUMENT_SEARCH_TOOLS = [search_part_documents]
