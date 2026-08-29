"""S8a WP-B2: inventory questions reach the registry tool; content keeps its path."""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.analysis.intent import (  # noqa: E402
    TaskIntent,
    classify_rules,
    is_source_inventory_question,
)
from ai.core.tools import capabilities  # noqa: E402
from ai.core.tools.capabilities import (  # noqa: E402
    MAX_INITIAL_TOOLS,
    select_capabilities,
)

PROFILE = frozenset({
    ("work_order", "view"),
    ("part", "view"),
    ("stock", "view"),
})

INVENTORY_QUESTION = "What manuals do you have for the HX-200?"
CONTENT_QUESTION = "What does the manual say about torque values for the pump?"


@pytest.fixture(autouse=True)
def _pinned_lexicons(monkeypatch):
    """Selection must route on static vocabulary, never live data."""
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)
    monkeypatch.setattr(capabilities, "machine_lexicon", frozenset)


def test_inventory_shape_makes_sources_primary_and_survives_trim():
    """The rider PREPENDS sources.read; position 0 is trim-protected."""
    selection = select_capabilities(INVENTORY_QUESTION, profile=PROFILE, authenticated=True)
    assert selection.pack_ids[0] == "sources.read"
    assert "list_document_sources" in selection.tool_ids
    assert "source_inventory_shape" in selection.signals
    assert len(selection.tool_ids) <= MAX_INITIAL_TOOLS


def test_content_question_is_never_hijacked():
    """ "What does the manual say" keeps its exact pre-S8a selection."""
    selection = select_capabilities(CONTENT_QUESTION, profile=PROFILE, authenticated=True)
    assert selection.pack_ids[0] != "sources.read"
    assert "list_document_sources" not in selection.tool_ids
    assert "search_manuals" in selection.tool_ids


def test_typed_intent_path_selects_the_registry_tool_first():
    """S3's typed-intent selection now leads with sources.read."""
    selection = select_capabilities(
        "which documents are available for this machine?",
        profile=PROFILE,
        authenticated=True,
        task_intent="source_inventory",
    )
    assert selection.pack_ids[0] == "sources.read"
    assert "list_document_sources" in selection.tool_ids


def test_intent_and_selection_share_one_shape():
    """The router/broker predicate IS the classifier's inventory shape."""
    assert is_source_inventory_question(INVENTORY_QUESTION)
    decision = classify_rules(INVENTORY_QUESTION)
    assert decision is not None
    assert decision.intent is TaskIntent.SOURCE_INVENTORY
    assert not is_source_inventory_question(CONTENT_QUESTION)


def test_router_fast_paths_inventory_before_semantic_search():
    """The misroute S8a fixes: registry questions never reach similarity."""
    from ai.core.agents.routing import (
        UnifiedRouter,
        is_document_inventory_question,
    )

    assert is_document_inventory_question(INVENTORY_QUESTION)
    assert not is_document_inventory_question(CONTENT_QUESTION)
    # Action requests are guarded even when they name documents.
    assert not is_document_inventory_question("Please process the uploaded documents you have")

    router = UnifiedRouter()
    decision = asyncio.run(router.route(INVENTORY_QUESTION, "thread_x"))
    assert decision.reasoning == "Document inventory question"

    content_decision = asyncio.run(router.route(CONTENT_QUESTION, "thread_x"))
    assert content_decision.reasoning == "Explicit existing-document lookup"


def test_voice_twin_consults_the_inventory_predicate():
    """Voice routing shares the predicate rather than growing its own."""
    import inspect

    from ai.core.agents import voice_routing

    source = inspect.getsource(voice_routing)
    assert "is_document_inventory_question" in source
