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


@pytest.mark.parametrize(
    "question",
    [
        "Which revision of its service manual is current?",
        "And the superseded one?",
        "What about the superseded version?",
        "Is there a newer fleet bulletin that applies to both of them?",
        "Are there current documents for these machines?",
    ],
)
def test_registry_metadata_questions_keep_history_and_fleet_sources(question):
    assert is_source_inventory_question(question)
    decision = classify_rules(question)
    assert decision is not None and decision.intent is TaskIntent.SOURCE_INVENTORY
    selection = select_capabilities(question, profile=PROFILE, authenticated=True)
    assert selection.tool_ids == ("list_document_sources",)


def test_registry_turn_does_not_reintroduce_content_search_from_sticky_packs(monkeypatch):
    monkeypatch.setattr(capabilities, "stable_tool_prefix_enabled", lambda: True)
    monkeypatch.setattr(capabilities, "selection_v2_enabled", lambda: True)
    monkeypatch.setattr(
        capabilities, "_sticky_packs", lambda *_args, **_kwargs: ("manuals.read", "documents.read")
    )
    selection = select_capabilities(
        "List available nameplate photos",
        profile=PROFILE,
        authenticated=True,
        task_intent="source_inventory",
    )
    assert selection.tool_ids == ("list_document_sources",)
    assert "registry_inventory_only" in selection.signals


@pytest.mark.parametrize(
    "question",
    [
        "What does the current manual say about bolt torque?",
        "Which revision specifies the new tightening procedure?",
        "Show the difference in torque between revisions A and B.",
    ],
)
def test_content_and_revision_comparison_questions_keep_content_tools(question):
    assert not is_source_inventory_question(question)
    selection = select_capabilities(
        question, profile=PROFILE, authenticated=True, task_intent="manual_fact"
    )
    assert "search_manuals" in selection.tool_ids


def test_registry_only_selection_still_requires_authenticated_work_order_role():
    for profile, authenticated in ((frozenset(), True), (PROFILE, False)):
        selection = select_capabilities(
            "And the superseded one?",
            profile=profile,
            authenticated=authenticated,
            task_intent="source_inventory",
        )
        assert not selection.tool_ids


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
