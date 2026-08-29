"""S8a WP-B3: the §8.4 fallback orchestrator — class may change, scope never."""

# ruff: noqa: E402

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.analysis import source_gateway
from ai.core.analysis.source_gateway import (
    AmbiguousDocumentRef,
    AssetSet,
    retrieve_manual_fact,
)
from ai.core.contracts.retrieval import NO_RELEVANT_PASSAGE
from ai.core.integrations.controlled_document_corpus import corpus_filter


def _asset_set(serials=("SN-1", "SN-2"), machines=None):
    machines = machines or tuple(
        (index + 1, f"Machine {index + 1}", serial) for index, serial in enumerate(serials)
    )
    return AssetSet(
        machines=machines,
        serials=frozenset(s for s in serials if s),
        serial_less=tuple(name for _, name, serial in machines if not serial),
        warnings=(),
    )


class _RecordingSearch:
    """A fake corpus search recording exactly what each step asked for."""

    def __init__(self, hits_by_step: dict[str, int] | None = None):
        self.calls: list[dict] = []
        self.hits_by_step = hits_by_step or {}

    def __call__(self, **kwargs):
        step = "fleet" if kwargs.get("fleet_wide") else "scoped"
        self.calls.append(kwargs)
        count = self.hits_by_step.get(step, 0)
        return {"chunks": [{"excerpt": "x"}] * count, "returned_count": count}


def test_fallback_freezes_the_asset_set_and_never_widens() -> None:
    """Every recorded step used the step-1 serial set (or the labeled floor)."""
    corpus = _RecordingSearch()
    attachment_calls: list[dict] = []

    def attachment_search(**kwargs):
        attachment_calls.append(kwargs)
        return {"chunks": []}

    with mock.patch.object(source_gateway, "resolve_asset_set", return_value=_asset_set()):
        result = retrieve_manual_fact(
            user=object(),
            query="seal torque",
            machine_ids=[1, 2],
            corpus_search=corpus,
            pinned_search=mock.Mock(),
            attachment_search=attachment_search,
        )

    # Step 2: the frozen serials, explicitly. Step 4: fleet-wide labeled,
    # never a widened serial set.
    assert corpus.calls[0]["asset_ids"] == ("SN-1", "SN-2")
    assert corpus.calls[1].get("fleet_wide") is True
    assert "asset_ids" not in corpus.calls[1] or corpus.calls[1]["asset_ids"] is None
    # Step 5: attachments ride the SAME asset set.
    assert attachment_calls[0]["scope_asset_ids"] == ("SN-1", "SN-2")
    steps = [attempt["step"] for attempt in result["attempts"]]
    assert steps == [
        "exact_asset_controlled",
        "fleet_wide_controlled",
        "asset_attachments",
        "thread_uploads",
    ]
    assert NO_RELEVANT_PASSAGE in result["warnings"]


def test_serial_less_scoped_machines_resolve_nothing_and_call_nothing() -> None:
    """Applicability unresolved: zero searches, typed miss."""
    corpus = mock.Mock()
    with mock.patch.object(
        source_gateway,
        "resolve_asset_set",
        return_value=_asset_set(serials=("",), machines=((5, "Unstamped", ""),)),
    ):
        result = retrieve_manual_fact(
            user=object(),
            query="seal torque",
            corpus_search=corpus,
            pinned_search=mock.Mock(),
        )
    corpus.assert_not_called()
    assert result["scope_miss"] is True
    assert result["applicability"] == "unresolved"


def test_first_hit_wins_and_labels_fleet_wide_results() -> None:
    corpus = _RecordingSearch(hits_by_step={"fleet": 2})
    with mock.patch.object(source_gateway, "resolve_asset_set", return_value=_asset_set()):
        result = retrieve_manual_fact(
            user=object(),
            query="site safety plan",
            corpus_search=corpus,
            pinned_search=mock.Mock(),
        )
    assert result["labels"] == ["fleet_wide_unverified_applicability"]
    assert result["source_class"] == "controlled_document"
    outcomes = {a["step"]: a["outcome"] for a in result["attempts"]}
    assert outcomes["exact_asset_controlled"] == "no_relevant_passage"
    assert outcomes["fleet_wide_controlled"] == "hit"


def test_pinned_revision_runs_first_and_short_circuits() -> None:
    pinned = mock.Mock(return_value={"chunks": [{"chunk": "text"}]})
    corpus = mock.Mock()
    with (
        mock.patch.object(source_gateway, "resolve_asset_set", return_value=_asset_set()),
        mock.patch.object(
            source_gateway,
            "resolve_selected_document",
            return_value=mock.Mock(document_id="doc-1"),
        ),
    ):
        result = retrieve_manual_fact(
            user=object(),
            query="torque values",
            document_ref="HX-200 Manual",
            corpus_search=corpus,
            pinned_search=pinned,
        )
    corpus.assert_not_called()
    assert result["labels"] == ["pinned_revision"]
    assert result["attempts"][0] == {
        "step": "pinned_revision",
        "outcome": "hit",
        "hit_count": 1,
    }


def test_ambiguous_document_ref_asks_instead_of_searching() -> None:
    corpus = mock.Mock()
    ambiguous = AmbiguousDocumentRef(
        candidates=(("doc-1", "Manual A", "1"), ("doc-2", "Manual B", "2"))
    )
    with (
        mock.patch.object(source_gateway, "resolve_asset_set", return_value=_asset_set()),
        mock.patch.object(source_gateway, "resolve_selected_document", return_value=ambiguous),
    ):
        result = retrieve_manual_fact(
            user=object(),
            query="torque values",
            document_ref="Manual",
            corpus_search=corpus,
            pinned_search=mock.Mock(),
        )
    corpus.assert_not_called()
    assert result["machine_filter"] == "ambiguous"
    assert [c["document_id"] for c in result["document_candidates"]] == ["doc-1", "doc-2"]


def test_unresolved_pin_degrades_visibly_to_the_corpus() -> None:
    corpus = _RecordingSearch(hits_by_step={"scoped": 1})
    with (
        mock.patch.object(source_gateway, "resolve_asset_set", return_value=_asset_set()),
        mock.patch.object(source_gateway, "resolve_selected_document", return_value=None),
    ):
        result = retrieve_manual_fact(
            user=object(),
            query="torque values",
            document_ref="Nonexistent Doc",
            corpus_search=corpus,
            pinned_search=mock.Mock(),
        )
    assert result["attempts"][0]["outcome"] == "pin_unresolved"
    assert result["attempts"][1]["outcome"] == "hit"


def test_corpus_filter_fleet_wide_is_the_blank_stamp_clause() -> None:
    """§8.4 step 4: site-wide selection is asset_id eq '' — never unfiltered."""
    expression = corpus_filter(scope_key="site-a", fleet_wide=True)
    assert "asset_id eq ''" in expression
    assert "search.in(asset_id" not in expression
    # fleet_wide wins over an accidental asset set (a programming error must
    # narrow, not widen).
    both = corpus_filter(scope_key="site-a", fleet_wide=True, asset_ids=("SN-1",))
    assert "asset_id eq ''" in both
    assert "SN-1" not in both
