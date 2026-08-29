"""S13 WP-B4: admission control + SLO classification."""

# ruff: noqa: E402

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.quota import admission
from ai.core.quota.slo import SLO_TARGETS, slo_breach, slo_class_for


def _settings(**overrides):
    base = {
        "feature_ai_admission_control_shadow": False,
        "feature_ai_admission_control_enforce": True,
        "ai_admission_max_active_per_user": 2,
        "ai_admission_max_active_global": 3,
        "ai_admission_retry_after_s": 5,
        "ai_admission_lease_ttl_s": 300,
        "ai_deployment_env": "adm-test",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


class TestAcquireRelease:
    def _patch(self, **overrides):
        return mock.patch("ai.core.config.get_settings", lambda: _settings(**overrides))

    def test_user_cap_rejects_with_jittered_retry_after(self):
        with self._patch():
            assert admission.acquire_admission("u1").admitted
            assert admission.acquire_admission("u1").admitted
            decision = admission.acquire_admission("u1")
        assert not decision.admitted
        assert decision.outcome == "rejected_user"
        assert 5 <= decision.retry_after <= 10  # base + jitter in [0, base]

    def test_global_cap_rejects_across_users(self):
        with self._patch():
            for user in ("a", "b", "c"):
                assert admission.acquire_admission(user).admitted
            decision = admission.acquire_admission("d")
        assert not decision.admitted
        assert decision.outcome == "rejected_global"

    def test_release_frees_the_slot(self):
        with self._patch():
            admission.acquire_admission("u1")
            admission.acquire_admission("u1")
            assert not admission.acquire_admission("u1").admitted
            admission.release_admission("u1")
            assert admission.acquire_admission("u1").admitted

    def test_global_rejection_releases_the_user_slot_it_took(self):
        with self._patch(ai_admission_max_active_global=1):
            assert admission.acquire_admission("a").admitted
            assert not admission.acquire_admission("b").admitted
            # b holds nothing: releasing a frees the global slot for b.
            admission.release_admission("a")
            assert admission.acquire_admission("b").admitted

    def test_shadow_logs_would_reject_but_admits(self, caplog):
        import logging

        with (
            self._patch(
                feature_ai_admission_control_shadow=True,
                feature_ai_admission_control_enforce=False,
            ),
            caplog.at_level(logging.WARNING, logger="ai.core.quota.admission"),
        ):
            for _ in range(3):
                decision = admission.acquire_admission("u1")
                assert decision.admitted
        assert decision.outcome == "would_reject"
        assert any("admission.would_reject" in r.getMessage() for r in caplog.records)

    def test_flags_off_is_a_noop(self):
        with self._patch(
            feature_ai_admission_control_shadow=False,
            feature_ai_admission_control_enforce=False,
        ):
            for _ in range(10):
                assert admission.acquire_admission("u1").admitted
            admission.release_admission("u1")  # no-op, must not raise

    def test_store_error_fails_open(self):
        with (
            self._patch(),
            mock.patch("django.core.cache.cache.add", side_effect=RuntimeError("down")),
        ):
            decision = admission.acquire_admission("u1")
        assert decision.admitted
        assert decision.outcome == "store_error"

    def test_release_swallows_expired_keys(self):
        with self._patch():
            admission.release_admission("never-acquired")  # must not raise


class TestSlo:
    def test_table_matches_the_spec(self):
        assert SLO_TARGETS["lookup"] == (10, 30, 45)
        assert SLO_TARGETS["aggregate"] == (15, 40, 55)
        assert SLO_TARGETS["synthesis"] == (20, 45, 60)
        assert SLO_TARGETS["deterministic"] == (1, 2, 5)

    def test_classification(self):
        assert slo_class_for("safety_refusal", None) == "deterministic"
        assert slo_class_for("analysis_unavailable", "fleet_aggregate") == "deterministic"
        assert slo_class_for("wf8", "fleet_aggregate") == "aggregate"
        assert slo_class_for("wf8", "manual_wo_comparison") == "synthesis"
        assert slo_class_for("wf8", "record_retrieval") == "lookup"
        assert slo_class_for(None, None) == "lookup"

    def test_breach_thresholds(self):
        assert slo_breach(5, "lookup") is None
        assert slo_breach(15, "lookup") == "p50"
        assert slo_breach(35, "lookup") == "p95"
        assert slo_breach(50, "lookup") == "hard"
        assert slo_breach(50, "unknown-class") is None


def test_routes_serve_the_typed_capacity_code() -> None:
    """ai_capacity_busy is the literal every rail emits (QUOTA_ERROR_CODES pin)."""
    import importlib
    import inspect

    for module_name in ("ai.core.app", "ai.core.agui.routes", "ai.core.voice.routes"):
        source = inspect.getsource(importlib.import_module(module_name))
        assert "ai_capacity_busy" in source, module_name
        assert "AdmissionSaturated" in source, module_name


def test_process_acquires_before_reservation_and_releases_in_settle() -> None:
    """Ordering introspection: admission wraps the whole turn."""
    import inspect

    from ai.core import turn_service

    source = inspect.getsource(turn_service.NormalizedTurnService.process)
    acquire_at = source.index("acquire_admission")
    reserve_at = source.index("_reserve_turn_quota")
    assert acquire_at < reserve_at, "admission must precede reservation"

    settle_source = inspect.getsource(turn_service._settle_turn_quota)
    assert "release_admission" in settle_source
