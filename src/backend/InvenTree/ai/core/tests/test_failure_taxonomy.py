"""S38: typed turn failures — taxonomy mapping and the RUN_ERROR contract."""

# ruff: noqa: E402

from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.auth import AIPrincipal
from ai.core.config import Settings
from ai.core.failure_taxonomy import FailureClass, classify_turn_failure
from ai.core.trusted_context import TrustedTurnContext
from ai.core.turn_service import NormalizedTurnService, TurnExecutionFailed
from aichat.models import TurnState
from aichat.services import BeginTurnResult


class RateLimitError(Exception):
    """Name-matched stand-in for openai.RateLimitError."""


class APIConnectionError(Exception):
    """Name-matched stand-in for openai.APIConnectionError."""


class ImproperlyConfigured(Exception):
    """Name-matched stand-in for django.core.exceptions.ImproperlyConfigured."""


class ValidationError(Exception):
    """Name-matched stand-in for pydantic.ValidationError."""


class _Status(Exception):
    def __init__(self, status_code):
        super().__init__("boom")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RateLimitError(), FailureClass.RATE_LIMITED),
        (_Status(429), FailureClass.RATE_LIMITED),
        (APIConnectionError(), FailureClass.PROVIDER_OUTAGE),
        (TimeoutError(), FailureClass.PROVIDER_OUTAGE),
        (ConnectionResetError(), FailureClass.PROVIDER_OUTAGE),
        (_Status(503), FailureClass.PROVIDER_OUTAGE),
        (ImproperlyConfigured(), FailureClass.CONFIG_GATE),
        # Pydantic ValidationError is a model-OUTPUT failure here (e.g. a
        # blank reply failing the canonical schema), never a config gate.
        (ValidationError(), FailureClass.INTERNAL),
        (RuntimeError("anything"), FailureClass.INTERNAL),
        (KeyError("k"), FailureClass.INTERNAL),
    ],
)
def test_classification_by_exception_class(exc, expected):
    assert classify_turn_failure(exc) is expected


def test_status_code_is_read_from_nested_response():
    exc = RuntimeError("x")
    exc.response = SimpleNamespace(status_code=502)
    assert classify_turn_failure(exc) is FailureClass.PROVIDER_OUTAGE


def test_wrapper_does_not_demote_cause_chain():
    """A raise-from wrapper keeps the underlying provider class."""
    try:
        try:
            raise APIConnectionError("socket down")
        except APIConnectionError as inner:
            raise RuntimeError("workflow_failed") from inner
    except RuntimeError as wrapped:
        assert classify_turn_failure(wrapped) is FailureClass.PROVIDER_OUTAGE


def test_implicit_context_chain_is_walked():
    try:
        try:
            raise RateLimitError("throttled")
        except RateLimitError:
            raise RuntimeError("workflow_failed")
    except RuntimeError as wrapped:
        assert classify_turn_failure(wrapped) is FailureClass.RATE_LIMITED


def test_stamped_failure_class_wins_over_chain():
    """A layer that saw the original exception can pre-classify (root.py

    carries wf8's verdict across the string boundary this way)."""
    exc = RuntimeError("lookup_failed")
    exc.failure_class = "provider_outage"
    assert classify_turn_failure(exc) is FailureClass.PROVIDER_OUTAGE


def test_invalid_stamp_falls_through_to_chain():
    exc = RuntimeError("lookup_failed")
    exc.failure_class = "not-a-class"
    assert classify_turn_failure(exc) is FailureClass.INTERNAL


# --- RUN_ERROR contract through the real turn service -----------------------


class _TestTurnService(NormalizedTurnService):
    @staticmethod
    async def _call_sync(function, *args, **kwargs):
        return function(*args, **kwargs)


@dataclass
class _FakeTurn:
    pk: str
    request_fingerprint: str
    canonical_result: dict[str, Any] | None = None
    state: str = TurnState.RUNNING

    @property
    def is_terminal(self) -> bool:
        return self.state in TurnState.terminal_values()


class _Repository:
    def __init__(self) -> None:
        self.thread = SimpleNamespace(pk="thread_taxonomy")
        self.turns: dict[str, _FakeTurn] = {}
        self.terminal_calls: list[dict[str, Any]] = []

    def get_or_create(self, thread_id=None, *, title=""):
        return self.thread, False

    def begin_turn(self, thread_id, **kwargs):
        turn = _FakeTurn("turn_taxonomy", kwargs["request_fingerprint"])
        self.turns[kwargs["idempotency_key"]] = turn
        return BeginTurnResult(turn, False)

    def terminal(self, turn_id, **kwargs):
        turn = next(t for t in self.turns.values() if t.pk == turn_id)
        turn.state = kwargs["state"]
        turn.canonical_result = dict(kwargs["canonical_result"])
        self.terminal_calls.append(kwargs)
        return turn


class _OutageWorkflow:
    async def run_stream(self, **kwargs):
        if False:  # pragma: no cover - generator shape
            yield ""
        raise APIConnectionError("socket detail must not escape")


def _principal() -> AIPrincipal:
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="operator",
        authentication_method="django_session",
        scope="site:main",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _context(locale: str = "en") -> TrustedTurnContext:
    return TrustedTurnContext(
        actor="user:7",
        server_policy_key="site:main",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/chat",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id="00000000-0000-0000-0000-000000000007",
        policy_version="1",
        untrusted_content="{}",
        locale=locale,
    )


async def _fail_one_turn(locale: str = "en") -> _Repository:
    repository = _Repository()
    service = _TestTurnService(
        workflow_factory=_OutageWorkflow,
        repository_factory=lambda actor, context: repository,  # noqa: ARG005
    )
    import contextlib

    with contextlib.suppress(TurnExecutionFailed):
        await service.process(
            actor=_principal(),
            thread_id="thread_taxonomy",
            content="hello",
            modality="text",
            trusted_context=_context(locale),
            modality_metadata={},
            idempotency_key=f"taxonomy:{locale}",
            correlation_id=_context().correlation_id,
        )
    return repository


def _run_error_records(repository: _Repository) -> list[dict[str, Any]]:
    """AGUIEvent.to_dict flattens data into the top level; type is 'type'."""
    canonical = repository.terminal_calls[-1]["canonical_result"]
    return [
        record
        for record in canonical.get("events") or []
        if isinstance(record, dict) and "error" in str(record.get("type", "")).lower()
    ]


@pytest.mark.asyncio
async def test_shadow_keeps_generic_event_and_message(monkeypatch):
    """Flag off: class is logged only; event and message are unchanged."""
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: Settings(_env_file=None, FEATURE_TYPED_TURN_FAILURES=False),
    )
    repository = await _fail_one_turn()
    errors = _run_error_records(repository)
    assert errors, "expected a RUN_ERROR event"
    assert "failure_class" not in errors[-1]
    assert (
        repository.terminal_calls[-1]["output_content"]
        == "The diagnostic turn failed before a complete answer was produced."
    )


@pytest.mark.asyncio
async def test_typed_event_and_localized_message(monkeypatch):
    """Flag on: RUN_ERROR carries failure_class; message is per-class + locale."""
    from ai.core.i18n_templates import TURN_FAILED_PROVIDER_OUTAGE, deterministic_template

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: Settings(_env_file=None, FEATURE_TYPED_TURN_FAILURES=True),
    )
    repository = await _fail_one_turn(locale="de")
    errors = _run_error_records(repository)
    assert errors, "expected a RUN_ERROR event"
    assert errors[-1]["failure_class"] == "provider_outage"
    assert errors[-1]["code"] == "turn_failed"
    assert repository.terminal_calls[-1]["output_content"] == deterministic_template(
        TURN_FAILED_PROVIDER_OUTAGE, "de"
    )
    # The provider's exception text must never reach the persisted result.
    assert "socket detail" not in str(repository.terminal_calls[-1])
