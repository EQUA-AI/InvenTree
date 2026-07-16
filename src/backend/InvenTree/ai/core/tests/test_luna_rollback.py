"""WS6-T8: disabling diagnosis preserves the typed fast path.

The kill switch is ``FEATURE_VOICE_LIVE_DIAGNOSIS``. With it off, the
normalized turn service builds no router, no reasoning adapter, and no
diagnostic tool registry — complex requests follow the legacy typed path
instead of silently pretending to diagnose.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.config import Settings  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402


def _service_with(settings: Settings) -> NormalizedTurnService:
    with patch("ai.core.config.get_settings", return_value=settings):
        return NormalizedTurnService(workflow_factory=lambda: object())


def test_diagnosis_flag_off_builds_no_reasoning_components():
    service = _service_with(Settings(_env_file=None))
    assert service.complexity_router is None
    assert service.reasoning_adapter is None
    assert service.diagnostic_tool_registry is None


def test_diagnosis_flag_on_builds_router_and_registry():
    settings = Settings(
        _env_file=None,
        FEATURE_VOICE_LIVE_DIAGNOSIS=True,
    )
    service = _service_with(settings)
    assert service.complexity_router is not None
    assert service.diagnostic_tool_registry is not None


def test_safety_tool_stays_absent_while_p0s_are_open():
    settings = Settings(
        _env_file=None,
        FEATURE_VOICE_LIVE_DIAGNOSIS=True,
    )
    service = _service_with(settings)
    names = service.diagnostic_tool_registry.names
    assert "get_live_safety_status" not in names
    assert "get_machine_context" in names
