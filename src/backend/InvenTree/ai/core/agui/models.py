"""AG-UI ``RunAgentInput`` request models (S49).

camelCase per the protocol; ``extra="ignore"`` throughout so future spec
fields never 422. Client-supplied ``tools``/``state``/``context`` are
accepted and IGNORED — the tool registry, shared state, and context are
server-owned. Inbound ``messages`` are reconcile-or-drop: the server
derives exactly one user message and its own history is the only
transcript authority (transcript-forgery fence).
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class AGUIInputMessage(BaseModel):
    """One inbound protocol message; only the last user one is consumed.

    ``content`` accepts the full spec union — a string OR a list of content
    parts (text/image/audio/...). Non-text parts are IGNORED, never 422'd:
    the endpoint's contract is that spec-valid input is always accepted.
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    role: str = ""
    content: str | list[Any] | None = None

    def text_content(self) -> str:
        """The message's text: the string itself, or joined text parts."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = [
                str(part.get("text") or "")
                for part in self.content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(part for part in parts if part)
        return ""


class AGUIForwardedProps(BaseModel):
    """The adapter's side-channel for transport concerns."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    idempotency_key: str | None = Field(
        None, validation_alias=AliasChoices("idempotencyKey", "idempotency_key")
    )
    file_ids: list[str] | None = Field(None, validation_alias=AliasChoices("fileIds", "file_ids"))


class RunAgentInput(BaseModel):
    """The AG-UI run request body (POST /agui)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    thread_id: str | None = Field(None, validation_alias=AliasChoices("threadId", "thread_id"))
    run_id: str = Field(..., validation_alias=AliasChoices("runId", "run_id"))
    parent_run_id: str | None = Field(
        None, validation_alias=AliasChoices("parentRunId", "parent_run_id")
    )
    state: Any = None
    messages: list[AGUIInputMessage] = Field(default_factory=list)
    tools: list[Any] = Field(default_factory=list)
    context: list[Any] = Field(default_factory=list)
    forwarded_props: AGUIForwardedProps = Field(
        default_factory=AGUIForwardedProps,
        validation_alias=AliasChoices("forwardedProps", "forwarded_props"),
    )


def derive_user_message(run_input: RunAgentInput) -> str:
    """The single trusted user utterance: the LAST role=user message.

    Every other inbound message — including forged assistant/tool turns —
    is dropped; server-persisted history is the only transcript authority.
    Raises ValueError when no non-empty user message exists (route → 400).
    """
    for message in reversed(run_input.messages):
        if message.role == "user":
            text = message.text_content()
            if text.strip():
                return text
    raise ValueError("RunAgentInput must carry at least one user message with text content")
