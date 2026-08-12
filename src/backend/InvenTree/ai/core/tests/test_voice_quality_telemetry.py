"""Phase 4/5 (V21, V23, V24, V16, V25): vocabulary, search, language, telemetry.

From the 2026-07-26 live voice test:

* V21 -- "Check for all jobs" produced "I can't access job workflow details
  directly by voice", immediately after the assistant had summarised the board.
* V24 -- descriptive lookups missed because DRF ANDs every search token, so one
  article or filler word zeroes the result.
* V23 -- the assistant answered in Tagalog through an en-US voice, with no
  language policy stated anywhere.
* V16 -- 138 identical "Voice Live policy violation" warnings (89% of all log
  output) with the one field that would explain them discarded.
* V25 -- 2 of 36 turns were attributable to a workflow, both because they crashed.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core.tools.capabilities as capabilities  # noqa: E402
from ai.core.integrations.inventree.client import _search_terms  # noqa: E402

ALL_VIEW = frozenset({
    ("build", "view"),
    ("part", "view"),
    ("purchase_order", "view"),
    ("sales_order", "view"),
    ("stock", "view"),
    ("stock_location", "view"),
})


@pytest.fixture(autouse=True)
def _pinned_lexicon(monkeypatch):
    monkeypatch.setattr(capabilities, "category_lexicon", frozenset)


# --------------------------------------------------------------------------- #
# V21: the floor calls kanban cards "jobs"                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "query",
    [
        "check for all jobs",
        "what jobs are open?",
        "show me the tasks in progress",
        "how many jobs are on the board?",
    ],
)
def test_job_vocabulary_reaches_the_kanban_pack(query):
    selected = capabilities.select_capabilities(
        query,
        profile=ALL_VIEW | frozenset({("kanban", "view")}),
        authenticated=True,
    )

    assert "kanban.read" in selected.pack_ids, (query, selected.pack_ids)


def test_work_order_still_prefers_the_build_pack():
    """'work order' belongs to build.read; the new terms must not steal it."""
    selected = capabilities.select_capabilities(
        "show me work order WO-42",
        profile=ALL_VIEW | frozenset({("kanban", "view")}),
        authenticated=True,
    )

    assert selected.pack_ids[0] == "build.read"


# --------------------------------------------------------------------------- #
# V24: DRF ANDs every search token                                             #
# --------------------------------------------------------------------------- #
def test_search_drops_filler_that_would_zero_the_result():
    # The spoken question that failed in production.
    assert _search_terms("what is the stock level for ceramic capacitor 100pf 0402") == (
        "ceramic capacitor 100pf 0402"
    )


def test_search_keeps_identifiers_intact():
    assert _search_terms("C_100pF_0402") == "C_100pF_0402"
    assert _search_terms("show me part ABC-123") == "ABC-123"


def test_search_never_reduces_a_query_to_nothing():
    """If every token is a stopword, search the original rather than nothing."""
    assert _search_terms("what is the stock level") != ""
    assert _search_terms("") == ""


# --------------------------------------------------------------------------- #
# V23: language policy is stated, identifiers are never translated             #
# --------------------------------------------------------------------------- #
def test_voice_prompt_states_a_language_policy():
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    prompt = T1LookupWorkflow.VOICE_SYSTEM_PROMPT
    assert "language the technician used" in prompt
    assert "never translate an identifier" in prompt


# --------------------------------------------------------------------------- #
# V16/V25: telemetry that can answer "what happened?"                          #
# --------------------------------------------------------------------------- #
def test_policy_violation_log_names_the_event():
    import inspect

    from ai.core.voice import gateway

    source = inspect.getsource(gateway)
    assert "voice.policy_violation" in source
    assert "event_type=%s" in source


def test_turn_telemetry_is_rendered_not_passed_as_extra():
    """stdlib logging discards extra={}; the fields must be in the message."""
    import inspect

    from ai.core import turn_service

    source = inspect.getsource(turn_service.NormalizedTurnService._process_turn)
    assert "ai.turn modality=%s" in source
    for field in ("workflow=", "route=", "state=", "duration_ms="):
        assert field in source


def test_turn_telemetry_carries_no_transcript():
    """Attribution must never leak what was said."""
    import inspect

    from ai.core import turn_service

    source = inspect.getsource(turn_service.NormalizedTurnService._process_turn)
    telemetry = source[source.index("ai.turn modality=%s") :][:600]
    for forbidden in ("content", "message", "spoken_summary", "query"):
        assert f"{forbidden}," not in telemetry
