"""S4 adapter fault controls; native refusal production is a separate lane."""

import json
from contextlib import contextmanager

import pytest
from ai.core.analysis import executor, source_gateway
from ai.core.analysis.evidence import EvidenceStore
from ai.core.analysis.snapshot import AnalysisRetrievalIncomplete
from ai.core.contracts.retrieval import build_envelope


@contextmanager
def _cache_principal(*, user_pk="41", scope="synthetic-site", policy="synthetic-v1"):
    """Bind the actual context variable; this is not an authenticated request."""
    from ai.core.auth import AIPrincipal, principal_context

    principal = AIPrincipal(
        subject="synthetic-subject-" + user_pk,
        actor="synthetic-actor-" + user_pk,
        user_pk=user_pk,
        username="synthetic-cache-user",
        authentication_method="synthetic-cache-fixture",
        scope=scope,
        policy_version=policy,
        is_staff=False,
        is_superuser=False,
    )
    token = principal_context.set(principal)
    try:
        yield principal
    finally:
        principal_context.reset(token)


@pytest.mark.asyncio
async def test_bound_ai_actors_do_not_share_the_unbound_api_get_cache():
    """Real request/cache funnel must observe both scripted source reads."""
    from ai.core.tests.test_inventree_read_cache import _client_with_transport

    client, transport = _client_with_transport(ttl=30.0)
    with _cache_principal(user_pk="41"):
        first = await client._request("GET", "/part/", params={"a": 1})
    with _cache_principal(user_pk="42"):
        second = await client._request("GET", "/part/", params={"a": 1})
    assert transport.calls == 2
    assert first == {"n": 1}
    assert second == {"n": 2}
    assert client._read_cache == {}


@pytest.mark.asyncio
async def test_ai_gets_ignore_and_preserve_an_existing_unbound_cache():
    """AI reads must not consume, replace or clear another consumer's cache."""
    from ai.core.tests.test_inventree_read_cache import _client_with_transport

    client, transport = _client_with_transport(ttl=30.0)
    native = await client._request("GET", "/part/", params={"a": 1})
    retained = dict(client._read_cache)
    with _cache_principal():
        first = await client._request("GET", "/part/", params={"a": 1})
        second = await client._request("GET", "/part/", params={"a": 1})
    assert native == {"n": 1}
    assert first == {"n": 2}
    assert second == {"n": 3}
    assert client._read_cache == retained
    assert await client._request("GET", "/part/", params={"a": 1}) == native
    assert transport.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "scope", "policy"])
async def test_same_ai_actor_never_reuses_a_ttl_entry_after_change(changed):
    """Context/source snapshots are scripted, not native grant qualification."""
    from ai.core.tests.test_inventree_read_cache import _client_with_transport

    client, transport = _client_with_transport(ttl=30.0)
    with _cache_principal():
        first = await client._request("GET", "/part/", params={"a": 1})
        with _cache_principal(
            scope="changed-site" if changed == "scope" else "synthetic-site",
            policy="changed-policy" if changed == "policy" else "synthetic-v1",
        ):
            second = await client._request("GET", "/part/", params={"a": 1})
    assert transport.calls == 2
    assert first == {"n": 1} and second == {"n": 2}
    assert client._read_cache == {}


@pytest.mark.asyncio
async def test_an_ai_response_does_not_seed_a_later_unbound_cache_hit():
    """The shared client's legacy reader must make its own first observation."""
    from ai.core.tests.test_inventree_read_cache import _client_with_transport

    client, transport = _client_with_transport(ttl=30.0)
    with _cache_principal():
        first = await client._request("GET", "/part/", params={"a": 1})
    assert client._read_cache == {}
    native = await client._request("GET", "/part/", params={"a": 1})
    assert first == {"n": 1} and native == {"n": 2}
    assert await client._request("GET", "/part/", params={"a": 1}) == native
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_principal_bound_inside_transport_prevents_cache_publication():
    """A final cache write rechecks context after the actual response await."""
    from ai.core.auth import get_current_principal, principal_context
    from ai.core.tests.test_inventree_read_cache import _client_with_transport

    client, transport = _client_with_transport(ttl=30.0)
    with _cache_principal() as principal:
        pass
    original = transport.request
    tokens = []

    async def request(*args, **kwargs):
        result = await original(*args, **kwargs)
        tokens.append(principal_context.set(principal))
        return result

    transport.request = request
    try:
        assert get_current_principal() is None
        assert await client._request("GET", "/part/", params={"a": 1}) == {"n": 1}
        assert get_current_principal() is principal
        assert client._read_cache == {}
        assert transport.calls == 1
    finally:
        for token in reversed(tokens):
            principal_context.reset(token)


@pytest.mark.asyncio
async def test_open_circuit_is_not_hidden_by_an_unbound_cache_for_ai():
    """Cache bypass must preserve an unavailable source rather than old data."""
    from ai.core.integrations.inventree.client import CircuitState, TransientError
    from ai.core.tests.test_inventree_read_cache import _client_with_transport
    from tenacity import RetryError

    client, transport = _client_with_transport(ttl=30.0)
    native = await client._request("GET", "/part/", params={"a": 1})
    client._circuit_breaker.state = CircuitState.OPEN
    with _cache_principal(), pytest.raises(RetryError) as raised:
        await client._request("GET", "/part/", params={"a": 1})
    fault = raised.value.last_attempt.exception()
    assert isinstance(fault, TransientError)
    assert "Circuit breaker is open" in str(fault)
    assert transport.calls == 1
    assert await client._request("GET", "/part/", params={"a": 1}) == native


def _empty_registry() -> dict:
    return {
        "sections": {
            "controlled_documents": {
                "documents": [],
                "population_count": 0,
                "returned_count": 0,
                "display_truncated": False,
                "retrieval": build_envelope(
                    source_class="controlled_document",
                    population_type="registry",
                    operation="source_inventory",
                    coverage={
                        "population_count": 0,
                        "returned_count": 0,
                        "complete_population": True,
                        "display_truncated": False,
                    },
                ),
            }
        }
    }


def test_unavailable_registry_is_not_a_complete_zero(monkeypatch):
    """A named source refusal dominates any attached data before facts exist."""
    result = _empty_registry()
    result.update(unavailable=True, code="source_authority_unavailable")
    monkeypatch.setattr(source_gateway, "inventory", lambda _actor: result)
    store = EvidenceStore()
    with pytest.raises(AnalysisRetrievalIncomplete) as raised:
        executor._retrieve_inventory(object(), store)
    assert raised.value.code == "capability_boundary"
    assert not store.facts
    assert not store.calculations


def test_authorized_complete_empty_registry_retains_its_real_zero(monkeypatch):
    """A useful opposite control separates source unavailability from emptiness."""
    result = _empty_registry()
    monkeypatch.setattr(source_gateway, "inventory", lambda _actor: result)
    store = EvidenceStore()
    assert executor._retrieve_inventory(object(), store) is result
    assert len(store.calculations) == 1
    calculation = next(iter(store.calculations.values()))
    assert calculation.complete_population is True
    assert calculation.rendered_values()["count"] == "0"


@pytest.mark.parametrize("population,truncated", [(1, False), (2, True)])
def test_complete_nonempty_registry_keeps_full_count_and_display_limits(
    monkeypatch, population, truncated
):
    """A metadata-qualified native count is not replaced by displayed row count."""
    result = _empty_registry()
    section = result["sections"]["controlled_documents"]
    section.update(
        documents=[
            {
                "document_id": "SYN-MANUAL",
                "title": "Synthetic manual",
                "current": {"revision": "C"},
                "source_state": {"indexed": True, "current": True},
            }
        ],
        population_count=population,
        returned_count=1,
        display_truncated=truncated,
    )
    section["retrieval"]["coverage"].update(
        population_count=population,
        returned_count=1,
        display_truncated=truncated,
    )
    monkeypatch.setattr(source_gateway, "inventory", lambda _actor: result)
    store = EvidenceStore()
    assert executor._retrieve_inventory(object(), store) is result
    calculation = next(iter(store.calculations.values()))
    assert calculation.rendered_values()["count"] == str(population)
    assert calculation.complete_population is True
    coverage = store.coverage_meta()
    assert coverage is not None
    assert coverage["display_truncated"] is truncated
    assert any(fact.source_id == "SYN-MANUAL" for fact in store.facts.values())


@pytest.mark.parametrize(
    "fault",
    [
        "missing-sections",
        "missing-section",
        "section-unavailable",
        "missing-count",
        "boolean-count",
        "string-count",
        "negative-count",
        "missing-envelope",
        "incomplete-envelope",
        "count-mismatch",
        "missing-returned",
        "non-list-documents",
    ],
)
def test_unqualified_registry_frames_never_create_complete_counts(monkeypatch, fault):
    """Missing or contradictory metadata cannot be repaired into complete zero."""
    result = _empty_registry()
    section = result["sections"]["controlled_documents"]
    if fault == "missing-sections":
        result.pop("sections")
    elif fault == "missing-section":
        result["sections"].pop("controlled_documents")
    elif fault == "section-unavailable":
        section["unavailable"] = True
    elif fault == "missing-count":
        section.pop("population_count")
    elif fault == "boolean-count":
        section["population_count"] = True
    elif fault == "string-count":
        section["population_count"] = "0"
    elif fault == "negative-count":
        section["population_count"] = -1
    elif fault == "missing-envelope":
        section.pop("retrieval")
    elif fault == "incomplete-envelope":
        section["retrieval"]["coverage"]["complete_population"] = False
    elif fault == "count-mismatch":
        section["population_count"] = 1
    elif fault == "missing-returned":
        section.pop("returned_count")
    elif fault == "non-list-documents":
        section["documents"] = {}
    monkeypatch.setattr(source_gateway, "inventory", lambda _actor: result)
    store = EvidenceStore()
    with pytest.raises(AnalysisRetrievalIncomplete) as raised:
        executor._retrieve_inventory(object(), store)
    assert raised.value.code == "capability_boundary"
    assert not store.facts
    assert not store.calculations


@pytest.mark.parametrize("fault_stage", ["none", "scoped", "fleet", "all"])
def test_failed_document_search_is_not_semantic_absence(monkeypatch, fault_stage):
    """Actual fallback preserves faults; callbacks/scope are synthetic here."""
    from ai.core.contracts.retrieval import NO_RELEVANT_PASSAGE

    assets = source_gateway.AssetSet(
        machines=((41, "Synthetic machine", "SYN-41"),),
        serials=frozenset({"SYN-41"}),
        serial_less=(),
        warnings=(),
    )
    monkeypatch.setattr(source_gateway, "resolve_asset_set", lambda *_args: assets)
    calls = []

    def search(**kwargs):
        stage = "fleet" if kwargs.get("fleet_wide") else "scoped"
        calls.append(stage)
        if fault_stage in (stage, "all"):
            raise RuntimeError("synthetic-private-provider-fault")
        return {"chunks": [], "returned_count": 0}

    result = source_gateway.retrieve_manual_fact(
        object(), query="Synthetic seal guidance", corpus_search=search
    )
    assert calls == ["scoped", "fleet"]
    assert result["chunks"] == []
    assert (result.get("unavailable") is True) is (fault_stage != "none")
    assert (NO_RELEVANT_PASSAGE in result["warnings"]) is (fault_stage == "none")
    if fault_stage != "none":
        assert "retrieval_unavailable" in result["warnings"]
    assert "synthetic-private-provider-fault" not in json.dumps(result)


@pytest.mark.parametrize("source_fault", [False, True])
def test_manual_search_fault_and_no_match_remain_distinct_on_the_wire(monkeypatch, source_fault):
    """Exercise real gateway/executor/turn emitter; source/actor are scripted."""
    import asyncio

    from ai.core import config
    from ai.core.integrations import attachment_corpus
    from ai.core.tests.test_analysis_executor import RecordingEmitter
    from ai.core.tests.test_analysis_wire_capture import _run, _service, _settings, _translate_all
    from ai.core.turn.execution import _run_analysis_branch

    assets = source_gateway.AssetSet(
        machines=((41, "Synthetic machine", "SYN-41"),),
        serials=frozenset({"SYN-41"}),
        serial_less=(),
        warnings=(),
    )
    monkeypatch.setattr(source_gateway, "resolve_asset_set", lambda *_args: assets)
    calls = []

    def failing_search(**kwargs):
        calls.append("fleet" if kwargs.get("fleet_wide") else "scoped")
        if source_fault:
            raise RuntimeError("synthetic-private-provider-fault")
        return {"chunks": []}

    def empty_attachments(**_kwargs):
        calls.append("attachments")
        return {"chunks": []}

    original = source_gateway.retrieve_manual_fact
    monkeypatch.setattr(
        source_gateway,
        "retrieve_manual_fact",
        lambda actor, **kwargs: original(actor, corpus_search=failing_search, **kwargs),
    )
    monkeypatch.setattr(attachment_corpus, "search_corpus_attachments", empty_attachments)
    monkeypatch.setattr(config, "get_settings", lambda: _settings("enforce"))
    synthesis_calls = []
    monkeypatch.setattr(
        executor, "synthesize_claims", lambda *_args, **_kwargs: synthesis_calls.append("synthesis")
    )
    emitter = RecordingEmitter()
    run = _run(intent="manual_fact", emitter=emitter)
    canonical = asyncio.run(_run_analysis_branch(_service(), run))
    assert calls == ["scoped", "fleet", "attachments"]
    if source_fault:
        assert synthesis_calls == []
        assert canonical["response_state"] == "incomplete"
        assert canonical["evidence_analysis"]["no_data_reason"] == "capability_boundary"
        assert canonical["evidence_analysis"]["claims"] == []
        assert canonical["evidence_analysis"]["citations"] == []
    else:
        assert synthesis_calls == ["synthesis"]
        assert canonical["response_state"] == "complete"
        assert canonical["evidence_analysis"]["no_data_reason"] is None
        assert canonical["evidence_analysis"]["coverage"]["complete_population"] is False
        assert canonical["evidence_analysis"]["claims"]
    assert run.extras["evidence_sets"] == []
    assert "synthetic-private-provider-fault" not in json.dumps(canonical)
    assert "synthetic-private-provider-fault" not in json.dumps(emitter.events)
    assert "synthetic-private-provider-fault" not in _translate_all(emitter.events)
