"""Client-scoped Azure AI Search retrieval over the attachment-RAG corpus (R2).

The retrieval twin of ``attachment_search`` (the write-side projection) and
the attachment-corpus sibling of ``controlled_document_corpus``. It answers
"what do the uploaded documents say about X?" over auto-ingested part and
machine attachments -- an *uncontrolled* trust tier, structurally separate
from the governed manuals corpus:

* ``scope_key eq '<AIMMS_SINGLE_SITE_POLICY_KEY>'`` -- the deployment's own
  site key, never caller input. An empty key refuses rather than widening.
* ``is_current eq true`` -- superseded revisions never answer.
* ``access_class eq 'attachment_uploaded'`` -- pinned; auto-ingested content
  must never satisfy a ``maintenance_authorized`` filter, and vice versa.
* ``search.in(model_type, ...)`` -- restricted to the arms the acting user's
  roles grant (``part:view`` -> part docs, ``work_order:view`` -> machine
  docs), so a bare query can never return the other arm's documents.
* ``client_codes/any(...)`` -- the actor's resolved client grants, derived
  server-side via ``tasks.scope.client_codes_for_actor``. Fail-closed: an
  unresolved scope or an empty grant set refuses, indistinguishable from an
  empty corpus (denial == nonexistence). Codes stay in the filter expression
  only -- never in the payload, citations or errors.

Part and machine narrowing are resolved server-side (the model supplies a
name, never a ``part_id``/``asset_id`` literal) and degrade to the broad
query when unresolved -- narrowing is a precision feature, not the
authorization boundary. Excerpts are wrapped in the ``[UNTRUSTED-CONTENT]``
fence: uploaded documents are attacker-writable by anyone holding the
upload roles.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any

from ai.core.integrations.controlled_document_search import (
    EmbeddingClient,
    SearchClient,
    _odata_literal,
)
from ai.core.integrations.rag_profile import BASELINE_PROFILE, text_embedding_profile
from ai.core.integrations.search_query import semantic_hybrid_kwargs
from ai.core.maf_compat import ai_function
from ai.core.tools.diagnostics import fence_untrusted_content
from asgiref.sync import sync_to_async
from pydantic import Field

logger = logging.getLogger(__name__)

#: The single access class this tool may read. Server-side constant; the
#: governed corpus's classes are deliberately not readable here.
_ACCESS_CLASS = "attachment_uploaded"

#: Owner arms and the RBAC role that grants each. The filter always carries a
#: ``model_type`` clause restricted to granted arms.
_ARM_ROLES = (
    ("part", "part"),
    ("assetmachine", "work_order"),
)

#: Document types accepted as a narrowing argument -- the ingest router's own
#: classification vocabulary (``attachment_ingestion.classify_doc_type``).
#: Anything else is refused (the argument comes from the model).
_DOC_TYPE_ALLOWLIST = (
    "manual",
    "catalogue",
    "datasheet",
    "drawing",
    "tech_lit",
    "other",
)

#: Metadata is selected for final native reauthorization, then omitted from
#: model-visible chunks. Vectors are never selected.
_SELECT_FIELDS = [
    "client_codes",
    "scope_key",
    "source_sha256",
    "embedding_profile",
    "is_current",
    "id",
    "attachment_id",
    "model_type",
    "model_id",
    "part_id",
    "part_name",
    "asset_id",
    "machine_name",
    "doc_type",
    "source_file_name",
    "section_path",
    "heading_1",
    "heading_2",
    "heading_3",
    "page_number",
    "content",
    "as_of",
    "access_class",
]

#: Excerpts are truncated to this length BEFORE hashing and fencing, matching
#: the controlled corpus's bound.
_EXCERPT_MAX_CHARS = 8000


class AttachmentRetrievalError(Exception):
    """A stable attachment-corpus retrieval failure with a value-free code."""

    code = "ATTACHMENT_SEARCH_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def attachment_corpus_filter(
    *,
    scope_key: str,
    client_codes: frozenset[str] | set[str],
    model_types: tuple[str, ...] | list[str],
    part_id: int | None = None,
    asset_id: str | None = None,
    doc_type: str | None = None,
    attachment_id: int | None = None,
    model_id: int | None = None,
    scope_asset_ids: tuple[str, ...] | None = None,
    embedding_profile: str = BASELINE_PROFILE,
) -> str:
    """Build the non-negotiable attachment filter from server-side values only.

    Clause order is fixed (tests pin the exact string). ``client_codes`` and
    ``model_types`` are mandatory: an empty set refuses rather than widening,
    mirroring ``machine_scope_filter``'s ``pk__in=[]`` reasoning -- a missing
    clause would read as "everyone's".

    ``scope_asset_ids`` (S5 enforce) is a narrowing FLOOR from the analysis
    scope's server-resolved serials: machine-stamped chunks must belong to a
    scoped machine, while unstamped chunks (part documents, WO media whose
    owner has no serial) stay reachable — degrade steps may drop narrowing
    precision but can never step outside the scoped assets.

    ``attachment_id``/``model_id`` are the exact-target selector (AT-F1):
    positive native integers only, bound server-side from the Attachment and
    its owner. bool/zero/negative/fraction/string values are refused, never
    coerced or interpolated.
    """
    if not scope_key:
        raise AttachmentRetrievalError(
            "Attachment-corpus site scope is not configured",
            code="ATTACHMENT_SCOPE_UNCONFIGURED",
        )
    if not model_types:
        raise AttachmentRetrievalError(
            "No document arm is authorized", code="ATTACHMENT_SCOPE_UNRESOLVED"
        )
    if not client_codes:
        raise AttachmentRetrievalError(
            "Actor scope names no client", code="ATTACHMENT_SCOPE_UNRESOLVED"
        )
    for code in client_codes:
        # Client.code is an immutable SlugField, so these characters are
        # unreachable -- but the filter builder must not trust that.
        if "," in code or "'" in code:
            raise AttachmentRetrievalError(
                "Actor client scope is invalid", code="ATTACHMENT_SCOPE_INVALID"
            )

    if attachment_id is not None and (type(attachment_id) is not int or attachment_id < 1):
        raise AttachmentRetrievalError("attachment_id is invalid", code="ATTACHMENT_TARGET_INVALID")
    if model_id is not None and (type(model_id) is not int or model_id < 1):
        raise AttachmentRetrievalError("model_id is invalid", code="ATTACHMENT_TARGET_INVALID")

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
    if not embedding_profile:
        raise AttachmentRetrievalError(
            "Embedding profile is not configured", code="ATTACHMENT_EMBEDDING_PROFILE_INVALID"
        )
    if embedding_profile == BASELINE_PROFILE:
        clauses.append(
            "(embedding_profile eq 'v1' or embedding_profile eq null or embedding_profile eq '')"
        )
    else:
        clauses.append(f"embedding_profile eq '{_odata_literal(embedding_profile)}'")
    if attachment_id is not None:
        clauses.append(f"attachment_id eq {attachment_id}")
    if model_id is not None:
        clauses.append(f"model_id eq {model_id}")
    if part_id is not None:
        clauses.append(f"part_id eq {int(part_id)}")
    if asset_id:
        # Other-owner EXCLUSION, not owner selection: part documents carry no
        # asset stamp, and a machine hint ("the HX-200 gasket datasheet")
        # must never hide the part-owned datasheet that HAS the answer — a
        # bare equality clause did exactly that (live golden, 2026-09-01:
        # three datasheet items abstained under machine narrowing). Same
        # shape as the S5 serial floor below.
        clauses.append(
            f"(asset_id eq '' or asset_id eq null or asset_id eq '{_odata_literal(asset_id)}')"
        )
    if doc_type:
        clauses.append(f"doc_type eq '{_odata_literal(doc_type)}'")
    if scope_asset_ids is not None:
        joined_serials = ",".join(_odata_literal(item) for item in sorted(scope_asset_ids))
        if joined_serials:
            clauses.append(
                f"(asset_id eq '' or asset_id eq null or "
                f"search.in(asset_id, '{joined_serials}', ','))"
            )
        else:
            # Serial-less explicit scope: only unstamped chunks are provably
            # inside it; machine-stamped chunks cannot be verified in-scope.
            clauses.append("(asset_id eq '' or asset_id eq null)")
    return " and ".join(clauses)


def _granted_arms(user) -> tuple[str, ...]:
    """Return the ``model_type`` arms the user's roles grant, in filter order."""
    if getattr(user, "is_superuser", False):
        return tuple(model_type for model_type, _role in _ARM_ROLES)

    from users.permissions import check_user_role

    return tuple(
        model_type for model_type, role in _ARM_ROLES if check_user_role(user, role, "view")
    )


def _display_title(row: dict[str, Any]) -> str:
    """Friendly document name for a citation, from the source file name."""
    stem = str(row.get("source_file_name") or "document")
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return stem.replace("_", " ").strip() or "document"


def search_corpus_attachments(
    *,
    user,
    query: str,
    part: str | None = None,
    machine: str | None = None,
    doc_type: str | None = None,
    top_k: int = 5,
    search_client: SearchClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    machine_resolver=None,
    part_resolver=None,
    scope_asset_ids: tuple[str, ...] | None = None,
    attachment_id: int | None = None,
) -> dict[str, Any]:
    """Search current uploaded attachment documents and cite fenced excerpts.

    ``machine_resolver``/``part_resolver`` exist for tests; production
    resolution goes through ``assets.ai_read.machines_in_scope`` and the part
    catalogue under the acting user. Every refusal happens before any network
    call -- flag, validation, site scope, arm grants and client codes are all
    checked before the query is embedded.

    ``attachment_id`` is the exact-target selector (AT-F1): the current
    Attachment, its active latest serving-index ingest and its native owner
    authorization are bound BEFORE any inferred hint resolver runs or any
    provider client is constructed. ``part``/``machine``/``doc_type`` are
    then ignored entirely -- they can neither narrow nor hide the selected
    source -- and an unavailable target refuses with the typed, value-free
    ``ATTACHMENT_TARGET_UNAVAILABLE`` instead of degrading to a broad search.
    """
    from ai.core.config import get_settings

    settings = get_settings()
    if not settings.feature_attachment_rag_retrieval:
        raise AttachmentRetrievalError(
            "Attachment retrieval is not enabled",
            code="ATTACHMENT_RETRIEVAL_DISABLED",
        )
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise AttachmentRetrievalError("query is invalid", code="ATTACHMENT_QUERY_INVALID")
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise AttachmentRetrievalError("top_k is invalid", code="ATTACHMENT_QUERY_INVALID")
    top_k = max(1, min(top_k, 5))
    if attachment_id is None and doc_type is not None and doc_type not in _DOC_TYPE_ALLOWLIST:
        raise AttachmentRetrievalError(
            "doc_type is not recognised", code="ATTACHMENT_QUERY_INVALID"
        )
    ignored_hints: tuple[str, ...] = ()
    if attachment_id is not None:
        ignored_hints = tuple(
            name
            for name, value in (("part", part), ("machine", machine), ("doc_type", doc_type))
            if value
        )
    if attachment_id is not None and (type(attachment_id) is not int or attachment_id < 1):
        _record_target_outcome(
            requested_selector="attachment_id",
            effective_filter="exact",
            returned_count=0,
            refusal_status="invalid_selector",
            ignored_hints=ignored_hints,
        )
        raise AttachmentRetrievalError("attachment_id is invalid", code="ATTACHMENT_TARGET_INVALID")

    # AT-F1: bind the EXACT target (current Attachment, active latest
    # serving-index ingest, native owner authorization) before inferred
    # hint resolvers or any client construction/provider call.
    exact_target: dict[str, Any] | None = None
    if attachment_id is not None:
        from ai.core.integrations.retrieval_authority import exact_attachment_target

        exact_target = exact_attachment_target(user, attachment_id)
        if exact_target is None:
            # Missing, deleted, unindexed, superseded, wrong-index, foreign
            # owner and revoked access all refuse identically: one typed,
            # value-free code, no candidate list, no provider work.
            _record_target_outcome(
                requested_selector="attachment_id",
                effective_filter="exact",
                returned_count=0,
                refusal_status="target_unavailable",
                requested_attachment_id=attachment_id,
                ignored_hints=ignored_hints,
            )
            raise AttachmentRetrievalError(
                "Exact attachment target is unavailable",
                code="ATTACHMENT_TARGET_UNAVAILABLE",
            )

    if part and machine and exact_target is None:
        # The two narrowings are structurally exclusive: part docs carry no
        # asset_id and machine docs no part_id, so ANDing both clauses can
        # only ever return zero hits — a false "no documents" to the model.
        # Refuse with a message the model can act on instead.
        raise AttachmentRetrievalError(
            "part and machine are alternatives; pass one, not both",
            code="ATTACHMENT_QUERY_INVALID",
        )

    scope_key = settings.single_site_policy_key or ""
    if not scope_key:
        # Checked before any network call: a blank policy key must refuse
        # without embedding the query or touching the search service.
        raise AttachmentRetrievalError(
            "Attachment-corpus site scope is not configured",
            code="ATTACHMENT_SCOPE_UNCONFIGURED",
        )

    if exact_target is not None:
        # The binding already required the reader role (work_order for every
        # source, part additionally for part-owned documents), so the arm is
        # exactly the bound owner's own model_type.
        model_types = (str(exact_target["model_type"]),)
    else:
        model_types = _granted_arms(user)
    if not model_types:
        raise AttachmentRetrievalError(
            "No document arm is authorized", code="ATTACHMENT_SCOPE_UNRESOLVED"
        )

    from tasks.scope import ScopeError, client_codes_for_actor

    try:
        client_codes = client_codes_for_actor(user)
    except ScopeError as exc:
        # Denial is indistinguishable from an empty corpus downstream; the
        # code (never the client identity) is the whole diagnostic.
        raise AttachmentRetrievalError(
            "Actor scope is unresolved", code="ATTACHMENT_SCOPE_UNRESOLVED"
        ) from exc

    part_id: int | None = None
    part_filter = "not_requested"
    part_candidates: list[dict[str, Any]] = []
    # Narrowing is arm-gated: a hint for an arm the actor's roles do not
    # grant reports not_applied WITHOUT running its resolver — the candidate
    # payload (part names/IPNs, machine names/serials) would leak exactly the
    # metadata the missing role protects. The arm's documents are already
    # excluded by the model_type clause, so degrading to the broad
    # (granted-arm) search stays honest.
    if part and exact_target is None:
        part_filter = "not_applied"
    if part and exact_target is None and "part" in model_types:
        resolver = part_resolver if part_resolver is not None else _resolve_parts
        part_candidates = resolver(user, str(part)[:100])
        if len(part_candidates) == 1:
            part_id = int(part_candidates[0]["part_id"])
            part_filter = "applied"
        elif len(part_candidates) > 1:
            _record_search_outcome(
                user=user,
                query=query,
                hit_count=0,
                top_score=None,
                machine_filter="not_requested",
                document_class=doc_type,
                scope_key=scope_key,
                corpus="attachment",
                part_filter="ambiguous",
            )
            return {
                "chunks": [],
                "returned_count": 0,
                "machine_filter": "not_requested",
                "part_filter": "ambiguous",
                "part_candidates": part_candidates,
            }

    asset_id: str | None = None
    machine_filter = "not_requested"
    if machine and exact_target is None:
        machine_filter = "not_applied"
    if machine and exact_target is None and "assetmachine" in model_types:
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
                document_class=doc_type,
                scope_key=scope_key,
                corpus="attachment",
                part_filter=part_filter,
            )
            _propose_machine_question(candidates)
            return {
                "chunks": [],
                "returned_count": 0,
                "machine_filter": "ambiguous",
                "machine_candidates": candidates,
                "part_filter": part_filter,
            }

    if exact_target is not None:
        from ai.core.analysis.scope_context import current_turn_scope as _exact_scope

        exact_scope = _exact_scope()
        conflict = False
        if (
            exact_scope is not None
            and exact_scope.explicit
            and exact_scope.enforce
            and exact_target["model_type"] == "assetmachine"
        ):
            scoped_ids = set(getattr(exact_scope, "machine_ids", ()) or ())
            scoped_serials = set(exact_scope.machine_serials)
            conflict = bool(scoped_ids and exact_target["model_id"] not in scoped_ids) or bool(
                scoped_serials and exact_target.get("owner_serial") not in scoped_serials
            )
        if (
            not conflict
            and isinstance(scope_asset_ids, tuple)
            and exact_target.get("owner_serial")
            and exact_target["owner_serial"] not in set(scope_asset_ids)
        ):
            conflict = True
        if conflict:
            # The trusted structured scope is a FLOOR: a conflicting exact
            # target refuses, it never redirects to another owner.
            _record_target_outcome(
                requested_selector="attachment_id",
                effective_filter="exact",
                returned_count=0,
                refusal_status="scope_conflict",
                requested_attachment_id=attachment_id,
                effective_target=exact_target,
                ignored_hints=ignored_hints,
            )
            raise AttachmentRetrievalError(
                "Exact attachment target is unavailable",
                code="ATTACHMENT_TARGET_UNAVAILABLE",
            )

    from ai.core.integrations.projection_gate import recall_allowed

    if not recall_allowed("attachment"):
        raise AttachmentRetrievalError(
            "Projection recall is temporarily unavailable", code="ATTACHMENT_PROJECTION_HELD"
        )

    if embedding_client is None:
        embedding_client = _default_embedding_client()
    if search_client is None:
        search_client = _default_search_client()

    active_profile = text_embedding_profile(settings)
    try:
        vector = embedding_client.embed_query(query)
    except Exception as exc:
        raise AttachmentRetrievalError(
            "Attachment query embedding failed",
            code="ATTACHMENT_QUERY_EMBEDDING_FAILED",
        ) from exc

    # S5 (WP-A3): an ENFORCED explicit analysis scope adds the serial floor —
    # degrades below may drop narrowing precision (drop asset/doc-type) but
    # the floor rides every run, so they can never step outside the scoped
    # assets. Shadow observes; unscoped turns are untouched.
    from ai.core.analysis.scope_context import current_turn_scope as _current_scope

    scope_context = _current_scope()
    if scope_asset_ids is not None:
        if (
            not isinstance(scope_asset_ids, tuple)
            or len(scope_asset_ids) > 100
            or any(
                not isinstance(value, str) or not value or len(value) > 255
                for value in scope_asset_ids
            )
        ):
            raise AttachmentRetrievalError(
                "Invalid server asset scope", code="ATTACHMENT_SCOPE_UNRESOLVED"
            )
        scope_asset_ids = tuple(sorted(set(scope_asset_ids)))
    if scope_context is not None and scope_context.explicit and scope_context.enforce:
        current_serials = set(scope_context.machine_serials)
        scope_asset_ids = tuple(
            sorted(
                current_serials
                if scope_asset_ids is None
                else current_serials.intersection(scope_asset_ids)
            )
        )

    def _run(type_filter: str | None):
        filter_expression = attachment_corpus_filter(
            scope_key=scope_key,
            client_codes=client_codes,
            model_types=model_types,
            part_id=part_id,
            asset_id=asset_id,
            doc_type=type_filter,
            attachment_id=exact_target["attachment_id"] if exact_target is not None else None,
            model_id=exact_target["model_id"] if exact_target is not None else None,
            scope_asset_ids=scope_asset_ids,
            embedding_profile=active_profile,
        )
        try:
            search_kwargs = semantic_hybrid_kwargs(
                query=query,
                vector=vector,
                vector_field="text_vector",
                filter_expression=filter_expression,
                select=_SELECT_FIELDS,
                top=top_k,
            )
        except ValueError as exc:
            # The builder's wildcard/blank guard, mapped to the typed refusal.
            raise AttachmentRetrievalError(
                "query is invalid", code="ATTACHMENT_QUERY_INVALID"
            ) from exc
        try:
            return list(search_client.search(**search_kwargs))
        except AttachmentRetrievalError:
            raise
        except Exception as exc:
            raise AttachmentRetrievalError(
                "Attachment-corpus Search query failed",
                code="ATTACHMENT_SEARCH_FAILED",
            ) from exc

    rows = _run(None if exact_target is not None else doc_type)
    if not rows and doc_type and exact_target is None:
        # Type narrowing is a precision hint, exactly like part and machine
        # narrowing -- never the reason a corpus that HAS the answer reports
        # none. Degrade to the un-narrowed result.
        rows = _run(None)
    if exact_target is not None:
        # AT-F1 R05: the provider call is not atomic with native authority.
        # Re-derive the binding against FRESH state; a mid-call grant
        # revocation, ownership change or source change refuses with the SAME
        # typed, value-free unavailability -- never an empty result relabeled
        # 'no_passages' for a now-inaccessible target.
        from ai.core.integrations.retrieval_authority import exact_target_binding_current

        if not exact_target_binding_current(user, exact_target):
            _record_target_outcome(
                requested_selector="attachment_id",
                effective_filter="exact",
                returned_count=0,
                refusal_status="target_unavailable",
                requested_attachment_id=attachment_id,
                ignored_hints=ignored_hints,
            )
            raise AttachmentRetrievalError(
                "Exact attachment target is unavailable",
                code="ATTACHMENT_TARGET_UNAVAILABLE",
            )

    from ai.core.integrations.retrieval_authority import attachment_rows

    compatible_profiles = (
        (BASELINE_PROFILE, None, "") if active_profile == BASELINE_PROFILE else (active_profile,)
    )
    rows = [row for row in rows if row.get("embedding_profile") in compatible_profiles]
    if exact_target is not None:
        # AT-R09/R08: a Search client that ignores its exact filter must not
        # smuggle another attachment's rows into model context. Every row is
        # checked against the bound target before native reauthorization and
        # once more at exposure time (the provider call is not atomic with
        # actor/role/grant/ownership state).
        from ai.core.integrations.retrieval_authority import exact_target_row_reason

        rows = [
            row for row in rows if not exact_target_row_reason(row, target=exact_target, user=user)
        ]
    rows = attachment_rows(rows, user=user, corpus="attachment")
    if exact_target is not None:
        rows = [
            row for row in rows if not exact_target_row_reason(row, target=exact_target, user=user)
        ]
    chunks: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("content") or "")[:_EXCERPT_MAX_CHARS]
        chunks.append({
            # Hash over the raw truncation (pre-fence) so the grounding
            # auditor's excerpt identity is independent of fence markers.
            "excerpt": fence_untrusted_content(text),
            "score": row.get("@search.score", 0),
            "citation": {
                # The free-text citation fields — display title, file name,
                # section path — are authored by the UPLOADED document (its
                # headings) or its uploader (the filename), so they are the
                # same attacker-writable tier as the excerpt and get the same
                # fence. Server-stamped coordinates (ids, tier, serial, dates)
                # stay raw.
                "document": fence_untrusted_content(_display_title(row)[:255]),
                "source_file_name": fence_untrusted_content(
                    str(row.get("source_file_name") or "")[:255]
                ),
                "attachment_id": int(row.get("attachment_id") or 0),
                "source_sha256": str(row.get("source_sha256") or ""),
                "model_type": str(row.get("model_type") or ""),
                "model_id": int(row.get("model_id") or 0),
                "page_number": row.get("page_number"),
                "section_path": fence_untrusted_content(str(row.get("section_path") or "")[:512]),
                "chunk_id": str(row.get("id") or ""),
                "as_of": str(row.get("as_of") or ""),
                # Uploaded-document tier, surfaced so the model and the
                # grounding rail can distinguish it from a controlled manual.
                "access_class": str(row.get("access_class") or _ACCESS_CLASS),
                # The indexed machine identity (serial) so downstream
                # grounding can fence a citation from the WRONG machine's
                # documents. Empty for part documents.
                "asset_id": str(row.get("asset_id") or ""),
                "excerpt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
        })
    scope_fields: dict[str, Any] = {}
    if scope_context is not None:
        scope_fields = {
            "scope_hash": scope_context.scope_hash,
            "scope_mode": scope_context.mode,
            "scope_enforced": scope_asset_ids is not None,
        }
        if scope_context.explicit and not scope_context.enforce:
            scope_fields["out_of_scope_hits"] = sum(
                1
                for chunk in chunks
                if chunk["citation"]["asset_id"]
                and chunk["citation"]["asset_id"] not in scope_context.machine_serials
            )
    _record_search_outcome(
        user=user,
        query=query,
        hit_count=len(chunks),
        top_score=max((float(row["score"] or 0) for row in chunks), default=None),
        machine_filter=machine_filter,
        document_class=doc_type,
        scope_key=scope_key,
        corpus="attachment",
        part_filter=part_filter,
        **scope_fields,
    )
    # S5 §7.4: semantic retrieval never evaluates a population (see
    # controlled_document_corpus for the vocabulary rationale).
    from ai.core.contracts.retrieval import (
        NO_RELEVANT_PASSAGE,
        build_envelope,
        record_envelope,
    )

    envelope = build_envelope(
        source_class="asset_attachment",
        population_type="document_chunks",
        operation="semantic_search",
        filters={
            "machine_filter": machine_filter,
            "part_filter": part_filter,
            "doc_type": doc_type or None,
            **({"attachment_filter": "exact"} if exact_target is not None else {}),
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
            "attached": True,
            "indexed": True,
            "searchable_now": True,
            "current": True,
        },
        warnings=() if chunks else (NO_RELEVANT_PASSAGE,),
    )
    if exact_target is not None:
        _record_target_outcome(
            requested_selector="attachment_id",
            effective_filter="exact",
            returned_count=len(chunks),
            refusal_status="ok" if chunks else "no_passages",
            requested_attachment_id=attachment_id,
            effective_target=exact_target,
            ignored_hints=ignored_hints,
        )
        record_envelope(
            "search_attachment_docs",
            envelope,
            target_selector="attachment_id",
            target_filter="exact",
            returned_count=len(chunks),
            refusal_status="ok" if chunks else "no_passages",
        )
    else:
        record_envelope("search_attachment_docs", envelope)
    result = {
        "chunks": chunks,
        "returned_count": len(chunks),
        "machine_filter": machine_filter,
        "part_filter": part_filter,
        "retrieval": envelope,
    }
    if exact_target is not None:
        result["attachment_filter"] = "exact"
    return result


def _resolve_parts(actor, name: str) -> list[dict[str, Any]]:
    """Resolve a part name/IPN to candidates; role scope is the boundary.

    Only reachable when the caller's granted arms include ``part`` — the
    caller gates on ``"part" in model_types`` before invoking any resolver,
    so ``part:view`` (or superuser) is guaranteed here. Part visibility is
    role-based while tenant scoping rides the ``client_codes`` clause, so no
    per-row scope walk is needed. A unique exact name match wins outright.
    """
    from django.db.models import Q as _Q
    from part.models import Part

    exact = list(Part.objects.filter(name__iexact=name)[:2])
    if len(exact) == 1:
        rows = exact
    else:
        rows = list(
            Part.objects.filter(_Q(name__icontains=name) | _Q(IPN__icontains=name)).order_by(
                "name"
            )[:3]
        )
    return [{"part_id": row.pk, "name": row.name, "ipn": row.IPN or ""} for row in rows]


def _record_search_outcome(**kwargs) -> None:
    """Write the A7 ledger row; the writer itself is fail-soft (S16)."""
    from aichat.services.retrieval_misses import record_search

    record_search(**kwargs)


def _record_target_outcome(
    *,
    requested_selector: str,
    effective_filter: str,
    returned_count: int,
    refusal_status: str,
    requested_attachment_id: int | None = None,
    effective_target: dict[str, Any] | None = None,
    ignored_hints: tuple[str, ...] = (),
) -> None:
    """Bounded, turn-correlated exact-target diagnostics (AT-F1).

    Enums, counts and bounded numeric identities only: never query text,
    source text, filenames, vectors, secrets or ACL/client codes. The
    requested id echoes the caller's own validated input (no existence
    disclosure); effective owner identity is recorded ONLY after the native
    binding authorized it, and invalid raw values are never echoed. Also
    records the ignored hint FLAGS (never hint values) and the refusal /
    no-passages status. Correlates to the active turn through
    ``current_correlation`` and the capture ledger; fail-soft exactly like
    every other recorder here.
    """
    try:
        from ai.core.correlation import current_correlation
        from ai.core.tools.capture_ledger import record_retrieval_meta

        meta: dict[str, Any] = {
            "correlation_id": current_correlation(),
            "target_selector": requested_selector,
            "target_filter": effective_filter,
            "returned_count": int(returned_count),
            "refusal_status": refusal_status,
            "ignored_hints": list(ignored_hints),
        }
        if type(requested_attachment_id) is int and requested_attachment_id > 0:
            meta["requested_attachment_id"] = requested_attachment_id
        if effective_target:
            meta["effective_attachment_id"] = int(effective_target["attachment_id"])
            meta["effective_owner_type"] = str(effective_target["model_type"])
            meta["effective_owner_id"] = int(effective_target["model_id"])
        record_retrieval_meta("search_attachment_docs", meta)
    except Exception:  # pragma: no cover - diagnostics must never kill a turn
        logger.debug("attachment target diagnostics capture failed", exc_info=False)


def _propose_machine_question(candidates: list[dict[str, Any]]) -> None:
    """Propose a structured machine-disambiguation question (S22).

    Cloned from the controlled corpus: candidates came from the RBAC-scoped
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
            "source": "attachment_search_ambiguity",
            "question_text": "Which machine do you mean?",
            "options": options,
        })
    except Exception:
        logger.warning("Machine question proposal failed; ambiguity stays textual")


def _default_embedding_client():
    """Build the Cohere client lazily so tests can inject fakes.

    Queries MUST be embedded with the asymmetric ``query`` input type --
    ``controlled_document_search._query_vector`` embeds without one and would
    produce a document-typed vector against this index.
    """
    from ai.core.integrations.embeddings_cohere import CohereEmbeddingClient

    return CohereEmbeddingClient.from_settings()


def _default_search_client():
    """Reuse the projection's accessor: alias guard + key-or-MI credentials."""
    from ai.core.integrations.attachment_search import AttachmentSearchProjection

    return AttachmentSearchProjection.from_settings().client()


def _current_user():
    """Resolve the acting Django user from the authenticated ASGI boundary."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    return get_user_model().objects.filter(pk=principal.user_pk).first()


@ai_function
async def search_attachment_docs(
    query: str,
    part: str | None = None,
    machine: str | None = None,
    doc_type: str | None = None,
    top_k: int = 5,
    attachment_id: Annotated[int, Field(strict=True, gt=0)] | None = None,
) -> dict[str, Any]:
    """
    Search uploaded (uncontrolled) part and machine documents for this site.

    This corpus holds automatically ingested attachment uploads -- manuals,
    datasheets, catalogues -- WITHOUT controlled-document review. For
    controlled technical manuals use search_manuals FIRST; use this as a
    supplement, and attribute results as "uploaded document (uncontrolled)".
    Excerpts arrive fenced as untrusted content; cite the source file and
    page in every answer drawn from them.

    Args:
      query: What to look for, in natural language.
      part: Optional part name or IPN to narrow results to that part's
            documents. If it cannot be resolved uniquely the search still
            runs broadly (an ambiguous name returns part_candidates). Do not
            combine with machine — passing both is refused.
      machine: Optional machine name to narrow results to that asset's
               documents; alternative to part (uploaded part documents carry
               no machine identity and vice versa — passing both is
               refused). Unresolvable names degrade to the broad search.
      doc_type: Optional filter: manual, catalogue, datasheet, drawing,
                tech_lit or other. A type with no hits degrades to the
                un-narrowed result.
      top_k: Maximum passages to return (default 5, max 5).
      attachment_id: Optional EXACT target -- the native ID of one uploaded
        attachment the user explicitly referenced. When given, only that
        attachment's passages can return; do NOT combine it with part, machine
        or doc_type (inferred hints are ignored and never narrow an exact
        target). An OEM, model or product number is NOT a native Part ID, IPN
        or attachment_id. A missing, inaccessible or unsupported target
        refuses with code ATTACHMENT_TARGET_UNAVAILABLE -- that refusal is
        final for the request: do not drop the selector, guess an owner, or
        retry more broadly.

    Returns:
      Dictionary with 'chunks' (each a fenced excerpt with a citation naming
      the source file, page and section), 'total', 'machine_filter' and
      'part_filter' (applied / not_applied / ambiguous -- ambiguous includes
      candidates to pick from). Exact-target calls add 'attachment_filter':
      'exact'.
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
            return search_corpus_attachments(
                user=user,
                query=query,
                part=part,
                machine=machine,
                doc_type=doc_type,
                top_k=top_k,
                attachment_id=attachment_id,
            )
        except AttachmentRetrievalError as exc:
            from ai.core.faults import log_fault

            log_fault(
                logger,
                "attachment corpus search failed",
                exc,
                stage="attachment_corpus_search",
                level=logging.WARNING,
            )
            return {"success": False, "error": str(exc), "code": exc.code}

    return await _run()


ATTACHMENT_CORPUS_TOOLS = [search_attachment_docs]

__all__ = [
    "ATTACHMENT_CORPUS_TOOLS",
    "AttachmentRetrievalError",
    "attachment_corpus_filter",
    "search_attachment_docs",
    "search_corpus_attachments",
]
