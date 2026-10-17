"""Exact uploaded-attachment targeting tests (AT-F1, ``attachment_id``).

Clones the ``test_attachment_corpus`` idiom -- recording fake search and
embedding clients, stubbed ``tasks.scope``/``users.permissions`` -- for the
exact selector vertical: the native owner is bound before any inferred hint
resolver or provider work, misleading hints cannot narrow an exact target,
malformed selectors refuse before the network, every unavailable target is
indistinguishable, and rows that do not match the bound target are rejected
before excerpts reach model context.
"""

import asyncio
import inspect
import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from ai.core import config  # noqa: E402
from ai.core.integrations import attachment_corpus as corpus_mod  # noqa: E402
from ai.core.integrations import retrieval_authority  # noqa: E402
from ai.core.integrations.attachment_corpus import (  # noqa: E402
    AttachmentRetrievalError,
    attachment_corpus_filter,
    search_attachment_docs,
    search_corpus_attachments,
)
from pydantic import ValidationError as PydanticValidationError  # noqa: E402

SCOPE_KEY = "epcon-experimental"

#: The bound exact target the stub resolver reports (matches ``_row`` below).
BOUND_TARGET = {
    "attachment_id": 2,
    "model_type": "assetmachine",
    "model_id": 33,
    "source_sha256": "461479ab8523aa",
}


class _EmbeddingClient:
    """Fake Cohere client recording every query it was asked to embed."""

    def __init__(self, *, error: Exception | None = None):
        self.queries: list[str] = []
        self._error = error

    def embed_query(self, text):
        self.queries.append(text)
        if self._error is not None:
            raise self._error
        return [0.5] * 1536


class _SearchClient:
    """Fake search client recording the kwargs of each search call."""

    def __init__(self, rows=None):
        self.kwargs = None
        self.all_kwargs = []
        self.calls = 0
        self._rows = rows if rows is not None else [_row()]

    def search(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        self.all_kwargs.append(kwargs)
        return list(self._rows)


def _row(**overrides):
    """An index row as Azure Search would return it, private fields included."""
    row = {
        "id": "att-2-461479ab8523-c7",
        "attachment_id": 2,
        "model_type": "assetmachine",
        "model_id": 33,
        "part_id": None,
        "part_name": "",
        "asset_id": "PVS351-UL-2012-0173",
        "machine_name": "Siemens SINVERT PVS351 UL",
        "doc_type": "manual",
        "source_file_name": "UL1741_UserManual_PVS351UL.pdf",
        "section_path": "7 Commissioning > 7.1 Commissioning the inverter",
        "heading_1": "7 Commissioning",
        "heading_2": "7.1 Commissioning the inverter",
        "heading_3": "",
        "page_number": 60,
        "content": "Close the DC disconnect before energising the inverter.",
        "as_of": "2026-08-19",
        "access_class": "attachment_uploaded",
        "client_codes": ["acme"],
        "scope_key": SCOPE_KEY,
        "source_sha256": "461479ab8523aa",
        "is_current": True,
        "embedding_profile": None,
        "@search.score": 2.5,
    }
    row.update(overrides)
    return row


def _settings(**overrides):
    base = {
        "feature_attachment_rag_retrieval": True,
        "single_site_policy_key": SCOPE_KEY,
        "feature_question_cards": False,
        "cohere_embed_model": "embed-v-4-0",
        "cohere_embed_dimensions": 1536,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _StubScopeError(Exception):
    """Stands in for tasks.scope.ScopeError in these unit tests."""


def _stub_scope(monkeypatch, *, codes=frozenset({"acme"}), error=None):
    """Install a sys.modules stub for tasks.scope (app not importable here)."""

    def _resolver(user):
        if error is not None:
            raise error
        return codes

    scope_stub = types.ModuleType("tasks.scope")
    scope_stub.ScopeError = _StubScopeError
    scope_stub.client_codes_for_actor = _resolver
    tasks_pkg = types.ModuleType("tasks")
    tasks_pkg.scope = scope_stub
    monkeypatch.setitem(sys.modules, "tasks", tasks_pkg)
    monkeypatch.setitem(sys.modules, "tasks.scope", scope_stub)


def _stub_user_roles(monkeypatch, *, granted=("part", "work_order")):
    """Install a sys.modules stub for users.permissions.check_user_role."""
    stub = types.ModuleType("users.permissions")
    stub.check_user_role = lambda _user, role, _perm: role in granted
    users_pkg = types.ModuleType("users")
    users_pkg.permissions = stub
    monkeypatch.setitem(sys.modules, "users", users_pkg)
    monkeypatch.setitem(sys.modules, "users.permissions", stub)


def _user(**overrides):
    base = {"username": "tech", "is_superuser": True}
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(**overrides):
    """Run the search with recording fakes; return (search, embed, result)."""
    search_client = overrides.pop("search_client", _SearchClient())
    embedding_client = overrides.pop("embedding_client", _EmbeddingClient())
    kwargs = {
        "user": _user(),
        "query": "What is the DC disconnect commissioning step?",
        "search_client": search_client,
        "embedding_client": embedding_client,
    }
    kwargs.update(overrides)
    return search_client, embedding_client, search_corpus_attachments(**kwargs)


@pytest.fixture
def exact_env(monkeypatch):
    """Flag on, scope resolved, and fake native exact-target authority.

    The unit tier stubs the ORM seams only; the real binding and row
    authorization run in ``aichat.tests.test_attachment_exact_target``.
    ``row_matches_exact_target`` stays REAL so substitution logic is tested.
    """
    monkeypatch.setattr(config, "get_settings", lambda: _settings())
    from ai.core.integrations import projection_gate

    monkeypatch.setattr(projection_gate, "recall_allowed", lambda _corpus: True)
    _stub_scope(monkeypatch)
    _stub_user_roles(monkeypatch, granted=("part", "work_order"))
    monkeypatch.setattr(retrieval_authority, "attachment_rows", lambda rows, **_kw: rows)
    monkeypatch.setattr(retrieval_authority, "fresh_actor", lambda user: user)
    monkeypatch.setattr(retrieval_authority, "fresh_role", lambda _actor, _role: True)
    monkeypatch.setattr(retrieval_authority, "native_attachment_owner", lambda _actor, _att: True)
    # The exact-target seams do not exist before the runtime slice lands;
    # keep the fixture installable so the RED run can reach behavior.
    monkeypatch.setattr(
        retrieval_authority,
        "exact_attachment_target",
        lambda _user, _attachment_id: dict(BOUND_TARGET),
        raising=False,
    )

    def _row_reason(row, *, target, user):
        del user  # exposure-time actor checks are stubbed away at this tier
        return (
            "" if retrieval_authority.row_matches_exact_target(row, target) else "target_mismatch"
        )

    monkeypatch.setattr(
        retrieval_authority,
        "exact_target_row_reason",
        _row_reason,
        raising=False,
    )


# ---------------------------------------------------------------------------
# AT-R01/R03: exact binding wins over misleading inferred hints
# ---------------------------------------------------------------------------


def test_exact_machine_target_ignores_misleading_part_hint(exact_env):
    """The focused vertical slice: an exact machine attachment plus a valid
    but misleading Part hint and a valid but wrong doc_type must return only
    the selected source, with no hint resolver run and no narrowing."""
    calls: list[str] = []

    def _spy_resolver(_actor, _name):
        calls.append(_name)
        return [{"part_id": 99, "name": "gasket", "ipn": "G-1"}]

    search, _embed, result = _run(
        user=_user(is_superuser=False),
        attachment_id=2,
        part="gasket",
        doc_type="datasheet",
        part_resolver=_spy_resolver,
        machine_resolver=_spy_resolver,
    )
    assert calls == []  # no inferred hint resolver may run in exact mode
    assert search.calls == 1  # one exact query; no degrade retry
    filter_expression = search.kwargs["filter"]
    assert "attachment_id eq 2" in filter_expression
    assert "model_type eq 'assetmachine'" in filter_expression
    assert "model_id eq 33" in filter_expression
    assert "part_id" not in filter_expression
    assert "doc_type" not in filter_expression
    assert "asset_id" not in filter_expression
    assert result["attachment_filter"] == "exact"
    assert [chunk["citation"]["attachment_id"] for chunk in result["chunks"]] == [2]


@pytest.mark.parametrize("hint", ["pdf", "unknown", ""])
def test_exact_selector_ignores_unrecognized_doc_type(exact_env, hint):
    """Irrelevant type hints cannot hide an authorized exact attachment."""
    search, _embed, result = _run(attachment_id=2, doc_type=hint)
    assert search.calls == 1
    assert "doc_type" not in search.kwargs["filter"]
    assert result["attachment_filter"] == "exact"
    assert [chunk["citation"]["attachment_id"] for chunk in result["chunks"]] == [2]


@pytest.mark.parametrize("hint", ["pdf", "unknown", ""])
def test_non_exact_selector_rejects_unrecognized_doc_type(exact_env, hint):
    """Ordinary searches retain document-type validation before provider work."""
    embed, search = _EmbeddingClient(), _SearchClient()
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        _run(doc_type=hint, embedding_client=embed, search_client=search)
    assert excinfo.value.code == "ATTACHMENT_QUERY_INVALID"
    assert embed.queries == []
    assert search.calls == 0


@pytest.mark.parametrize("selector", [True, 2.0, "2"])
def test_invalid_selector_with_unrecognized_type_stays_invalid(exact_env, selector):
    """Ignoring an exact-target hint never relaxes selector validation."""
    embed, search = _EmbeddingClient(), _SearchClient()
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        _run(
            attachment_id=selector,
            doc_type="pdf",
            embedding_client=embed,
            search_client=search,
        )
    assert excinfo.value.code == "ATTACHMENT_TARGET_INVALID"
    assert embed.queries == []
    assert search.calls == 0


def test_unavailable_exact_selector_with_unrecognized_type_refuses(exact_env, monkeypatch):
    """An unavailable target stays unavailable without a broad-search fallback."""
    monkeypatch.setattr(retrieval_authority, "exact_attachment_target", lambda _u, _a: None)
    embed, search = _EmbeddingClient(), _SearchClient()
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        _run(attachment_id=7, doc_type="pdf", embedding_client=embed, search_client=search)
    assert excinfo.value.code == "ATTACHMENT_TARGET_UNAVAILABLE"
    assert embed.queries == []
    assert search.calls == 0


def test_selector_omission_keeps_existing_part_narrowing(exact_env):
    """Unchanged positive control: without the selector, hint narrowing and
    the broad/degrade semantics are exactly as before."""

    def _resolver(_actor, _name):
        return [{"part_id": 99, "name": "gasket", "ipn": "G-1"}]

    search, _embed, result = _run(part="gasket", part_resolver=_resolver)
    assert "part_id eq 99" in search.kwargs["filter"]
    assert result["chunks"]


# ---------------------------------------------------------------------------
# AT-R10: malformed selectors refuse before any network work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [True, False, 0, -1, 2.0, "2", "two"])
def test_invalid_selector_refuses_before_network(exact_env, bad):
    embed, search = _EmbeddingClient(), _SearchClient()
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        _run(attachment_id=bad, embedding_client=embed, search_client=search)
    assert excinfo.value.code == "ATTACHMENT_TARGET_INVALID"
    assert embed.queries == []
    assert search.calls == 0


@pytest.mark.parametrize("bad", [True, 0, -1, 2.0, "2"])
def test_filter_boundary_rejects_invalid_selector(bad):
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        attachment_corpus_filter(
            scope_key=SCOPE_KEY,
            client_codes={"acme"},
            model_types=("part",),
            attachment_id=bad,
        )
    assert excinfo.value.code == "ATTACHMENT_TARGET_INVALID"


# ---------------------------------------------------------------------------
# AT-R04: unavailable targets are one typed, value-free, indistinguishable refusal
# ---------------------------------------------------------------------------


def test_missing_and_denied_targets_refuse_identically(exact_env, monkeypatch):
    outcomes = []
    for _label in ("missing", "denied"):
        monkeypatch.setattr(retrieval_authority, "exact_attachment_target", lambda _u, _a: None)
        embed, search = _EmbeddingClient(), _SearchClient()
        with pytest.raises(AttachmentRetrievalError) as excinfo:
            _run(attachment_id=7, embedding_client=embed, search_client=search)
        assert excinfo.value.code == "ATTACHMENT_TARGET_UNAVAILABLE"
        assert embed.queries == []
        assert search.calls == 0
        outcomes.append(str(excinfo.value))
    assert outcomes[0] == outcomes[1]
    assert "7" not in outcomes[0]


# ---------------------------------------------------------------------------
# AT-R08/R09: every returned row must match the bound target
# ---------------------------------------------------------------------------


def test_foreign_rows_from_noncompliant_search_are_rejected(exact_env):
    foreign = _row(
        id="att-5-aaaa",
        attachment_id=5,
        model_id=44,
        source_sha256="deadbeef",
        source_file_name="other-client-file.pdf",
    )
    _search, _embed, result = _run(attachment_id=2, search_client=_SearchClient(rows=[foreign]))
    assert result["chunks"] == []


def test_row_with_wrong_source_hash_is_rejected(exact_env):
    tampered = _row(source_sha256="tampered-hash", source_file_name="leaky.pdf")
    _search, _embed, result = _run(
        attachment_id=2,
        search_client=_SearchClient(rows=[tampered, _row(id="att-2-ok")]),
    )
    assert [chunk["citation"]["chunk_id"] for chunk in result["chunks"]] == ["att-2-ok"]


def test_row_matches_exact_target_is_strict():
    assert retrieval_authority.row_matches_exact_target(_row(), BOUND_TARGET)
    for override in (
        {"attachment_id": 5},
        {"model_type": "part"},
        {"model_id": 44},
        {"source_sha256": "other"},
    ):
        assert not retrieval_authority.row_matches_exact_target(_row(**override), BOUND_TARGET)
    assert not retrieval_authority.row_matches_exact_target(_row(), {})


# ---------------------------------------------------------------------------
# AT-R12 + diagnostics: wrapper schema, guidance, bounded value-free telemetry
# ---------------------------------------------------------------------------


def test_wrapper_schema_exposes_optional_attachment_id():
    sig = inspect.signature(inspect.unwrap(search_attachment_docs))
    param = sig.parameters.get("attachment_id")
    assert param is not None
    assert param.default is None


def test_exact_outcome_is_recorded_value_free(exact_env, monkeypatch):
    from ai.core.tools import capture_ledger

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        capture_ledger,
        "record_retrieval_meta",
        lambda tool_id, meta: seen.append((tool_id, dict(meta))),
    )
    _run(attachment_id=2)
    exact = [meta for _tool, meta in seen if meta.get("target_filter") == "exact"]
    assert exact, f"no exact target meta recorded: {seen}"
    assert exact[0]["target_selector"] == "attachment_id"
    assert exact[0]["returned_count"] == 1
    assert exact[0]["refusal_status"] == "ok"
    blob = json.dumps(seen)
    assert "DC disconnect" not in blob  # no query text
    assert "UL1741" not in blob  # no source file name
    assert "acme" not in blob  # no ACL/client codes


def test_exact_refusal_is_recorded_value_free(exact_env, monkeypatch):
    from ai.core.tools import capture_ledger

    monkeypatch.setattr(retrieval_authority, "exact_attachment_target", lambda _u, _a: None)
    seen: list[dict] = []
    monkeypatch.setattr(
        capture_ledger,
        "record_retrieval_meta",
        lambda _tool_id, meta: seen.append(dict(meta)),
    )
    with pytest.raises(AttachmentRetrievalError):
        _run(attachment_id=7)
    assert seen and seen[-1]["refusal_status"] == "target_unavailable"
    assert seen[-1]["returned_count"] == 0
    # The caller's own requested id may be echoed inside protected internal
    # metadata (it discloses no existence); nothing else may carry it.
    echo_free = [dict(meta, requested_attachment_id=None) for meta in seen]
    assert "7" not in json.dumps(echo_free)


def test_lookup_guidance_requires_exact_selector():
    wf8 = Path(corpus_mod.__file__).resolve().parents[1] / "workflows" / "wf8_lookup.py"
    text = wf8.read_text(encoding="utf-8")
    assert "attachment_id" in text
    assert "not a native Part ID" in text
    assert "ATTACHMENT_TARGET_UNAVAILABLE" in text


# ---------------------------------------------------------------------------
# AT-R10/R12: the REAL generated SDK input model must refuse coerced selectors
# ---------------------------------------------------------------------------


def _sdk_input_model():
    """The input model the pinned SDK generates for the real tool callable."""
    from agent_framework import ai_function as sdk_ai_function

    return sdk_ai_function(search_attachment_docs).input_model


def test_sdk_input_model_refuses_coerced_selectors():
    """True/2.0/'2' must never parse as a native positive int selector."""
    model = _sdk_input_model()
    for bad in (True, False, 0, -1, 2.0, 2.5, "2", "two"):
        with pytest.raises(PydanticValidationError):
            model.model_validate({"query": "q", "attachment_id": bad})
    good = model.model_validate({"query": "q", "attachment_id": 59})
    assert good.attachment_id == 59 and type(good.attachment_id) is int
    assert model.model_validate({"query": "q"}).attachment_id is None
    assert model.model_validate({"query": "q", "attachment_id": None}).attachment_id is None
    schema = model.model_json_schema()["properties"]["attachment_id"]
    parts = schema.get("anyOf", [schema])
    assert {"type": "null"} in parts
    integer = next(part for part in parts if part.get("type") == "integer")
    assert integer.get("exclusiveMinimum") == 0 or integer.get("minimum", 0) >= 1


def test_sdk_wrapper_forwards_strict_selector(exact_env, monkeypatch):
    """Validated SDK arguments reach the search body as native ints/None."""
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {
            "chunks": [],
            "returned_count": 0,
            "machine_filter": "not_requested",
            "part_filter": "not_requested",
        }

    monkeypatch.setattr(corpus_mod, "_current_user", lambda: _user())
    monkeypatch.setattr(corpus_mod, "search_corpus_attachments", _capture)
    model = _sdk_input_model()
    parsed = model.model_validate({"query": "q", "attachment_id": 59})
    asyncio.run(search_attachment_docs(**parsed.model_dump()))
    assert captured["attachment_id"] == 59
    assert type(captured["attachment_id"]) is int
    omitted = model.model_validate({"query": "q"})
    asyncio.run(search_attachment_docs(**omitted.model_dump()))
    assert captured["attachment_id"] is None


def test_selector_guidance_is_in_args_section():
    """attachment_id guidance belongs to Args, never under Returns."""
    doc = inspect.getdoc(search_attachment_docs)
    args_part, sep, returns_part = doc.partition("Returns:")
    assert sep
    assert "attachment_id" in args_part.partition("Args:")[2]
    assert "ATTACHMENT_TARGET_UNAVAILABLE" in args_part
    assert "attachment_id" not in returns_part


# ---------------------------------------------------------------------------
# AT-F1 diagnostics: bounded requested-vs-effective internal metadata
# ---------------------------------------------------------------------------


def test_exact_diagnostics_record_requested_vs_effective(exact_env, monkeypatch):
    from ai.core.tools import capture_ledger

    seen: list[dict] = []
    monkeypatch.setattr(
        capture_ledger, "record_retrieval_meta", lambda _tool_id, meta: seen.append(dict(meta))
    )
    _run(attachment_id=2, part="gasket", doc_type="datasheet")
    exact = [meta for meta in seen if "requested_attachment_id" in meta]
    assert exact, seen
    meta = exact[0]
    assert meta["target_selector"] == "attachment_id"
    assert meta["requested_attachment_id"] == 2
    assert meta["effective_attachment_id"] == 2
    assert meta["effective_owner_type"] == "assetmachine"
    assert meta["effective_owner_id"] == 33
    assert sorted(meta.get("ignored_hints") or []) == ["doc_type", "part"]
    assert meta["returned_count"] == 1
    assert meta["refusal_status"] == "ok"


def test_exact_refusal_diagnostics_keep_effective_absent(exact_env, monkeypatch):
    from ai.core.tools import capture_ledger

    monkeypatch.setattr(retrieval_authority, "exact_attachment_target", lambda _u, _a: None)
    seen: list[dict] = []
    monkeypatch.setattr(
        capture_ledger, "record_retrieval_meta", lambda _tool_id, meta: seen.append(dict(meta))
    )
    with pytest.raises(AttachmentRetrievalError):
        _run(attachment_id=7)
    meta = seen[-1]
    assert meta["refusal_status"] == "target_unavailable"
    # Caller-supplied identity only; no existence disclosure, no owner/file data.
    assert meta["requested_attachment_id"] == 7
    assert "effective_attachment_id" not in meta
    assert "effective_owner_type" not in meta
    assert "effective_owner_id" not in meta
    blob = json.dumps(seen)
    for leak in ("gasket", "acme", "UL1741", "DC disconnect"):
        assert leak not in blob


def test_invalid_selector_diagnostics_omit_raw_values(exact_env, monkeypatch):
    from ai.core.tools import capture_ledger

    seen: list[dict] = []
    monkeypatch.setattr(
        capture_ledger, "record_retrieval_meta", lambda _tool_id, meta: seen.append(dict(meta))
    )
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        _run(attachment_id=2.5)
    assert excinfo.value.code == "ATTACHMENT_TARGET_INVALID"
    meta = seen[-1]
    assert meta["refusal_status"] == "invalid_selector"
    assert "requested_attachment_id" not in meta
    assert "2.5" not in json.dumps(seen)


# ---------------------------------------------------------------------------
# AT-R05: mid-provider binding loss is unavailable, never "no passages"
# ---------------------------------------------------------------------------


def test_mid_provider_binding_loss_is_unavailable_not_no_passages(exact_env, monkeypatch):
    from ai.core.tools import capture_ledger

    seen: list[dict] = []
    monkeypatch.setattr(
        capture_ledger, "record_retrieval_meta", lambda _tool_id, meta: seen.append(dict(meta))
    )
    calls = {"n": 0}

    def _bind(_user, _attachment_id):
        calls["n"] += 1
        return dict(BOUND_TARGET) if calls["n"] == 1 else None

    monkeypatch.setattr(retrieval_authority, "exact_attachment_target", _bind)
    search = _SearchClient(rows=[_row()])
    embed = _EmbeddingClient()
    with pytest.raises(AttachmentRetrievalError) as excinfo:
        _run(attachment_id=2, search_client=search, embedding_client=embed)
    assert excinfo.value.code == "ATTACHMENT_TARGET_UNAVAILABLE"
    assert search.calls == 1  # provider work happened; the refusal comes after
    assert [meta for meta in seen if meta.get("refusal_status") == "no_passages"] == []
    assert seen and seen[-1]["refusal_status"] == "target_unavailable"
