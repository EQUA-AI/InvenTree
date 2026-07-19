"""Read-only execution fence for voice-modality turns.

Speech must never execute an effect (contract §0.2 / FR-VO-010). The voice
loop is hands-free — there is no visible confirmation step — so enforcement
cannot live in the client. This context variable is set around workflow
execution for voice turns and checked at the InvenTree client request
funnel, which every live write tool ultimately calls; a mutating request
under the fence fails as a normal tool error the agent can relay.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

READ_ONLY_TOOLS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aimms_read_only_tools", default=False
)

#: The safe tool-facing message; contains no hidden-object details.
READ_ONLY_MESSAGE = (
    "This voice conversation is read-only. Changes must be made on the "
    "normal authenticated surface, not by voice."
)


def read_only_tools_active() -> bool:
    """Whether the current execution context forbids write tools."""
    return READ_ONLY_TOOLS.get()


@contextmanager
def read_only_tool_fence():
    """Forbid write tools for the enclosed (async) execution context."""
    token = READ_ONLY_TOOLS.set(True)
    try:
        yield
    finally:
        READ_ONLY_TOOLS.reset(token)
