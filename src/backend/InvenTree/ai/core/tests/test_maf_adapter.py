"""M1 PR C: the maf_adapter package — replay renderer parity and the pin provider.

Django-light on purpose (fake agent/session, no ``ai.core`` service import):
these are the tests the ``ai_maf_matrix.yaml`` lane runs on both SDK
shapes. GA cases skip on the pin.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from ai.core.memory import maf_adapter
from ai.core.memory.context_assembler import REPLAY_CARRIER
from ai.core.memory.maf_adapter import (
    MAF_SHAPE,
    AimmsPinContextProvider,
    memory_block_message,
    replay_messages,
)

HISTORY = [
    {"role": "user", "content": "How many fasteners are in stock?"},
    {"role": "assistant", "content": "Four parts carry them."},
    {"role": "system", "content": "never replayed"},
    {"role": "tool", "content": "never replayed either"},
    {"role": "user", "content": "   "},
    "not a dict",
]


def _legacy_run_input(query, context):
    """wf8._run_input before PR C — the parity oracle."""
    from agent_framework import ChatMessage, Role, TextContent

    history = (context or {}).get("conversation_history")
    if not isinstance(history, list) or not history:
        return query
    messages = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        role_name = str(entry.get("role"))
        if role_name not in ("user", "assistant"):
            continue
        role = Role.ASSISTANT if role_name == "assistant" else Role.USER
        messages.append(ChatMessage(role=role, contents=[TextContent(text=content)]))
    if not messages:
        return query
    messages.append(ChatMessage(role=Role.USER, contents=[TextContent(text=query)]))
    return messages


def _texts(messages):
    return [(str(m.role.value if hasattr(m.role, "value") else m.role), m.text) for m in messages]


def test_replay_messages_matches_the_pre_pr_c_wf8_renderer():
    for context in (
        None,
        {},
        {"conversation_history": []},
        {"conversation_history": HISTORY},
        {"conversation_history": "x"},
    ):
        expected = _legacy_run_input("just the open ones", context)
        actual = replay_messages("just the open ones", context)
        if isinstance(expected, str):
            assert actual == expected
        else:
            assert _texts(actual) == _texts(expected)


def test_replay_filters_roles_and_appends_the_query_last():
    messages = replay_messages("and those?", {"conversation_history": HISTORY})
    assert _texts(messages) == [
        ("user", "How many fasteners are in stock?"),
        ("assistant", "Four parts carry them."),
        ("user", "and those?"),
    ]


def test_wf8_run_input_delegates_to_the_shared_renderer():
    from ai.core.workflows import wf8_lookup

    assert _texts(
        wf8_lookup.T1LookupWorkflow._run_input("q", {"conversation_history": HISTORY})
    ) == _texts(replay_messages("q", {"conversation_history": HISTORY}))
    assert wf8_lookup.T1LookupWorkflow._run_input("q", None) == "q"


def test_memory_block_message_is_a_user_message_or_none():
    bundle = SimpleNamespace(
        memory_block=lambda: {"role": "user", "content": "[Thread summary]\nbody"}
    )
    message = memory_block_message(bundle)
    assert message is not None
    assert str(message.role.value if hasattr(message.role, "value") else message.role) == "user"
    assert message.text.startswith("[Thread summary]")
    assert memory_block_message(SimpleNamespace(memory_block=lambda: None)) is None
    assert memory_block_message(None) is None


# --------------------------------------------------------------------------- #
# The pin provider                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(MAF_SHAPE != "pin", reason="pinned SDK only")
def test_pin_provider_injects_nothing_while_the_dict_replays():
    assert REPLAY_CARRIER == "dict"
    provider = AimmsPinContextProvider()
    context = asyncio.run(provider.invoking([SimpleNamespace(role="user", text="q")]))
    assert not context.messages and not context.instructions and not context.tools
    assert provider.provider_calls == 1


@pytest.mark.skipif(MAF_SHAPE != "pin", reason="pinned SDK only")
def test_pin_provider_records_one_ledger_event_per_invocation():
    provider = AimmsPinContextProvider()
    asyncio.run(provider.thread_created("thread_1"))
    asyncio.run(provider.invoked([1, 2], [3], None))
    asyncio.run(provider.invoked([1], None, RuntimeError("boom")))
    assert provider.threads_created == ["thread_1"]
    assert provider.events == [
        {"request_messages": 2, "response_messages": 1, "error": None},
        {"request_messages": 1, "response_messages": 0, "error": "RuntimeError"},
    ]


@pytest.mark.skipif(MAF_SHAPE != "pin", reason="pinned SDK only")
def test_pin_provider_attaches_to_a_fake_agent_without_replaying():
    """Through the SDK's own aggregate: still no messages, no instructions."""
    from agent_framework import AggregateContextProvider

    provider = AimmsPinContextProvider()
    aggregate = AggregateContextProvider([provider])
    context = asyncio.run(aggregate.invoking([SimpleNamespace(role="user", text="q")]))
    assert not context.messages
    assert not context.instructions
    assert provider.provider_calls == 1


def test_the_ga_module_is_not_importable_on_the_pin():
    if MAF_SHAPE == "ga":
        pytest.skip("GA shape: the module imports")
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("ai.core.memory.maf_adapter._ga")
    assert not hasattr(maf_adapter, "AimmsLedgerHistoryProvider")


# --------------------------------------------------------------------------- #
# GA providers (the matrix lane's ga entry)                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(MAF_SHAPE != "ga", reason="GA SDK only")
def test_ga_ledger_provider_is_the_only_loader_and_never_saves():
    from ai.core.memory.maf_adapter._ga import AimmsAuditHistoryProvider, AimmsLedgerHistoryProvider

    bundle = SimpleNamespace(replay_dict=lambda: HISTORY[:2])
    ledger = AimmsLedgerHistoryProvider(lambda: bundle)
    audit = AimmsAuditHistoryProvider()
    assert ledger.load_messages is True and audit.load_messages is False
    assert audit.store_context_messages is True
    # While the dict replays, the loader stays silent (double-replay hazard).
    assert asyncio.run(ledger.get_messages("s1")) == []
    asyncio.run(ledger.save_messages("s1", [object()]))
    asyncio.run(audit.save_messages("s1", [object()]))
    assert len(audit.captured) == 1


@pytest.mark.skipif(MAF_SHAPE != "ga", reason="GA SDK only")
def test_ga_memory_provider_enqueues_only_after_run():
    from ai.core.memory.maf_adapter._ga import AimmsMemoryContextProvider

    calls = []
    provider = AimmsMemoryContextProvider(lambda: None, enqueue=lambda: calls.append("enqueued"))
    asyncio.run(provider.after_run(agent=None, session=None, context=None, state={}))
    assert calls == ["enqueued"] and provider.after_run_calls == 1
