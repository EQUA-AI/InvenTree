"""S15 (WP-B2/B3): the fail-closed pilot-stop gate, island half.

Loader/cache/dedup semantics via the swappable seams (no Django-plane
service involved), the code-literal wire pins, and the voice-rail typed
conversion through the real route handler.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core import pilot_latch  # noqa: E402
from ai.core.config import Settings  # noqa: E402
from django.core.management import call_command  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    pilot_latch.invalidate_latch_cache()
    monkeypatch.setattr(pilot_latch, "_last_engage_attempt", {})
    yield
    pilot_latch.invalidate_latch_cache()


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **over)  # ty: ignore[unknown-argument]


def _armed(monkeypatch, state: pilot_latch.LatchState | Exception):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_AI_PILOT_STOP_LATCH=True),
    )
    if isinstance(state, Exception):

        def loader():
            raise state

    else:

        def loader():
            return state

    monkeypatch.setattr(pilot_latch, "load_latch_state", loader)


# --------------------------------------------------------------------------- #
# Gate semantics                                                               #
# --------------------------------------------------------------------------- #
def test_dark_flag_is_inert_and_never_loads(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pilot_latch, "load_latch_state", lambda: calls.append(1) or pilot_latch.LatchState(True)
    )
    pilot_latch.check_pilot_admission()  # default settings: flag off
    assert calls == []


def test_latched_state_raises_the_typed_stop(monkeypatch):
    _armed(monkeypatch, pilot_latch.LatchState(True, "model_pin_mismatch"))
    with pytest.raises(pilot_latch.PilotStopped) as excinfo:
        pilot_latch.check_pilot_admission()
    assert excinfo.value.reason_code == "model_pin_mismatch"
    assert excinfo.value.code == "pilot_stopped"


def test_clear_state_passes_and_caches(monkeypatch):
    calls = []

    def loader():
        calls.append(1)
        return pilot_latch.LatchState(False)

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_AI_PILOT_STOP_LATCH=True),
    )
    monkeypatch.setattr(pilot_latch, "load_latch_state", loader)
    pilot_latch.check_pilot_admission()
    pilot_latch.check_pilot_admission()
    assert calls == [1], "the second check must ride the cache"


def test_unreadable_state_fails_closed_while_armed(monkeypatch):
    """The deliberate inverse of admission control's fail-open ADR."""
    _armed(monkeypatch, RuntimeError("db down"))
    with pytest.raises(pilot_latch.PilotLatchUnavailable) as excinfo:
        pilot_latch.check_pilot_admission()
    assert excinfo.value.code == "pilot_latch_unavailable"
    assert excinfo.value.retry_after == 30


def test_cached_reason_stops_without_the_loader(monkeypatch):
    from django.core.cache import cache

    cache.set(pilot_latch.LATCH_CACHE_KEY, "stale_domain_contamination", 60)
    _armed(monkeypatch, RuntimeError("loader must not be needed"))
    with pytest.raises(pilot_latch.PilotStopped) as excinfo:
        pilot_latch.check_pilot_admission()
    assert excinfo.value.reason_code == "stale_domain_contamination"


# --------------------------------------------------------------------------- #
# report_critical_event (the B3 entry point)                                   #
# --------------------------------------------------------------------------- #
def test_dark_report_logs_but_never_engages(monkeypatch):
    engaged = []
    monkeypatch.setattr(pilot_latch, "_engage", lambda code, _detail: engaged.append(code))
    pilot_latch.report_critical_event("enforce_fail_open")
    assert engaged == []


def test_armed_report_engages_once_and_dedups(monkeypatch):
    engaged = []
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_AI_PILOT_STOP_LATCH=True),
    )
    monkeypatch.setattr(pilot_latch, "_engage", lambda code, _detail: engaged.append(code))
    pilot_latch.report_critical_event("model_pin_mismatch", "gpt-x")
    pilot_latch.report_critical_event("model_pin_mismatch", "gpt-x")
    assert engaged == ["model_pin_mismatch"]
    # A different reason engages independently.
    pilot_latch.report_critical_event("enforce_fail_open")
    assert engaged == ["model_pin_mismatch", "enforce_fail_open"]


def test_report_never_raises_even_when_engage_explodes(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(FEATURE_AI_PILOT_STOP_LATCH=True),
    )

    def boom(code, detail):
        raise RuntimeError("db down")

    monkeypatch.setattr(pilot_latch, "_engage", boom)
    pilot_latch.report_critical_event("eval_fixture_leak")  # must not raise


# --------------------------------------------------------------------------- #
# Wire pins                                                                    #
# --------------------------------------------------------------------------- #
def test_pilot_error_codes_are_pinned_literals():
    assert pilot_latch.PILOT_ERROR_CODES == ("pilot_stopped", "pilot_latch_unavailable")
    assert pilot_latch.PilotStopped().code == "pilot_stopped"
    assert pilot_latch.PilotLatchUnavailable().code == "pilot_latch_unavailable"


def test_preflight_payload_carries_the_latch_field():
    from ai.core.quota.wire import QuotaPreflightPayload

    assert "pilot_stopped" in QuotaPreflightPayload.model_fields


# --------------------------------------------------------------------------- #
# Rail conversion: the voice route (the same typed pattern as HTTP/AG-UI)      #
# --------------------------------------------------------------------------- #
def test_voice_rail_converts_the_stop_to_a_typed_503(monkeypatch):
    from types import SimpleNamespace

    import ai.core.app as ai_app
    from ai.core.voice.routes import (
        VoiceSessionCreateRequest,
        VoiceTurnRequest,
        create_voice_session,
        submit_voice_turn,
    )

    from .test_realtime_session_api import _expect_http, _principal, _run, _user
    from .test_realtime_session_api import _settings as _voice_settings

    user = _user()
    settings = _voice_settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )

    class _StoppedService:
        async def process(self, **kwargs):
            raise pilot_latch.PilotStopped("manual")

    with (
        patch.object(ai_app, "get_turn_service", return_value=_StoppedService()),
        patch(
            "ai.core.trusted_context.build_trusted_turn_context",
            return_value=SimpleNamespace(),
        ),
    ):
        _expect_http(
            principal,
            lambda: submit_voice_turn(
                created["id"], VoiceTurnRequest(transcript="status?", item_id="pl-1")
            ),
            settings,
            503,
            "pilot_stopped",
        )
    # The rejection landed in the content-free ledger.
    from aichat.models import AIRequestRejection

    assert AIRequestRejection.objects.filter(code="pilot_stopped").exists()


# --------------------------------------------------------------------------- #
# WP-B3: the mechanically detectable Q50 triggers                              #
# --------------------------------------------------------------------------- #
def test_mid_process_model_swap_reports_the_pin_trigger(monkeypatch):
    from ai.core.integrations import model_pins

    reports = []
    monkeypatch.setattr(
        "ai.core.pilot_latch.report_critical_event",
        lambda code, _detail="": reports.append(code),
    )
    model_pins.record_resolved_model("pl-dep-a", "model-1")
    assert reports == []  # first resolution is informational
    model_pins.record_resolved_model("pl-dep-a", "model-2")
    assert reports == ["model_pin_mismatch"]


def test_first_resolution_against_a_set_pin_reports(monkeypatch):
    from ai.core.integrations import model_pins

    reports = []
    monkeypatch.setattr(
        "ai.core.pilot_latch.report_critical_event",
        lambda code, _detail="": reports.append(code),
    )
    pinned = _settings(
        AZURE_OPENAI_DEPLOYMENT="pl-dep-b", AZURE_OPENAI_EXPECTED_MODEL="model-pinned"
    )
    monkeypatch.setattr("ai.core.config.get_settings", lambda: pinned)
    model_pins.record_resolved_model("pl-dep-b", "model-swapped")
    assert reports == ["model_pin_mismatch"]


def test_budget_fail_open_reports_only_under_enforce(monkeypatch):
    from ai.core.middleware import budget

    reports = []
    monkeypatch.setattr(
        "ai.core.pilot_latch.report_critical_event",
        lambda code, _detail="": reports.append(code),
    )
    monkeypatch.setattr(budget, "current_spend", lambda _user_pk, _now=None: None)

    shadow_settings = _settings(AI_USER_DAILY_TOKEN_BUDGET=1000, FEATURE_TOKEN_BUDGET_SHADOW=True)
    monkeypatch.setattr("ai.core.config.get_settings", lambda: shadow_settings)
    decision = budget.check_budget("77")
    assert decision.blocked is False
    assert reports == []

    enforce_settings = _settings(AI_USER_DAILY_TOKEN_BUDGET=1000, FEATURE_TOKEN_BUDGET_ENFORCE=True)
    monkeypatch.setattr("ai.core.config.get_settings", lambda: enforce_settings)
    decision = budget.check_budget("77")
    assert decision.blocked is False  # behavior unchanged; the EVENT is the point
    assert reports == ["enforce_fail_open"]


def test_rate_store_outage_reports_only_under_enforce(monkeypatch):
    from ai.core.middleware.rate_limit import RateLimitConfig, WindowedRateLimiter

    class _DeadStore:
        def peek(self, **kwargs):
            return None

        def increment(self, **kwargs):
            return None

    reports = []
    monkeypatch.setattr(
        "ai.core.pilot_latch.report_critical_event",
        lambda code, _detail="": reports.append(code),
    )
    limiter = WindowedRateLimiter(RateLimitConfig(), store=_DeadStore())

    dark = _settings()
    monkeypatch.setattr("ai.core.config.get_settings", lambda: dark)
    result = limiter.check_rate_limit("77", "/chat")
    assert result.allowed is True
    assert reports == []

    enforce = _settings(FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE=True)
    monkeypatch.setattr("ai.core.config.get_settings", lambda: enforce)
    result = limiter.check_rate_limit("77", "/chat")
    assert result.allowed is True  # fail-open behavior unchanged; event reported
    assert reports == ["enforce_fail_open"]
