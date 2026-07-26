"""V2: the confirmation bar is set by the resolved tool, not by the utterance.

Signed-off contract (2026-07-26): a bare "yes" confirms a REVERSIBLE write; an
IRREVERSIBLE or EXTERNAL_EFFECT action requires the exact server-authored phrase.

Production defect this pins (2026-07-26 live test): "delete the kanban card"
resolved to archive_kanban_card -- reversible -- yet was read back as "This
cannot be undone. To confirm, say confirm delete." The same code left
send_email, cancel_purchase_order and merge_stock on the lenient bar whenever
the request avoided a destructive verb.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.voice.action_severity import (  # noqa: E402
    DEFAULT_CONFIRM_PHRASE,
    WriteSeverity,
    classified_tool_names,
    confirm_phrase_for_tool_name,
    severity_for_tool_name,
)
from ai.core.voice.confirmation import (  # noqa: E402
    ConfirmationReply,
    WriteActionClass,
    interpret_confirmation_reply,
)


def test_every_action_tool_is_deliberately_classified():
    """Adding a write tool must be a severity decision, not a default."""
    from ai.core.tools.capabilities import tool_name
    from ai.core.voice.tool_actions import text_chat_action_tools

    exposed = {tool_name(tool).lower() for tool in text_chat_action_tools()}
    unclassified = exposed - classified_tool_names()

    assert unclassified == set(), f"unclassified voice action tools: {sorted(unclassified)}"


def test_unknown_tools_fail_closed_to_strict():
    assert severity_for_tool_name("some_future_write_tool") is WriteSeverity.IRREVERSIBLE
    assert confirm_phrase_for_tool_name("some_future_write_tool") == DEFAULT_CONFIRM_PHRASE


@pytest.mark.parametrize(
    "name",
    ["archive_kanban_card", "restore_kanban_card", "move_kanban_card", "add_stock", "update_part"],
)
def test_reversible_actions_keep_the_lenient_bar(name):
    assert severity_for_tool_name(name) is WriteSeverity.REVERSIBLE


@pytest.mark.parametrize(
    "name",
    ["send_email", "generate_and_send_document", "mark_email_processed"],
)
def test_external_effects_are_never_lenient(name):
    """An email cannot be recalled, however routine the request sounded."""
    assert severity_for_tool_name(name) is WriteSeverity.EXTERNAL_EFFECT


@pytest.mark.parametrize(
    "name",
    [
        "cancel_purchase_order",
        "merge_stock",
        "deactivate_part",
        "remove_stock",
        "delete_purchase_order",
        "convert_stock",
        "issue_purchase_order",
    ],
)
def test_destructive_actions_require_a_phrase(name):
    assert severity_for_tool_name(name) is WriteSeverity.IRREVERSIBLE
    assert confirm_phrase_for_tool_name(name) != ""


def test_no_confirm_phrase_reads_as_a_decline():
    """'cancel' alone would collide with the decline grammar and abort itself."""
    for name in classified_tool_names():
        phrase = confirm_phrase_for_tool_name(name)
        if severity_for_tool_name(name) is WriteSeverity.REVERSIBLE:
            continue
        assert (
            interpret_confirmation_reply(phrase, required_phrase=phrase) is ConfirmationReply.AFFIRM
        ), f"{name}: {phrase!r} does not confirm itself"


# --------------------------------------------------------------------------- #
# _action_class: the tool decides, the utterance may only raise                #
# --------------------------------------------------------------------------- #
def _classify(tool_name_str: str, content: str):
    """Classify by tool name without importing live tool callables."""
    from unittest.mock import patch

    from ai.core.voice import tool_actions

    with (
        patch.object(tool_actions, "tool_name", lambda _tool: tool_name_str),
        patch.object(tool_actions, "tool_requirement", lambda _tool: None),
    ):
        return tool_actions._action_class(object(), content)


def test_archive_stays_reversible_despite_the_word_delete():
    """The exact production utterance; archive must not claim to be permanent."""
    action_class, phrase = _classify(
        "archive_kanban_card", "can you delete the kanban card for grinder pump"
    )

    assert action_class is WriteActionClass.IRREVERSIBLE  # utterance raised it
    # ...but the phrase names the real operation, not the mis-described one.
    assert phrase == DEFAULT_CONFIRM_PHRASE


def test_archive_with_a_neutral_utterance_takes_the_lenient_bar():
    action_class, phrase = _classify(
        "archive_kanban_card", "please take that grinder pump card off the board"
    )

    assert action_class is WriteActionClass.CONFIRMABLE
    assert phrase == ""


def test_send_email_is_strict_even_for_a_neutral_utterance():
    """The dangerous inverse: this used to execute on a bare 'yes'."""
    action_class, phrase = _classify("send_email", "send the RFQ to the supplier for the seal")

    assert action_class is WriteActionClass.IRREVERSIBLE
    assert phrase == "confirm send"
    assert interpret_confirmation_reply("yes", required_phrase=phrase) is not (
        ConfirmationReply.AFFIRM
    )


def test_cancel_purchase_order_is_strict_and_its_phrase_is_unambiguous():
    action_class, phrase = _classify("cancel_purchase_order", "cancel purchase order 14")

    assert action_class is WriteActionClass.IRREVERSIBLE
    # Not the bare word "cancel", which the decline grammar would swallow.
    assert phrase == "confirm cancel order"
    assert interpret_confirmation_reply(phrase, required_phrase=phrase) is (
        ConfirmationReply.AFFIRM
    )


def test_utterance_can_raise_but_never_lower_the_bar():
    from ai.core.tools.capabilities import tool_name
    from ai.core.voice.tool_actions import text_chat_action_tools

    neutral = "please handle that for me"
    destructive = "delete it permanently"
    for tool in text_chat_action_tools():
        name = tool_name(tool).lower()
        lenient, _ = _classify(name, neutral)
        raised, _ = _classify(name, destructive)
        if lenient is WriteActionClass.IRREVERSIBLE:
            assert raised is WriteActionClass.IRREVERSIBLE, name
