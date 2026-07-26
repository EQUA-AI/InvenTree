"""V6/V7/V8: read-back verifiability, the one-turn window, and audit visibility.

All three come from the 2026-07-26 live voice test:

* V6 -- the read-back said "Archive kanban card with card id 127", which a
  technician cannot check before saying yes.
* V7 -- any turn after a proposal was consumed as a confirmation reply, so an
  unrelated question would be answered "Cancelled. No change was made." and
  never actually answered.
* V8 -- the whole write-confirmation audit trail was discarded: ai/core's
  logger.info() never reached stdout because Django's basicConfig had already
  run, making the later one a no-op.
"""

from __future__ import annotations

import asyncio
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.voice.confirmation import (  # noqa: E402
    PendingVoiceConfirmation,
    ProposedWriteAction,
    WriteActionClass,
)
from ai.core.voice.write_gate import (  # noqa: E402
    ExecutableWrite,
    InMemoryPendingWriteStore,
    StoredPendingWrite,
    VoiceWriteGate,
)

_ACTOR = object()
_CTX = object()


class _Allow:
    def allows(self, actor, capability):
        return True


class _Executor:
    def __init__(self):
        self.calls = []

    async def execute(self, executable, *, actor, trusted_context):
        self.calls.append(executable)
        from ai.core.voice.write_gate import VoiceWriteExecutionResult

        return VoiceWriteExecutionResult(ok=True)


def _pending_gate():
    store = InMemoryPendingWriteStore()
    executor = _Executor()
    gate = VoiceWriteGate(permission=_Allow(), executor=executor, store=store)
    store.save(
        7,
        StoredPendingWrite(
            pending=PendingVoiceConfirmation(
                nonce="n1",
                thread_id=7,
                action=ProposedWriteAction(
                    capability="kanban:change",
                    summary="Archive kanban card 127",
                    action_class=WriteActionClass.CONFIRMABLE,
                ),
            ),
            executable=ExecutableWrite(
                tool_name="archive_kanban_card",
                capability="kanban:change",
                arguments={"card_id": 127},
            ),
        ),
    )
    return gate, executor, store


# --------------------------------------------------------------------------- #
# V7: the one-turn window must not swallow an unrelated question               #
# --------------------------------------------------------------------------- #
def test_unrelated_turn_routes_normally_instead_of_being_cancelled():
    gate, executor, store = _pending_gate()

    result = asyncio.run(
        gate.resolve_pending(
            "how many fasteners are in stock?",
            actor=_ACTOR,
            trusted_context=_CTX,
            thread_id=7,
        )
    )

    # None => the caller proceeds with normal routing and the question is answered.
    assert result is None
    # ...and the proposal is gone, so a later bare "yes" cannot revive it.
    assert store.take(7) is None
    assert executor.calls == []


def test_bare_yes_to_a_strict_action_explains_itself_rather_than_routing_away():
    """They did agree -- just not with the exact phrase. Say so."""
    from ai.core.voice.confirmation import STRICT_PHRASE_REQUIRED_PHRASE

    store = InMemoryPendingWriteStore()
    executor = _Executor()
    gate = VoiceWriteGate(permission=_Allow(), executor=executor, store=store)
    store.save(
        7,
        StoredPendingWrite(
            pending=PendingVoiceConfirmation(
                nonce="n1",
                thread_id=7,
                action=ProposedWriteAction(
                    capability="purchase_order:delete",
                    summary="Delete purchase order 14",
                    action_class=WriteActionClass.IRREVERSIBLE,
                    confirm_phrase="confirm delete",
                ),
            ),
            executable=ExecutableWrite(
                tool_name="delete_purchase_order",
                capability="purchase_order:delete",
                arguments={"order_id": 14},
            ),
        ),
    )

    result = asyncio.run(
        gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=7)
    )

    assert result is not None, "a bare yes must not be routed away as a new question"
    assert result.executed is False
    assert result.spoken == STRICT_PHRASE_REQUIRED_PHRASE
    assert executor.calls == []


def test_explicit_decline_still_speaks_the_cancellation():
    gate, executor, _ = _pending_gate()

    result = asyncio.run(
        gate.resolve_pending("cancel", actor=_ACTOR, trusted_context=_CTX, thread_id=7)
    )

    assert result is not None
    assert result.executed is False
    assert "Cancelled" in result.spoken
    assert executor.calls == []


def test_confirmation_still_executes():
    gate, executor, _ = _pending_gate()

    result = asyncio.run(
        gate.resolve_pending("yes", actor=_ACTOR, trusted_context=_CTX, thread_id=7)
    )

    assert result is not None and result.executed is True
    assert len(executor.calls) == 1


def test_abandoned_proposal_is_audited(caplog):
    gate, _executor, _ = _pending_gate()

    with caplog.at_level(logging.INFO, logger="ai.core.voice.write_gate"):
        asyncio.run(
            gate.resolve_pending(
                "what's the stock level for part 49?",
                actor=_ACTOR,
                trusted_context=_CTX,
                thread_id=7,
            )
        )

    assert "voice.write_confirmation.audit" in caplog.text


# --------------------------------------------------------------------------- #
# V6: the read-back must name the record                                       #
# --------------------------------------------------------------------------- #
def test_read_back_names_the_record_not_just_its_id(monkeypatch):
    from ai.core.voice import tool_actions

    async def fake_label(key, value):  # noqa: RUF029 - matches the async seam
        return "Confirm Grinder Pump Emergency Spare" if key == "card_id" else None

    monkeypatch.setattr(tool_actions, "_record_label", fake_label)

    class _Tool:
        __name__ = "archive_kanban_card"

    summary = asyncio.run(tool_actions._action_summary_async(_Tool(), {"card_id": 127}))

    assert "Confirm Grinder Pump Emergency Spare" in summary
    assert "127" in summary  # the id is still there for the record


def test_read_back_falls_back_to_the_id_when_no_label_resolves(monkeypatch):
    from ai.core.voice import tool_actions

    async def no_label(key, value):  # noqa: RUF029 - matches the async seam
        return None

    monkeypatch.setattr(tool_actions, "_record_label", no_label)

    class _Tool:
        __name__ = "archive_kanban_card"

    summary = asyncio.run(tool_actions._action_summary_async(_Tool(), {"card_id": 127}))

    assert "127" in summary
    assert '("' not in summary


def test_label_lookup_never_raises(monkeypatch):
    """A failing lookup must not break the confirmation."""
    from ai.core.voice import tool_actions

    monkeypatch.setitem(tool_actions._RECORD_LABELERS, "card_id", ("nonexistent.module", "nope"))

    assert asyncio.run(tool_actions._record_label("card_id", 127)) is None


# --------------------------------------------------------------------------- #
# V8: ai/core INFO logs must survive Django's logging configuration            #
# --------------------------------------------------------------------------- #
def test_ai_namespace_logging_is_configured_explicitly():
    """basicConfig() is a no-op after Django's; the namespace needs its own level."""
    import inspect

    from ai.core import app

    source = inspect.getsource(app)
    assert 'logging.getLogger("ai").setLevel' in source
