"""Site-scoped corpus retrieval tests for the controlled-document manuals tool.

Mirrors ``test_controlled_document_search`` -- fake search and embedding
clients that record their kwargs -- but exercises the corpus twin: the filter
is built from server-side deployment constants (``scope_key``, ``is_current``,
``access_class``), machine narrowing is resolved server-side and degrades
rather than widens, and the citation payload never leaks storage coordinates.
"""

from types import SimpleNamespace

import pytest
from ai.core import config
from ai.core.integrations.controlled_document_corpus import (
    ControlledDocumentSearchError,
    corpus_filter,
    search_corpus,
)

SCOPE_KEY = "epcon-experimental"

BASE_FILTER = (
    f"scope_key eq '{SCOPE_KEY}' and is_current eq true "
    "and access_class eq 'maintenance_authorized'"
)


class _EmbeddingClient:
    """Fake embedding client recording every batch it was asked to embed."""

    def __init__(self, *, error: Exception | None = None):
        self.calls: list[list[str]] = []
        self._error = error

    def embed_batch(self, inputs):
        self.calls.append(list(inputs))
        if self._error is not None:
            raise self._error
        return [[0.5] * 3072]


class _SearchClient:
    """Fake search client recording the kwargs of its single search call."""

    def __init__(self, rows=None):
        self.kwargs = None
        self.calls = 0
        self._rows = rows if rows is not None else [_row()]

    def search(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return list(self._rows)


def _row(**overrides):
    """An index row as Azure Search would return it, storage path included."""
    row = {
        "id": "search-key-1",
        "chunk_id": "16:0000",
        "document_id": "aimms-tc-inf-ps1-manual",
        "document_revision": "2.0",
        "source_file_name": "pump_station_manual.md",
        "section_id": "16",
        "section_path": ("Influent Pump Station No. 1 > 16. Diagnostic Reasoning Framework"),
        "heading_1": "Influent Pump Station No. 1",
        "heading_2": "16. Diagnostic Reasoning Framework",
        "heading_3": "",
        "chunk": "Pump 2 tripped after seal leakage and rising current.",
        "as_of": "2026-07-26",
        "access_class": "maintenance_authorized",
        "asset_id": "PS1-0001",
        "document_class": "technical_manual",
        # The output contract strips this even when the index returns it.
        "source_blob_path": "raw/pump_station_manual.md",
        "@search.score": 2.5,
    }
    row.update(overrides)
    return row


@pytest.fixture
def site_scope(monkeypatch):
    """Pin the deployment's site key the way production reads it."""
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(single_site_policy_key=SCOPE_KEY),
    )


@pytest.fixture
def blank_scope(monkeypatch):
    """A deployment whose single-site policy key was never configured."""
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(single_site_policy_key=""),
    )


def _run(**overrides):
    """Run search_corpus with recording fakes; return (search, embed, result)."""
    search_client = overrides.pop("search_client", _SearchClient())
    embedding_client = overrides.pop("embedding_client", _EmbeddingClient())
    kwargs = {
        "user": SimpleNamespace(username="tech"),
        "query": "What is the seal replacement procedure?",
        "search_client": search_client,
        "embedding_client": embedding_client,
    }
    kwargs.update(overrides)
    return search_client, embedding_client, search_corpus(**kwargs)


# ---------------------------------------------------------------------------
# corpus_filter: the non-negotiable server-side filter string
# ---------------------------------------------------------------------------


def test_corpus_filter_base_expression_is_exact():
    """Scope, currency and access class are always present, in that order."""
    assert corpus_filter(scope_key=SCOPE_KEY) == BASE_FILTER


def test_corpus_filter_appends_asset_and_document_class_narrowing():
    """Optional narrowing clauses append after the mandatory ones."""
    assert corpus_filter(scope_key=SCOPE_KEY, asset_id="PS1-0001", document_class="procedure") == (
        f"{BASE_FILTER} and asset_id eq 'PS1-0001' and document_class eq 'procedure'"
    )


def test_corpus_filter_doubles_odata_single_quotes():
    """Values with quotes are escaped as OData literals, not concatenated raw."""
    assert corpus_filter(scope_key="o'brien site", asset_id="PS1'0001") == (
        "scope_key eq 'o''brien site' and is_current eq true "
        "and access_class eq 'maintenance_authorized' "
        "and asset_id eq 'PS1''0001'"
    )


def test_corpus_filter_refuses_empty_scope_key():
    """An unconfigured site key refuses rather than widening the query."""
    with pytest.raises(ControlledDocumentSearchError) as excinfo:
        corpus_filter(scope_key="")
    assert excinfo.value.code == "CONTROLLED_DOCUMENT_SCOPE_UNCONFIGURED"


# ---------------------------------------------------------------------------
# search_corpus: input validation before any client is touched
# ---------------------------------------------------------------------------


def test_unconfigured_scope_key_refuses_without_calling_search(blank_scope):
    """A blank policy key fails closed: no embedding, no search request."""
    search_client = _SearchClient()
    embedding_client = _EmbeddingClient()
    with pytest.raises(ControlledDocumentSearchError) as excinfo:
        _run(search_client=search_client, embedding_client=embedding_client)
    assert excinfo.value.code == "CONTROLLED_DOCUMENT_SCOPE_UNCONFIGURED"
    assert search_client.calls == 0
    assert embedding_client.calls == []


@pytest.mark.parametrize("query", ["", "   ", "x" * 4001, None, 42])
def test_invalid_query_is_refused(site_scope, query):
    """Empty, whitespace, oversized or non-string queries never run."""
    search_client = _SearchClient()
    with pytest.raises(ControlledDocumentSearchError) as excinfo:
        _run(query=query, search_client=search_client)
    assert excinfo.value.code == "CONTROLLED_DOCUMENT_QUERY_INVALID"
    assert search_client.calls == 0


@pytest.mark.parametrize("top_k", [True, False, "3", 2.5, None])
def test_non_integer_top_k_is_refused(site_scope, top_k):
    """Booleans and non-ints are refused instead of being coerced."""
    search_client = _SearchClient()
    with pytest.raises(ControlledDocumentSearchError) as excinfo:
        _run(top_k=top_k, search_client=search_client)
    assert excinfo.value.code == "CONTROLLED_DOCUMENT_QUERY_INVALID"
    assert search_client.calls == 0


@pytest.mark.parametrize(("requested", "effective"), [(99, 5), (6, 5), (0, 1), (-3, 1)])
def test_out_of_range_top_k_is_clamped(site_scope, requested, effective):
    """Integer top_k is clamped into 1..5 for both k-NN and top."""
    search_client, _, _ = _run(top_k=requested)
    assert search_client.kwargs["top"] == effective
    assert search_client.kwargs["vector_queries"][0].k_nearest_neighbors == effective


def test_unknown_document_class_is_refused(site_scope):
    """document_class comes from the model, so it must match the allowlist."""
    search_client = _SearchClient()
    with pytest.raises(ControlledDocumentSearchError) as excinfo:
        _run(document_class="blueprint", search_client=search_client)
    assert excinfo.value.code == "CONTROLLED_DOCUMENT_QUERY_INVALID"
    assert search_client.calls == 0


def test_allowlisted_document_class_narrows_the_filter(site_scope):
    """A recognised document_class appends a filter clause."""
    search_client, _, result = _run(document_class="knowledge_base")
    assert search_client.kwargs["filter"] == (
        f"{BASE_FILTER} and document_class eq 'knowledge_base'"
    )
    assert result["machine_filter"] == "not_requested"


class _ClassFilteredSearchClient(_SearchClient):
    """Returns nothing while a document_class clause is present."""

    def search(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if "document_class eq" in kwargs["filter"]:
            return []
        return list(self._rows)


def test_class_narrowing_degrades_instead_of_reporting_an_empty_manual(site_scope):
    """Class narrowing is a precision hint, never the reason for zero results.

    The live corpus carries class values outside the request allowlist, so an
    allowlisted narrowing filtered out EVERYTHING and the model honestly told
    the user the manual had no answer it demonstrably has (battery A1,
    2026-08-06). A zero-hit class filter must retry site-wide.
    """
    search_client = _ClassFilteredSearchClient()
    _, _, result = _run(search_client=search_client, document_class="technical_manual")
    assert search_client.calls == 2
    assert "document_class eq" not in search_client.kwargs["filter"]
    assert result["returned_count"] == 1


def test_class_narrowing_with_hits_does_not_retry(site_scope):
    search_client, _, result = _run(document_class="technical_manual")
    assert search_client.calls == 1
    assert result["returned_count"] == 1


def test_live_ingested_document_class_is_allowlisted(site_scope):
    """The class value the live chunks actually carry must build a filter.

    The ingested corpus classes its chunks
    `controlled_operations_maintenance_diagnostics_repair_knowledge` — outside
    the original request allowlist, so the model could never narrow to the
    documents that exist (battery A1 follow-up). Allowlisted until the manual
    is re-classed to `controlled_o_and_m` at the next re-ingestion.
    """
    live_class = "controlled_operations_maintenance_diagnostics_repair_knowledge"
    search_client, _, result = _run(document_class=live_class)
    assert search_client.kwargs["filter"] == (f"{BASE_FILTER} and document_class eq '{live_class}'")
    assert result["returned_count"] == 1
    # Widening the allowlist must not have weakened the refusal for classes
    # that exist nowhere.
    refused = _SearchClient()
    with pytest.raises(ControlledDocumentSearchError):
        _run(document_class="blueprint", search_client=refused)
    assert refused.calls == 0


# ---------------------------------------------------------------------------
# search_corpus: server-side machine narrowing
# ---------------------------------------------------------------------------


def _resolver(candidates):
    calls = []

    def resolver(actor, name):
        calls.append((actor, name))
        return candidates

    resolver.calls = calls
    return resolver


def test_single_machine_with_serial_narrows_by_asset_id(site_scope):
    """Exactly one resolvable machine with a serial becomes an asset filter."""
    resolver = _resolver([{"machine_id": 7, "name": "Influent Pump 1", "serial": "PS1-0001"}])
    search_client, _, result = _run(machine="Influent Pump 1", machine_resolver=resolver)
    assert search_client.kwargs["filter"] == f"{BASE_FILTER} and asset_id eq 'PS1-0001'"
    assert result["machine_filter"] == "applied"


def test_machine_name_is_bounded_before_it_reaches_the_resolver(site_scope):
    """The resolver sees at most 100 characters of caller-supplied name."""
    resolver = _resolver([])
    _run(machine="M" * 150, machine_resolver=resolver)
    ((_, name),) = resolver.calls
    assert name == "M" * 100


def test_ambiguous_machine_returns_candidates_without_searching(site_scope):
    """Two candidates surface a disambiguation payload and never hit Azure."""
    candidates = [
        {"machine_id": 7, "name": "Influent Pump 1", "serial": "PS1-0001"},
        {"machine_id": 8, "name": "Influent Pump 2", "serial": "PS1-0002"},
    ]
    search_client = _SearchClient()
    embedding_client = _EmbeddingClient()
    _, _, result = _run(
        machine="Influent Pump",
        machine_resolver=_resolver(candidates),
        search_client=search_client,
        embedding_client=embedding_client,
    )
    assert result == {
        "chunks": [],
        "returned_count": 0,
        "machine_filter": "ambiguous",
        "machine_candidates": candidates,
    }
    assert search_client.calls == 0
    assert embedding_client.calls == []


def test_unresolvable_machine_degrades_to_site_wide_query(site_scope):
    """No candidates: narrowing is precision, not authorization, so run wide."""
    search_client, _, result = _run(machine="Ghost Pump", machine_resolver=_resolver([]))
    assert result["machine_filter"] == "not_applied"
    assert search_client.kwargs["filter"] == BASE_FILTER
    assert search_client.calls == 1


def test_single_machine_without_serial_degrades_to_site_wide_query(site_scope):
    """A machine with no serial cannot narrow: still one site-wide query."""
    resolver = _resolver([{"machine_id": 7, "name": "Influent Pump 1", "serial": ""}])
    search_client, _, result = _run(machine="Influent Pump 1", machine_resolver=resolver)
    assert result["machine_filter"] == "not_applied"
    assert search_client.kwargs["filter"] == BASE_FILTER


# ---------------------------------------------------------------------------
# search_corpus: query construction and the citation payload
# ---------------------------------------------------------------------------


def test_hybrid_query_kwargs_and_citation_payload(site_scope):
    """The search call is a pre-filtered semantic hybrid; citations are full."""
    import hashlib

    search_client, embedding_client, result = _run()

    assert embedding_client.calls == [["What is the seal replacement procedure?"]]
    kwargs = search_client.kwargs
    assert kwargs["search_text"] == "What is the seal replacement procedure?"
    assert kwargs["vector_filter_mode"] == "preFilter"
    assert kwargs["query_type"] == "semantic"
    assert kwargs["semantic_configuration_name"] == "semantic-default"
    assert kwargs["filter"] == BASE_FILTER
    vector_query = kwargs["vector_queries"][0]
    assert vector_query.fields == "text_vector"
    assert vector_query.k_nearest_neighbors == 5
    assert "section_path" in kwargs["select"]
    assert "source_blob_path" not in kwargs["select"]

    assert result["returned_count"] == 1
    assert result["machine_filter"] == "not_requested"
    chunk = result["chunks"][0]
    # S5: the excerpt is fenced (the one previously-unfenced retrieval text).
    assert chunk["excerpt"].startswith("[UNTRUSTED-CONTENT-BEGIN]")
    assert "Pump 2 tripped after seal leakage and rising current." in chunk["excerpt"]
    assert chunk["excerpt"].endswith("[UNTRUSTED-CONTENT-END]")
    assert chunk["score"] == pytest.approx(2.5)
    citation = chunk["citation"]
    assert citation["document_id"] == "aimms-tc-inf-ps1-manual"
    assert citation["revision"] == "2.0"
    assert citation["section_id"] == "16"
    assert citation["section_path"] == (
        "Influent Pump Station No. 1 > 16. Diagnostic Reasoning Framework"
    )
    assert citation["chunk_id"] == "16:0000"
    assert citation["as_of"] == "2026-07-26"
    # The hash covers the RAW truncated text (the grounding auditor's
    # excerpt identity), not the fenced rendering.
    assert citation["excerpt_hash"] == (
        hashlib.sha256(b"Pump 2 tripped after seal leakage and rising current.").hexdigest()
    )
    # No registry row exists in this world, so the friendly title derives
    # from the source file name.
    assert citation["document"] == "pump station manual (rev 2.0)"


def test_payload_never_contains_storage_coordinates(site_scope):
    """source_blob_path from the index never reaches the model-visible payload."""
    import json

    _, _, result = _run()
    assert "source_blob_path" not in json.dumps(result)


def test_excerpt_is_truncated_and_hashed_over_the_truncation(site_scope):
    """Oversized chunks are cut to 8000 chars; the hash covers what is shown."""
    import hashlib

    search_client = _SearchClient(rows=[_row(chunk="x" * 9000)])
    _, _, result = _run(search_client=search_client)
    chunk = result["chunks"][0]
    # Fenced excerpt: 8000 raw chars plus the fence markers and newlines.
    assert "x" * 8000 in chunk["excerpt"]
    assert "x" * 8001 not in chunk["excerpt"]
    assert chunk["citation"]["excerpt_hash"] == (
        hashlib.sha256(("x" * 8000).encode("utf-8")).hexdigest()
    )


def test_embedding_failure_surfaces_a_stable_code(site_scope):
    """An embedding outage refuses with the query-embedding code, no search."""
    search_client = _SearchClient()
    embedding_client = _EmbeddingClient(error=RuntimeError("deployment offline"))
    with pytest.raises(ControlledDocumentSearchError) as excinfo:
        _run(search_client=search_client, embedding_client=embedding_client)
    assert excinfo.value.code == "CONTROLLED_DOCUMENT_QUERY_EMBEDDING_FAILED"
    assert search_client.calls == 0


# ---------------------------------------------------------------------------
# ai.core.config: environment alias spellings populate the canonical fields
# ---------------------------------------------------------------------------


def _fresh_settings(monkeypatch, tmp_path, **env):
    """Instantiate Settings from a controlled environment, ignoring .env."""
    monkeypatch.chdir(tmp_path)  # directory validators mkdir relative paths
    for name in (
        "AZURE_SEARCH_API_KEY",
        "AZURE_SEARCH_KEY",
        "AZURE_SEARCH_INDEX_NAME",
        "AZURE_SEARCH_INDEX",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return config.Settings(_env_file=None)


def test_alias_env_names_populate_canonical_fields(monkeypatch, tmp_path):
    """The alias spellings that shipped in real .env files still load."""
    settings = _fresh_settings(
        monkeypatch,
        tmp_path,
        AZURE_SEARCH_KEY="alias-key",
        AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT="text-embedding-3-large",
    )
    assert settings.azure_search_api_key == "alias-key"
    assert settings.azure_openai_embedding_deployment == "text-embedding-3-large"


def test_canonical_env_names_win_over_aliases(monkeypatch, tmp_path):
    """When both spellings are set, the canonical name takes precedence."""
    settings = _fresh_settings(
        monkeypatch,
        tmp_path,
        AZURE_SEARCH_API_KEY="canonical-key",
        AZURE_SEARCH_KEY="alias-key",
        AZURE_OPENAI_EMBEDDING_DEPLOYMENT="canonical-embedding",
        AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT="alias-embedding",
    )
    assert settings.azure_search_api_key == "canonical-key"
    assert settings.azure_openai_embedding_deployment == "canonical-embedding"


# ---------------------------------------------------------------------------
# S5 (WP-A3): analysis-scope enforcement — no-broadening pins
# ---------------------------------------------------------------------------
from ai.core.analysis.scope_context import (  # noqa: E402
    TurnScopeContext,
    turn_scope_context,
)


def _scope(serials=("PS1-0001",), *, enforce=True, shadow=True, machine_ids=(4,)):
    return TurnScopeContext(
        mode="explicit_assets",
        machine_ids=frozenset(machine_ids),
        machine_serials=frozenset(serials),
        date_from=None,
        date_to=None,
        source_classes=frozenset({"controlled_document"}),
        scope_hash="d" * 64,
        scope_version=2,
        snapshot_id="snap_deadbeefdeadbeefdead",
        thread_pk=1,
        display_label="Pump bay",
        shadow=shadow,
        enforce=enforce,
    )


@pytest.fixture
def _reset_scope():
    token = turn_scope_context.set(None)
    yield
    turn_scope_context.reset(token)


def test_corpus_filter_scope_serials_use_search_in():
    assert corpus_filter(scope_key=SCOPE_KEY, asset_ids=("B-2", "A-1")) == (
        f"{BASE_FILTER} and search.in(asset_id, 'A-1,B-2', ',')"
    )
    # The multi-valued scope clause wins over a resolver-derived single id.
    assert "search.in(asset_id" in corpus_filter(
        scope_key=SCOPE_KEY, asset_id="X", asset_ids=("A-1",)
    )


def test_enforced_scope_drives_the_filter_and_ignores_the_model_name(site_scope, _reset_scope):
    """§8.4: the model-supplied machine name is never the scope mechanism."""
    turn_scope_context.set(_scope())
    resolver_calls = []

    def resolver(actor, name):
        resolver_calls.append(name)
        return []

    search_client, _, result = _run(machine="Some Other Machine", machine_resolver=resolver)
    assert resolver_calls == [], "name resolution must not run under an enforced scope"
    assert "search.in(asset_id, 'PS1-0001', ',')" in search_client.kwargs["filter"]
    assert result["machine_filter"] == "scope_applied"


def test_enforced_scope_zero_hits_never_broaden(site_scope, _reset_scope):
    """The site-wide name-degrade is structurally unreachable under enforce."""
    turn_scope_context.set(_scope())
    search_client = _SearchClient(rows=[])
    client, _, result = _run(search_client=search_client)
    # One search (plus no class retry without a class arg): every call keeps
    # the scope clause — nothing ever ran without it.
    assert client.calls == 1
    assert "search.in(asset_id, 'PS1-0001', ',')" in client.kwargs["filter"]
    assert result["returned_count"] == 0
    assert "no_relevant_passage_retrieved" in result["retrieval"]["warnings"]


def test_enforced_scope_class_fallback_keeps_the_asset_floor(site_scope, _reset_scope):
    """Degrade #2 (class removal) survives, but inside the scope floor."""
    turn_scope_context.set(_scope())

    class _TwoCallClient(_SearchClient):
        def __init__(self):
            super().__init__(rows=[])
            self.filters = []

        def search(self, **kwargs):
            self.calls += 1
            self.kwargs = kwargs
            self.filters.append(kwargs["filter"])
            return [] if self.calls == 1 else [_row()]

    client = _TwoCallClient()
    _, _, result = _run(search_client=client, document_class="procedure")
    assert client.calls == 2
    assert all("search.in(asset_id, 'PS1-0001', ',')" in f for f in client.filters), (
        "the class retry may widen the class, never the asset scope"
    )
    assert result["returned_count"] == 1


def test_serial_less_enforced_scope_is_applicability_unresolved(site_scope, _reset_scope):
    """A scoped machine without a serial never triggers fleet-wide search."""
    turn_scope_context.set(_scope(serials=()))
    search_client, embed, result = _run()
    assert search_client.calls == 0, "no search may run"
    assert embed.calls == [], "no embedding may run"
    assert result["scope_miss"] is True
    assert result["applicability"] == "unresolved"
    assert result["machine_filter"] == "scope_unresolved"


def test_shadow_scope_keeps_legacy_behavior(site_scope, _reset_scope):
    """Shadow observes: the legacy name-degrade result is unchanged."""
    turn_scope_context.set(_scope(enforce=False))
    search_client, _, result = _run()
    assert "search.in(asset_id" not in search_client.kwargs["filter"]
    assert result["returned_count"] == 1
