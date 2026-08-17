"""Azure AI Search projection for the attachment-docs text space (R1).

Deliberately not ``AzureSearchProjection``: supersede here must be zero-gap
(decision #15) — upsert the new-sha documents first, then prune the old-sha
ones — whereas the governed adapter deletes before uploading. Full deletion
exists only on the purge path (attachment removed).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_UPLOAD_BATCH = 100
_PRUNE_PAGE = 1000
# Defensive bound only; one attachment never legitimately projects this many
# stale pages of chunks.
_MAX_PRUNE_PAGES = 50


class AttachmentIndexingError(Exception):
    """A bounded attachment Search-projection failure with a value-free code."""

    code = "ATTACHMENT_SEARCH_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class AttachmentSearchProjection:
    """Attachment-docs index adapter (key or managed identity).

    Documents carry deterministic sha-scoped keys
    (``att-{attachment_id}-{sha256[:12]}-c{chunk_index}``), so an upsert of a
    new revision can never collide with the previous one and the two may
    briefly coexist — ``is_current`` stays true on both only for the instant
    between upsert and prune, which is the zero-gap contract.
    """

    def __init__(self, *, endpoint: str, index_name: str, api_key: str = "") -> None:
        self._endpoint = endpoint
        self._index_name = index_name
        self._api_key = api_key
        self._client: Any | None = None

    @property
    def index_name(self) -> str:
        """Serving index stamped onto registry rows."""
        return self._index_name

    @classmethod
    def from_settings(cls) -> AttachmentSearchProjection:
        """Build the adapter from the attachment-RAG Search configuration."""
        from ai.core.config import get_settings

        settings = get_settings()
        index_name = settings.azure_search_attachment_docs_index
        if not settings.azure_search_endpoint or not index_name:
            raise AttachmentIndexingError(
                "Attachment Search configuration is unavailable",
                code="ATTACHMENT_SEARCH_CONFIG_INVALID",
            )
        # Defense in depth beyond the startup validator: never project the
        # auto-ingested corpus into the governed or legacy document indexes.
        # Stripped comparison (F-01): whitespace must not defeat the guard.
        reserved = {
            (settings.azure_search_controlled_documents_index or "").strip(),
            (getattr(settings, "azure_search_documents_index", "") or "").strip(),
        }
        if index_name.strip() in reserved - {""}:
            raise AttachmentIndexingError(
                "Attachment index must not alias another document index",
                code="ATTACHMENT_SEARCH_INDEX_ALIASED",
            )
        return cls(
            endpoint=settings.azure_search_endpoint,
            index_name=index_name,
            api_key=settings.azure_search_api_key,
        )

    def _get_client(self) -> Any:
        """Lazily create a key-backed local client or managed-identity client."""
        if self._client is not None:
            return self._client
        try:
            from azure.core.credentials import AzureKeyCredential
            from azure.search.documents import SearchClient
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise AttachmentIndexingError(
                "Azure Search SDK is unavailable",
                code="ATTACHMENT_SEARCH_UNAVAILABLE",
            ) from exc
        credential: Any
        if self._api_key:
            credential = AzureKeyCredential(self._api_key)
        else:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment packaging
                raise AttachmentIndexingError(
                    "Azure Identity SDK is unavailable",
                    code="ATTACHMENT_SEARCH_UNAVAILABLE",
                ) from exc
            credential = DefaultAzureCredential()
        self._client = SearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=credential,
        )
        return self._client

    def close(self) -> None:
        """Release the underlying SearchClient (it owns a connection pool)."""
        import contextlib

        client, self._client = self._client, None
        closer = getattr(client, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    @staticmethod
    def _all_succeeded(results: Any) -> bool:
        """Accept SDK results only when each document operation succeeded."""
        for result in results:
            if isinstance(result, dict):
                succeeded = result.get("succeeded", False)
            else:
                succeeded = getattr(result, "succeeded", False)
            if not succeeded:
                return False
        return True

    def upsert_documents(self, documents: list[dict[str, object]]) -> None:
        """Add-or-replace the supplied projection in bounded batches."""
        client = self._get_client()
        try:
            for start in range(0, len(documents), _UPLOAD_BATCH):
                uploaded = client.upload_documents(
                    documents=documents[start : start + _UPLOAD_BATCH]
                )
                if not self._all_succeeded(uploaded):
                    raise AttachmentIndexingError(
                        "Attachment projection upload failed",
                        code="ATTACHMENT_SEARCH_UPLOAD_FAILED",
                    )
        except AttachmentIndexingError:
            raise
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger, "Attachment projection upload failed", exc, stage="attachment_project"
            )
            raise AttachmentIndexingError(
                "Attachment projection upload failed",
                code="ATTACHMENT_SEARCH_UPLOAD_FAILED",
            ) from exc

    def _delete_where(self, filter_expression: str, *, code: str) -> int:
        """Delete every document matching a server-authored filter, paged."""
        client = self._get_client()
        deleted_total = 0
        try:
            for _page in range(_MAX_PRUNE_PAGES):
                stale = client.search(
                    search_text="*",
                    filter=filter_expression,
                    select=["id"],
                    top=_PRUNE_PAGE,
                )
                stale_ids = [{"id": row["id"]} for row in stale]
                if not stale_ids:
                    return deleted_total
                deleted = client.delete_documents(documents=stale_ids)
                if not self._all_succeeded(deleted):
                    raise AttachmentIndexingError(
                        "Attachment projection deletion failed", code=code
                    )
                deleted_total += len(stale_ids)
        except AttachmentIndexingError:
            raise
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger, "Attachment projection deletion failed", exc, stage="attachment_project"
            )
            raise AttachmentIndexingError(
                "Attachment projection deletion failed", code=code
            ) from exc
        # Exhausting the defensive page cap with matches still streaming in
        # must be loud: a silent partial delete leaves documents retrievable
        # while the registry believes they are gone (review finding F-05).
        raise AttachmentIndexingError(
            "Attachment projection deletion exhausted the page cap",
            code="ATTACHMENT_SEARCH_PAGE_CAP_EXHAUSTED",
        )

    def prune_stale_sha(self, *, attachment_id: int, keep_sha256: str) -> int:
        """Remove superseded-revision documents after the new ones are live."""
        escaped_sha = keep_sha256.replace("'", "''")
        return self._delete_where(
            f"attachment_id eq {int(attachment_id)} and source_sha256 ne '{escaped_sha}'",
            code="ATTACHMENT_SEARCH_PRUNE_FAILED",
        )

    def purge_attachment(self, *, attachment_id: int) -> int:
        """Remove every document for a deleted attachment (denial ≡ nonexistence)."""
        return self._delete_where(
            f"attachment_id eq {int(attachment_id)}",
            code="ATTACHMENT_SEARCH_PURGE_FAILED",
        )

    def merge_client_codes(self, *, attachment_id: int, client_codes: list[str]) -> int:
        """Re-stamp the authorization coordinate only — no re-extract, no re-embed."""
        client = self._get_client()
        merged_total = 0
        try:
            for _page in range(_MAX_PRUNE_PAGES):
                rows = client.search(
                    search_text="*",
                    filter=f"attachment_id eq {int(attachment_id)}",
                    select=["id", "client_codes"],
                    top=_PRUNE_PAGE,
                )
                updates = [
                    {"id": row["id"], "client_codes": list(client_codes)}
                    for row in rows
                    if list(row.get("client_codes") or []) != list(client_codes)
                ]
                if not updates:
                    return merged_total
                merged = client.merge_documents(documents=updates)
                if not self._all_succeeded(merged):
                    raise AttachmentIndexingError(
                        "Attachment scope re-stamp failed",
                        code="ATTACHMENT_SEARCH_MERGE_FAILED",
                    )
                merged_total += len(updates)
        except AttachmentIndexingError:
            raise
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(logger, "Attachment scope re-stamp failed", exc, stage="attachment_project")
            raise AttachmentIndexingError(
                "Attachment scope re-stamp failed",
                code="ATTACHMENT_SEARCH_MERGE_FAILED",
            ) from exc
        raise AttachmentIndexingError(
            "Attachment scope re-stamp exhausted the page cap",
            code="ATTACHMENT_SEARCH_PAGE_CAP_EXHAUSTED",
        )

    def mark_sha_stale(self, *, attachment_id: int, source_sha256: str) -> int:
        """Set ``is_current: false`` on exactly one revision's documents.

        Decision #15's belt-and-braces (review finding F-09): runs before that
        revision's purge, so R2's ``is_current eq true`` filter stays correct
        even inside a failed/partial supersede window. Sha-scoped on purpose —
        a blanket ``ne`` sweep could stale-mark a concurrently-upserted newer
        revision it never observed (same TOCTOU as blanket pruning).
        """
        client = self._get_client()
        escaped_sha = source_sha256.replace("'", "''")
        filter_expression = (
            f"attachment_id eq {int(attachment_id)} "
            f"and source_sha256 eq '{escaped_sha}' and is_current eq true"
        )
        marked_total = 0
        try:
            for _page in range(_MAX_PRUNE_PAGES):
                rows = client.search(
                    search_text="*",
                    filter=filter_expression,
                    select=["id"],
                    top=_PRUNE_PAGE,
                )
                updates = [{"id": row["id"], "is_current": False} for row in rows]
                if not updates:
                    return marked_total
                merged = client.merge_documents(documents=updates)
                if not self._all_succeeded(merged):
                    raise AttachmentIndexingError(
                        "Attachment stale-marking failed",
                        code="ATTACHMENT_SEARCH_MERGE_FAILED",
                    )
                marked_total += len(updates)
        except AttachmentIndexingError:
            raise
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(logger, "Attachment stale-marking failed", exc, stage="attachment_project")
            raise AttachmentIndexingError(
                "Attachment stale-marking failed",
                code="ATTACHMENT_SEARCH_MERGE_FAILED",
            ) from exc
        raise AttachmentIndexingError(
            "Attachment stale-marking exhausted the page cap",
            code="ATTACHMENT_SEARCH_PAGE_CAP_EXHAUSTED",
        )

    def purge_sha(self, *, attachment_id: int, source_sha256: str) -> int:
        """Remove exactly one revision's documents (cross-sha loser cleanup)."""
        escaped_sha = source_sha256.replace("'", "''")
        return self._delete_where(
            f"attachment_id eq {int(attachment_id)} and source_sha256 eq '{escaped_sha}'",
            code="ATTACHMENT_SEARCH_PRUNE_FAILED",
        )
