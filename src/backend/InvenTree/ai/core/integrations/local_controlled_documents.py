"""Offline retrieval of approved, exact-machine controlled PDF sources."""

from __future__ import annotations

import hashlib
import io
import re
from itertools import islice
from pathlib import Path
from typing import Any

_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_MAX_PAGES = 200
_MAX_DOCUMENTS = 20
_MAX_PAGE_CHARS = 40000
_EXCERPT_CHARS = 1600
_STOP_WORDS = frozenset({
    "about",
    "and",
    "are",
    "can",
    "for",
    "from",
    "how",
    "into",
    "its",
    "manual",
    "of",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "with",
})


def local_retrieval_enabled(settings: Any) -> bool:
    """Require the explicit development-only local provider."""
    return (
        getattr(settings, "ai_provider", "azure") == "local"
        and getattr(settings, "env", "") == "development"
    )


def _authorized_machine(user, machine_id: int):
    from ai.core.integrations.projection_gate import recall_allowed
    from ai.core.integrations.retrieval_authority import fresh_actor, fresh_role
    from assets.models import AssetMachine
    from tasks.scope import ScopeError, require_machine_scope

    actor = fresh_actor(user)
    if actor is None or not fresh_role(actor, "work_order") or not recall_allowed("controlled"):
        return None
    machine = AssetMachine.objects.filter(pk=machine_id, active=True).first()
    if machine is None:
        return None
    try:
        require_machine_scope(actor, machine)
    except ScopeError:
        return None
    return machine


def _source_bytes(document, root: Path) -> bytes | None:
    """Read one trusted-root PDF and verify the actual bytes before parsing."""
    location = str(document.source_location or "")
    path = Path(location)
    if (
        not location
        or "://" in location
        or not path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".pdf"
        or path.name != document.source_filename
        or not re.fullmatch(r"[0-9a-f]{64}", document.source_sha256 or "")
    ):
        return None
    try:
        trusted_root = Path(root).resolve(strict=True)
        source = path.resolve(strict=True)
        source.relative_to(trusted_root)
        if not source.is_file() or source.stat().st_size > _MAX_DOCUMENT_BYTES:
            return None
        with source.open("rb") as stream:
            content = stream.read(_MAX_DOCUMENT_BYTES + 1)
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        len(content) > _MAX_DOCUMENT_BYTES
        or not content.startswith(b"%PDF-")
        or hashlib.sha256(content).hexdigest() != document.source_sha256
    ):
        return None
    return content


def _page_hits(content: bytes, terms: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """Rank bounded literal passages, excluding indexes and distant header hits."""
    from pypdf import PdfReader

    hits = []
    terms = tuple(
        dict.fromkeys(term.lower() for term in terms if term and term.lower() not in _STOP_WORDS)
    )[:30]
    if not terms:
        return hits
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted or len(reader.pages) > _MAX_PAGES:
            return []
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "")[:_MAX_PAGE_CHARS]
            # A contents entry identifies a section; it does not supply the
            # actual maintenance instructions to support a procedural claim.
            if (
                re.search(r"\btable\s+of\s+contents\b", page_text, re.I)
                or len(re.findall(r"\.{3,}", page_text)) >= 6
            ):
                continue
            pages.append((page_number, page_text))
        # Downweight common running-header terms without deleting source text.
        weights = {
            term: max(1, len(pages) - sum(term in text.lower() for _, text in pages))
            for term in terms
        }
        for page_number, page_text in pages:
            lowered = page_text.lower()
            positions = sorted({
                match.start()
                for term in terms
                for match in islice(re.finditer(re.escape(term), lowered), 8)
            })
            best = None
            for position in positions:
                start = max(0, position - _EXCERPT_CHARS // 3)
                excerpt = page_text[start : start + _EXCERPT_CHARS].strip()
                # Score only words visible in this exact excerpt, rather than
                # scattered matches elsewhere on the full PDF page.
                score = sum(weights[term] for term in terms if term in excerpt.lower())
                if best is None or score > best[0]:
                    best = (score, page_number, excerpt)
            if best is not None:
                hits.append(best)
    except Exception:
        return []
    return hits


def search_local_manuals(
    *, user, machine_id: int, query: str, limit: int = 5, document_class: str | None = None
) -> list[dict[str, Any]]:
    """Return real PDF excerpts only after fresh scope and registry checks."""
    from ai.core.config import get_settings
    from aichat.models import ApplicabilityKind, ControlledDocument, ControlledDocumentState
    from aichat.services.applicability import verified_claims_for_targets

    settings = get_settings()
    if (
        not local_retrieval_enabled(settings)
        or type(machine_id) is not int
        or machine_id < 1
        or type(limit) is not int
        or not 1 <= limit <= 10
        or not isinstance(query, str)
        or not query.strip()
        or len(query) > 4000
        or not settings.single_site_policy_key
        or _authorized_machine(user, machine_id) is None
    ):
        return []
    terms = tuple(
        dict.fromkeys(
            term for term in re.findall(r"[a-z0-9]{3,}", query.lower()) if term not in _STOP_WORDS
        )
    )[:30]
    if not terms:
        return []
    scope_key = settings.single_site_policy_key
    claims = verified_claims_for_targets(machine_ids=(machine_id,)).filter(
        kind=ApplicabilityKind.EXACT_MACHINE, target_machine_id=machine_id
    )
    documents = ControlledDocument.objects.filter(
        pk__in=claims.values("document_id"),
        scope_key=scope_key,
        scope_hash=hashlib.sha256(scope_key.encode("utf-8")).hexdigest(),
        access_class="maintenance_authorized",
        state=ControlledDocumentState.INDEXED,
        is_current=True,
        approved_by__isnull=False,
    )
    if document_class:
        documents = documents.filter(document_class=document_class)
    results = []
    for document in documents.order_by("pk")[:_MAX_DOCUMENTS]:
        content = _source_bytes(document, settings.controlled_documents_root)
        if content is None:
            continue
        for score, page, excerpt in _page_hits(content, terms):
            results.append({
                "document_pk": document.pk,
                "selection_id": str(document.selection_id),
                "document_id": document.document_id,
                "revision": document.revision,
                "title": document.title,
                "source_filename": document.source_filename,
                "source_sha256": document.source_sha256,
                "page": page,
                "excerpt": excerpt,
                "score": score,
                "as_of": document.updated_at,
                "asset_id": str(document.asset_id or ""),
                "_source_snapshot": (
                    document.pk,
                    document.source_sha256,
                    document.document_id,
                    document.revision,
                    document.title,
                    document.source_filename,
                    document.source_location,
                    document.updated_at,
                    document.asset_id,
                    document.selection_id,
                ),
            })
    if _authorized_machine(user, machine_id) is None:
        return []
    document_ids = {result["document_pk"] for result in results}
    live_documents = set(
        documents.filter(pk__in=document_ids).values_list(
            "pk",
            "source_sha256",
            "document_id",
            "revision",
            "title",
            "source_filename",
            "source_location",
            "updated_at",
            "asset_id",
            "selection_id",
        )
    )
    results = [result for result in results if result.pop("_source_snapshot") in live_documents]
    results.sort(key=lambda result: (-result["score"], result["document_pk"], result["page"]))
    return results[:limit]
