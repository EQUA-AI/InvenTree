"""S4 producer-shaped document controls; transport/authority are scripted.

These exercise real projection/gateway/fact/validator code, not real Search,
authentication, historical attribution or final deployed emission.
"""

import hashlib
from types import SimpleNamespace

import pytest
from ai.core.analysis import executor, source_gateway
from ai.core.analysis.evidence import EvidenceStore
from ai.core.analysis.renderer import assign_ordinals
from ai.core.analysis.schemas import AnalysisClaim
from ai.core.analysis.validator import check_applicability
from ai.core.integrations import attachment_corpus, projection_gate, retrieval_authority


def test_actual_attachment_fallback_keeps_upload_classification(monkeypatch):
    """An admitted upload never becomes a controlled manual through fallback."""
    from ai.core import config
    from tasks import scope

    text = "Synthetic uploaded evidence, not a controlled procedure."
    digest = hashlib.sha256(b"synthetic original document bytes").hexdigest()
    row = {
        "id": "synthetic-attachment-17-chunk-1",
        "attachment_id": 17,
        "model_type": "assetmachine",
        "model_id": 41,
        "asset_id": "SYN-41",
        "content": text,
        "source_sha256": digest,
        "source_file_name": "synthetic-upload.txt",
        "display_title": "Synthetic upload",
        "section_path": "Observed note",
        "page_number": 1,
        "access_class": "attachment_uploaded",
        "client_codes": ["synthetic-client"],
        "embedding_profile": "v1",
        "@search.score": 1.0,
    }
    calls = []

    class Embedding:
        def embed_query(self, query):
            calls.append(("embedding", query))
            return [0.0] * 1536

    class Search:
        def search(self, **kwargs):
            calls.append(("search", kwargs))
            return [dict(row)]

    settings = SimpleNamespace(
        feature_attachment_rag_retrieval=True,
        single_site_policy_key="synthetic-policy",
        feature_question_cards=False,
        cohere_embed_model="embed-v-4-0",
        cohere_embed_dimensions=1536,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scope, "client_codes_for_actor", lambda _actor: frozenset({"synthetic-client"})
    )
    monkeypatch.setattr(attachment_corpus, "_granted_arms", lambda _actor: ("assetmachine",))
    monkeypatch.setattr(projection_gate, "recall_allowed", lambda _corpus: True)
    monkeypatch.setattr(retrieval_authority, "attachment_rows", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(attachment_corpus, "_default_embedding_client", Embedding)
    monkeypatch.setattr(attachment_corpus, "_default_search_client", Search)
    assets = source_gateway.AssetSet(
        machines=((41, "Synthetic machine", "SYN-41"),),
        serials=frozenset({"SYN-41"}),
        serial_less=(),
        warnings=(),
    )
    monkeypatch.setattr(source_gateway, "resolve_asset_set", lambda _actor, _ids=None: assets)
    original = source_gateway.retrieve_manual_fact

    def retrieve(actor, **kwargs):
        return original(actor, corpus_search=lambda **_kwargs: {"chunks": []}, **kwargs)

    monkeypatch.setattr(source_gateway, "retrieve_manual_fact", retrieve)
    store = EvidenceStore()
    result = executor._retrieve_manual(object(), store, query="What does the uploaded note say?")
    assert result["source_class"] == "asset_attachment"
    assert result["labels"] == ["uncontrolled_attachment"]
    assert [name for name, _value in calls] == ["embedding", "search"]
    assert len(store.facts) == 1
    fact = next(iter(store.facts.values()))
    assert fact.source_class == "asset_attachment"
    assert fact.controlled is False
    assert fact.source_id == "17"
    assert fact.source_revision == digest
    assert fact.locator["model_type"] == "assetmachine"
    assert fact.locator["model_id"] == 41
    assert fact.locator["source_sha256"] == digest
    assert fact.locator["excerpt_hash"] == hashlib.sha256(text.encode()).hexdigest()
    claim = AnalysisClaim.model_validate_json(
        '{"claim_id":"synthetic-claim","claim_role":"answer",'
        '"claim_type":"direct_source_fact","evidence_classification":"documented",'
        '"fact_refs":["' + fact.fact_id + '"],'
        '"calculation_output_refs":[],"evidence_refs":[],"entity_refs":[],'
        '"render_template":"analysis.manual_passage_fact","paraphrase":"follow this procedure"}'
    )
    assert "uncontrolled_source" in {item.code for item in check_applicability([claim], store)}
    citation = assign_ordinals([claim], store).entries[0]
    assert citation.source_class == "asset_attachment"
    assert citation.controlled is False
    assert citation.source_id == "17"
    assert citation.source_revision == digest
    assert citation.locator == fact.locator


def test_actual_controlled_projection_remains_a_controlled_manual(monkeypatch):
    """The opposite native projection remains usable, with literal revision."""
    from ai.core import config
    from ai.core.integrations import controlled_document_corpus as corpus

    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(single_site_policy_key="synthetic-policy")
    )
    monkeypatch.setattr(retrieval_authority, "controlled_rows", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(corpus, "_hits_verified_applicable", lambda _rows, **_kwargs: False)
    monkeypatch.setattr(corpus, "_record_search_outcome", lambda **_kwargs: None)
    calls = []

    class Embedding:
        def embed_batch(self, inputs):
            calls.append("embedding")
            return [[0.0] * 3072]

    class Search:
        def search(self, **kwargs):
            calls.append("search")
            return [
                {
                    "id": "synthetic-controlled-chunk",
                    "document_id": "SYN-MANUAL",
                    "document_revision": "revision-C",
                    "chunk_id": "synthetic-controlled-chunk",
                    "chunk": "Synthetic controlled passage.",
                }
            ]

    assets = source_gateway.AssetSet(
        machines=((41, "Synthetic machine", "SYN-41"),),
        serials=frozenset({"SYN-41"}),
        serial_less=(),
        warnings=(),
    )
    monkeypatch.setattr(source_gateway, "resolve_asset_set", lambda _actor, _ids=None: assets)
    original = source_gateway.retrieve_manual_fact

    def retrieve(actor, **kwargs):
        return original(
            actor,
            corpus_search=lambda **search_kwargs: corpus.search_corpus(
                search_client=Search(), embedding_client=Embedding(), **search_kwargs
            ),
            **kwargs,
        )

    monkeypatch.setattr(source_gateway, "retrieve_manual_fact", retrieve)
    store = EvidenceStore()
    executor._retrieve_manual(object(), store, query="What does the controlled manual say?")
    assert calls == ["embedding", "search"]
    assert len(store.facts) == 1
    fact = next(iter(store.facts.values()))
    assert fact.source_class == "controlled_document"
    assert fact.source_id == "SYN-MANUAL"
    assert fact.source_revision == "revision-C"
    assert fact.controlled is True
    claim = AnalysisClaim.model_validate({
        "claim_id": "controlled-positive",
        "claim_role": "answer",
        "claim_type": "direct_source_fact",
        "evidence_classification": "documented",
        "fact_refs": [fact.fact_id],
        "calculation_output_refs": [],
        "evidence_refs": [],
        "entity_refs": [],
        "render_template": "analysis.manual_passage_fact",
        "paraphrase": "documented guidance",
    })
    assert "uncontrolled_source" not in {item.code for item in check_applicability([claim], store)}
    citation = assign_ordinals([claim], store).entries[0]
    assert citation.controlled is True
    assert citation.source_class == "controlled_document"
    assert citation.source_revision == "revision-C"


@pytest.mark.parametrize("fault", ["unknown-class", "boolean-id", "missing-hash", "mixed-class"])
def test_invalid_retrieved_citations_never_mint_controlled_facts(monkeypatch, fault):
    """These additional guards qualify working behavior, not native admission."""
    result = {
        "source_class": "asset_attachment",
        "chunks": [
            {
                "citation": {
                    "attachment_id": 17,
                    "model_type": "assetmachine",
                    "model_id": 41,
                    "source_sha256": "a" * 64,
                    "excerpt_hash": "b" * 64,
                    "chunk_id": "synthetic-chunk",
                    "access_class": "attachment_uploaded",
                }
            }
        ],
    }
    citation = result["chunks"][0]["citation"]
    if fault == "unknown-class":
        result["source_class"] = "unknown"
    elif fault == "boolean-id":
        citation["attachment_id"] = True
    elif fault == "missing-hash":
        citation.pop("source_sha256")
    elif fault == "mixed-class":
        result["source_class"] = "controlled_document"
        citation.update(document_id="synthetic-forged-manual", revision="C")
    monkeypatch.setattr(source_gateway, "retrieve_manual_fact", lambda *_args, **_kwargs: result)
    store = EvidenceStore()
    with pytest.raises(ValueError):
        executor._retrieve_manual(object(), store, query="Synthetic query")
    assert not store.facts
