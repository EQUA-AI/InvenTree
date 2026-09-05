"""The ONLY memory module that imports ``agent_framework`` (GR-35; plan §9.2).

Re-exports only:

* :func:`replay_messages` / :func:`memory_block_message` (``_replay``) — the
  shared replay renderer every rail uses (moved verbatim from wf8
  ``_run_input``): the builder's ``conversation_history`` dict becomes SDK
  messages, user/assistant roles only, the current query appended last.
* :class:`AimmsPinContextProvider` (``_pin``) on the pinned SDK. The GA
  providers live in ``_ga`` and are imported from there explicitly (the
  module raises ImportError on the pin).
* :data:`MAF_SHAPE` (``_probe``): ``"pin"`` or ``"ga"``.
"""

from __future__ import annotations

from ai.core.memory.maf_adapter._pin import AimmsPinContextProvider
from ai.core.memory.maf_adapter._probe import MAF_SHAPE
from ai.core.memory.maf_adapter._replay import memory_block_message, replay_messages

__all__ = ["MAF_SHAPE", "AimmsPinContextProvider", "memory_block_message", "replay_messages"]
