"""S24: history replay budgets and the per-turn usage ledger.

The replay window renamed from turns to messages (it always counted
messages), gained char budgets so one huge answer cannot dominate prompt
payload, and both provider rails now record usage into a per-turn ledger
persisted through terminal metadata — so the budget defaults are measured,
not guessed.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.tests.test_luna_diagnostics import (  # noqa: E402
    _adapter,
    _Client,
    _envelope,
    _response,
)
from ai.core.turn_service import (  # noqa: E402
    _budgeted_history,
    _terminal_output_metadata,
)
from ai.core.usage import (  # noqa: E402
    TurnUsageLedger,
    bind_turn_usage,
    drain_turn_usage,
    record_usage,
    turn_usage_ledger,
)


def _messages(*contents: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": content} for content in contents]


class TestBudgetedHistory:
    """The pure replay-budget helper."""

    def test_within_budget_passes_through_unchanged(self) -> None:
        history = _messages("short question", "short answer")
        assert _budgeted_history(history, max_message_chars=4000, max_total_chars=24000) == history

    def test_oversized_message_keeps_its_head_with_a_visible_marker(self) -> None:
        history = _messages("A" * 50)
        budgeted = _budgeted_history(history, max_message_chars=10, max_total_chars=0)
        assert budgeted[0]["content"] == "A" * 10 + "… [truncated]"

    def test_total_budget_drops_oldest_whole_messages_first(self) -> None:
        history = _messages("O" * 100, "M" * 100, "N" * 100)
        budgeted = _budgeted_history(history, max_message_chars=0, max_total_chars=210)
        assert [entry["content"][0] for entry in budgeted] == ["M", "N"]

    def test_newest_two_messages_are_never_dropped(self) -> None:
        # The newest pair alone exceeds the total budget; dropping either
        # would erase the antecedent a follow-up resolves against.
        history = _messages("old", "Q" * 100, "A" * 100)
        budgeted = _budgeted_history(history, max_message_chars=0, max_total_chars=50)
        assert len(budgeted) == 2
        assert [entry["content"][0] for entry in budgeted] == ["Q", "A"]

    def test_zero_disables_each_cap_independently(self) -> None:
        history = _messages("B" * 9000, "C" * 9000)
        untouched = _budgeted_history(history, max_message_chars=0, max_total_chars=0)
        assert untouched == history
        per_message_only = _budgeted_history(history, max_message_chars=10, max_total_chars=0)
        assert len(per_message_only) == 2
        assert all("… [truncated]" in entry["content"] for entry in per_message_only)

    def test_roles_survive_budgeting(self) -> None:
        history = [
            {"role": "user", "content": "X" * 20},
            {"role": "assistant", "content": "Y" * 20},
        ]
        budgeted = _budgeted_history(history, max_message_chars=5, max_total_chars=0)
        assert [entry["role"] for entry in budgeted] == ["user", "assistant"]


class TestHistorySettings:
    """The renamed knob and its legacy env spellings."""

    def test_defaults(self) -> None:
        from ai.core.config import Settings

        settings = Settings(_env_file=None)
        assert settings.chat_history_messages == 12
        assert settings.chat_history_max_message_chars == 4000
        assert settings.chat_history_max_total_chars == 24000
        assert settings.feature_turn_usage_persistence is True

    def test_legacy_turns_spellings_still_load(self, monkeypatch) -> None:
        from ai.core.config import Settings

        for name in (
            "AIMMS_CHAT_HISTORY_MESSAGES",
            "CHAT_HISTORY_MESSAGES",
            "AIMMS_CHAT_HISTORY_TURNS",
            "CHAT_HISTORY_TURNS",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AIMMS_CHAT_HISTORY_TURNS", "7")
        assert Settings(_env_file=None).chat_history_messages == 7
        # The canonical spelling wins over the legacy one when both are set.
        monkeypatch.setenv("AIMMS_CHAT_HISTORY_MESSAGES", "9")
        assert Settings(_env_file=None).chat_history_messages == 9


class TestTurnUsageLedger:
    """The fail-soft per-turn accumulator."""

    def test_record_and_totals(self) -> None:
        # S37: both vocabularies normalize to canonical keys at record time,
        # so cross-source totals merge into one comparable number.
        ledger = TurnUsageLedger()
        ledger.record("wf8_lookup", {"input_token_count": 100, "output_token_count": 20})
        ledger.record("luna_diagnostics", {"input_tokens": 50, "output_token_count": 5})
        assert ledger.totals() == {
            "input_tokens": 150,
            "output_tokens": 25,
        }
        assert ledger.events[0]["source"] == "wf8_lookup"

    def test_non_integers_and_bools_are_dropped(self) -> None:
        # S37: "model"/"deployment" are the only strings that survive; bools
        # and other non-ints still drop, and a string-only event records
        # nothing.
        ledger = TurnUsageLedger()
        ledger.record("wf8_lookup", {"model": "gpt", "cached": True, "tokens": 3})
        assert ledger.events == [{"source": "wf8_lookup", "model": "gpt", "tokens": 3}]
        ledger.record("wf8_lookup", {"model": "gpt"})
        assert len(ledger.events) == 1  # nothing numeric -> no event

    def test_event_list_is_bounded(self) -> None:
        ledger = TurnUsageLedger()
        for index in range(50):
            ledger.record("s", {"n": index})
        assert len(ledger.events) == 32

    def test_record_usage_is_a_noop_when_unbound(self) -> None:
        token = turn_usage_ledger.set(None)
        try:
            record_usage("wf8_lookup", {"tokens": 1})  # must not raise
            assert drain_turn_usage() is None
        finally:
            turn_usage_ledger.reset(token)

    def test_bind_records_and_drains(self) -> None:
        with bind_turn_usage() as ledger:
            record_usage("wf8_lookup", {"tokens": 4})
            drained = drain_turn_usage()
            # S37: totals() sums canonical keys only; non-canonical ints stay
            # per-event detail.
            assert drained == {
                "events": [{"source": "wf8_lookup", "tokens": 4}],
                "totals": {},
            }
            assert ledger.events
        assert drain_turn_usage() is None  # unbound again outside


class TestTerminalMetadataUsageStamp:
    """Usage rides the model_versions funnel, behind its kill switch."""

    def test_usage_is_stamped_when_enabled(self) -> None:
        settings = SimpleNamespace(feature_turn_usage_persistence=True)
        with (
            bind_turn_usage(),
            patch("ai.core.config.get_settings", return_value=settings),
            patch(
                "ai.core.integrations.model_pins.resolved_model_versions",
                return_value={},
            ),
        ):
            record_usage("wf8_lookup", {"input_token_count": 10})
            metadata = _terminal_output_metadata({"workflow_used": "wf8"})
        assert metadata["workflow_used"] == "wf8"
        assert metadata["usage"]["totals"] == {"input_tokens": 10}

    def test_kill_switch_suppresses_the_stamp(self) -> None:
        settings = SimpleNamespace(feature_turn_usage_persistence=False)
        with (
            bind_turn_usage(),
            patch("ai.core.config.get_settings", return_value=settings),
            patch(
                "ai.core.integrations.model_pins.resolved_model_versions",
                return_value={},
            ),
        ):
            record_usage("wf8_lookup", {"input_token_count": 10})
            metadata = _terminal_output_metadata({"workflow_used": "wf8"})
        assert "usage" not in metadata

    def test_empty_ledger_leaves_metadata_clean(self) -> None:
        settings = SimpleNamespace(feature_turn_usage_persistence=True)
        with (
            bind_turn_usage(),
            patch("ai.core.config.get_settings", return_value=settings),
            patch(
                "ai.core.integrations.model_pins.resolved_model_versions",
                return_value={},
            ),
        ):
            metadata = _terminal_output_metadata({"workflow_used": "wf8"})
        assert "usage" not in metadata


class TestHistoryUsageTelemetry:
    """The replay pipeline reports what the window actually costs."""

    def test_replay_metrics_are_recorded(self) -> None:
        from ai.core.tests.test_normalized_turn_service import _TestTurnService

        class _Repository:
            def recent_messages(self, thread_id, limit, *, exclude_latest=0):
                del thread_id, limit, exclude_latest
                return [
                    SimpleNamespace(role="user", content="How many pumps?"),
                    SimpleNamespace(role="assistant", content="Two."),
                ]

        service = _TestTurnService(workflow_factory=lambda: None)
        settings = SimpleNamespace(
            chat_history_messages=6,
            chat_history_max_message_chars=4000,
            chat_history_max_total_chars=24000,
        )
        with (
            bind_turn_usage() as ledger,
            patch("ai.core.config.get_settings", return_value=settings),
        ):
            history = asyncio.run(service._conversation_history(_Repository(), "thread"))
        assert len(history) == 2
        event = next(item for item in ledger.events if item["source"] == "history_replay")
        assert event["history_messages"] == 2
        assert event["history_chars"] == len("How many pumps?") + len("Two.")


class TestLunaUsageAndTruncation:
    """Per-dispatch recording plus honest attribution of provider truncation."""

    def test_dispatch_records_provider_usage(self) -> None:
        from ai.core.tests.test_luna_diagnostics import _canonical_json

        response = _response(text=_canonical_json())
        response.usage = SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150)
        adapter = _adapter(_Client([response]))
        with bind_turn_usage() as ledger:
            outcome = asyncio.run(adapter.reason(envelope=_envelope()))
        assert outcome.response.response_state.value == "complete"
        assert ledger.events == [
            {
                "source": "luna_diagnostics",
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            }
        ]

    def test_provider_truncation_is_named_not_misattributed(self) -> None:
        # HTTP 200, status=incomplete, reason=max_output_tokens: the partial
        # body fails schema validation, which previously blamed the schema.
        response = _response(text='{"kind": "repair_diag')
        response.status = "incomplete"
        response.incomplete_details = SimpleNamespace(reason="max_output_tokens")
        adapter = _adapter(_Client([response]))
        outcome = asyncio.run(adapter.reason(envelope=_envelope()))
        assert outcome.provenance.outcome_code == "output_token_limit"

    def test_other_incomplete_reasons_keep_existing_attribution(self) -> None:
        def _partial():
            response = _response(text='{"kind": "repair_diag')
            response.status = "incomplete"
            response.incomplete_details = SimpleNamespace(reason="content_filter")
            return response

        # Two copies: the schema-retry continuation consumes one.
        adapter = _adapter(_Client([_partial(), _partial()]))
        outcome = asyncio.run(adapter.reason(envelope=_envelope()))
        assert outcome.provenance.outcome_code == "invalid_final_schema"
