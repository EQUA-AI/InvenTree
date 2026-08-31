"""R5 WP-E: the shared query builder — kwargs pins and the wildcard guard."""

import pytest
from ai.core.integrations.search_query import (
    HNSW_NAME,
    SEMANTIC_CONFIG,
    VECTOR_PROFILE,
    filter_only_kwargs,
    semantic_hybrid_kwargs,
)


def test_wiring_names_are_pinned():
    """The index builder and every call site share these exact names."""
    assert SEMANTIC_CONFIG == "semantic-default"
    assert VECTOR_PROFILE == "vector-default"
    assert HNSW_NAME == "hnsw-default"


def test_semantic_hybrid_kwargs_shape_is_the_pre_r5_wire():
    """Every invariant kwarg matches the four pre-builder call sites."""
    kwargs = semantic_hybrid_kwargs(
        query="seal replacement",
        vector=[0.5, 0.25],
        vector_field="media_vector",
        filter_expression="is_current eq true",
        select=["id", "caption"],
        top=5,
    )
    vq = kwargs.pop("vector_queries")
    assert kwargs == {
        "search_text": "seal replacement",
        "vector_filter_mode": "preFilter",
        "filter": "is_current eq true",
        "top": 5,
        "query_type": "semantic",
        "semantic_configuration_name": "semantic-default",
        "select": ["id", "caption"],
    }
    assert len(vq) == 1
    assert vq[0].fields == "media_vector"
    assert vq[0].k_nearest_neighbors == 5
    assert vq[0].vector == [0.5, 0.25]


@pytest.mark.parametrize("bad", ["", "   ", "*", " * "])
def test_semantic_guard_refuses_blank_and_wildcard(bad):
    """Wildcard-plus-semantic is unconstructible, not merely discouraged."""
    with pytest.raises(ValueError):
        semantic_hybrid_kwargs(
            query=bad,
            vector=[0.5],
            vector_field="text_vector",
            filter_expression="x",
            select=["id"],
            top=3,
        )


def test_filter_only_kwargs_never_carries_query_type():
    """The fetch shape for expansions: wildcard text, no ranking knobs."""
    kwargs = filter_only_kwargs(
        filter_expression="attachment_id eq 7",
        select=["id"],
        top=10,
        order_by=["timecode_start_s asc"],
    )
    assert kwargs == {
        "search_text": "*",
        "filter": "attachment_id eq 7",
        "top": 10,
        "select": ["id"],
        "order_by": ["timecode_start_s asc"],
    }
    assert "query_type" not in kwargs
    minimal = filter_only_kwargs(filter_expression="x", select=["id"], top=1)
    assert "order_by" not in minimal


def test_index_command_imports_the_shared_names():
    """create_rag_search_indexes must consume, not redefine, the names."""
    import importlib

    module = importlib.import_module("aichat.management.commands.create_rag_search_indexes")
    assert module.SEMANTIC_CONFIG is SEMANTIC_CONFIG
    assert module.VECTOR_PROFILE is VECTOR_PROFILE
    assert module.HNSW_NAME is HNSW_NAME
