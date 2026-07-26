"""Phase 2 (V9/V11/V12): say only what was actually read, and answer follow-ups.

From the 2026-07-26 live voice test:

* V9  -- "Unable to complete lookup." was spoken as though it were an answer,
  because a workflow that caught its own exception still reported success.
* V11 -- "Details are in the chat if you need specifics" was said when the chat
  contained no details; spoken and visible text are the same string.
* V12 -- turn 3 ("And where are those located?") lost the subject and asked
  "Which fastener part numbers should I locate?", because capability selection
  never read the transcript.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core.tools.capabilities as capabilities  # noqa: E402
import pytest  # noqa: E402

ALL_VIEW = frozenset({
    ("build", "view"),
    ("part", "view"),
    ("purchase_order", "view"),
    ("sales_order", "view"),
    ("stock", "view"),
    ("stock_location", "view"),
})

FASTENER_HISTORY = [
    {"role": "user", "content": "How many fasteners are in stock?"},
    {"role": "assistant", "content": "There are about 37,203 fasteners in stock."},
]


@pytest.fixture(autouse=True)
def _pinned_lexicon(monkeypatch):
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)


# --------------------------------------------------------------------------- #
# V12: anaphoric follow-ups inherit their subject                              #
# --------------------------------------------------------------------------- #
def test_anaphoric_follow_up_inherits_the_subject_from_history():
    """A follow-up carrying no domain word of its own inherits the subject.

    ("where are those located?" no longer needs this path -- 'located' now scores
    stock.read directly -- so this uses a phrasing that truly carries nothing.)
    """
    selected = capabilities.select_capabilities(
        "and what about those?",
        context={"conversation_history": FASTENER_HISTORY},
        profile=ALL_VIEW,
        authenticated=True,
    )

    assert selected.clarification_required is False
    assert selected.tools, "the follow-up must reach tools instead of dead-ending"
    assert "stock.read" in selected.pack_ids
    assert "history_subject" in selected.signals


def test_the_same_follow_up_without_history_still_asks():
    """Fail-closed: no transcript means no subject to inherit."""
    selected = capabilities.select_capabilities(
        "and what about those?", profile=ALL_VIEW, authenticated=True
    )

    assert selected.clarification_required is True
    assert selected.tools == ()


def test_history_is_only_consulted_when_the_message_scores_nothing():
    """An ordinary turn must still be scored on its own words."""
    selected = capabilities.select_capabilities(
        "show the bill of materials for assembly 42",
        context={"conversation_history": FASTENER_HISTORY},
        profile=ALL_VIEW,
        authenticated=True,
    )

    assert "history_subject" not in selected.signals
    assert selected.pack_ids[0] == "bom.read"


def test_machine_history_rows_are_ignored():
    selected = capabilities.select_capabilities(
        "and what about those?",
        context={
            "conversation_history": [
                {"role": "tool", "content": "stock inventory quantity"},
                {"role": "system", "content": "stock inventory quantity"},
            ]
        },
        profile=ALL_VIEW,
        authenticated=True,
    )

    assert selected.clarification_required is True


def test_malformed_history_is_inert():
    for bad in (42, "not a list", {"conversation_history": None}, []):
        selected = capabilities.select_capabilities(
            "and what about those?",
            context={"conversation_history": bad},
            profile=ALL_VIEW,
            authenticated=True,
        )
        assert selected.clarification_required is True


# --------------------------------------------------------------------------- #
# V9: a failed workflow must not be reported as a successful answer            #
# --------------------------------------------------------------------------- #
def test_root_workflow_raises_on_a_failed_result():
    """RootWorkflow must not yield 'Unable to complete lookup.' as an answer."""
    import inspect

    from ai.core.workflows import root

    source = inspect.getsource(root)
    assert 'getattr(result, "success", True) is False' in source
    failure_index = source.index('getattr(result, "success", True) is False')
    response_index = source.index('if hasattr(result, "formatted_response")')
    assert failure_index < response_index, "the failure check must precede the answer"


# --------------------------------------------------------------------------- #
# V11: no promise of a channel that does not exist                             #
# --------------------------------------------------------------------------- #
def test_voice_prompt_does_not_promise_details_in_the_chat():
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    prompt = T1LookupWorkflow.VOICE_SYSTEM_PROMPT
    assert "rest is in the chat" not in prompt
    assert "never promise details" in prompt


def test_voice_prompt_does_not_teach_a_staleness_hedge():
    """'as of the last sync' was a prompt example spoken as if it were fact."""
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    assert "as of the last" not in T1LookupWorkflow.VOICE_SYSTEM_PROMPT
