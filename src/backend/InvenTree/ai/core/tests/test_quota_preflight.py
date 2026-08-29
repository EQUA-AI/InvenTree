"""S12 WP-B3: the read-only quota preflight endpoint."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from ai.core.quota import reservation as resv
from ai.core.quota.assignment_source import PolicySnapshot

_SNAPSHOT = PolicySnapshot(
    profile="evaluation",
    version=4,
    user_cap=100_000,
    tenant_cap=500_000,
    global_cap=1_000_000,
    requests_per_minute=200,
    requests_per_hour=2_000,
)


def _settings(**overrides) -> Settings:
    base = {
        "FEATURE_AI_QUOTA_PROFILES": True,
        "AI_QUOTA_TURN_RESERVE_TOKENS": 10_000,
        "AI_DEPLOYMENT_ENV": "pf-test",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _quota_settings():
    # One settings surface for BOTH the test's reservation setup and the
    # endpoint under test — the counter keys embed env + worst case.
    with mock.patch("ai.core.config.get_settings", _settings):
        yield


def _principal():
    return SimpleNamespace(
        subject="user:5", user_pk="5", scope="site-a", rate_limit_key="user:5", is_staff=False
    )


def _call(**params):
    from ai.core.app import quota_preflight

    with (
        mock.patch("ai.core.app._principal", side_effect=_principal),
        mock.patch("ai.core.quota.profiles.resolve_profile", lambda _pk: _SNAPSHOT),
    ):
        return asyncio.run(quota_preflight(**params))


def test_preflight_arithmetic_used_reserved_remaining() -> None:
    reservation = resv.reserve_turn(
        user_pk="5", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="pf-1"
    )
    resv.settle_turn(reservation, ledger_tokens=4_000, outcome="executed")
    resv.reserve_turn(user_pk="5", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="pf-2")
    payload = _call()
    user = payload["tokens"]["user"]
    assert user["used"] == 4_000
    assert user["reserved"] == 10_000
    assert user["cap"] == 100_000
    assert user["remaining"] == 100_000 - 14_000
    assert payload["profile"] == "evaluation"
    assert payload["policy_version"] == 4
    assert set(payload["tokens"]) == {"user", "tenant", "global"}
    assert payload["fits"] is None


def test_fits_true_false_and_null() -> None:
    assert _call()["fits"] is None
    assert _call(estimated_tokens=50_000)["fits"] is True
    reservation = resv.reserve_turn(
        user_pk="5", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="pf-3"
    )
    resv.settle_turn(reservation, ledger_tokens=95_000, outcome="executed")
    assert _call(estimated_tokens=50_000)["fits"] is False
    assert _call(estimated_requests=100)["fits"] is True
    assert _call(estimated_requests=5_000)["fits"] is False


def test_eval_profile_request_rates_are_visible() -> None:
    payload = _call()
    assert payload["requests"]["per_minute"]["limit"] == 200
    assert payload["requests"]["per_hour"]["limit"] == 2_000


def test_over_cap_caller_still_reads_a_200_shape() -> None:
    """The endpoint never blocks: an exhausted user reads why."""
    reservation = resv.reserve_turn(
        user_pk="5", tenant_id="site-a", snapshot=_SNAPSHOT, idempotency_key="pf-4"
    )
    resv.settle_turn(reservation, ledger_tokens=200_000, outcome="executed")
    payload = _call()
    assert payload["tokens"]["user"]["remaining"] == 0


def test_store_health_reflects_locmem() -> None:
    """The Part-4 disqualifier is observable: LocMem reads as shared=False."""
    payload = _call()
    assert payload["store"]["healthy"] is True
    assert payload["store"]["shared"] is False


def test_source_outage_degrades_to_standard_not_an_error() -> None:
    from ai.core.app import quota_preflight
    from ai.core.quota.profiles import QuotaSourceUnavailable

    def broken(_pk):
        raise QuotaSourceUnavailable("db down")

    with (
        mock.patch("ai.core.app._principal", side_effect=_principal),
        mock.patch("ai.core.quota.profiles.resolve_profile", broken),
    ):
        payload = asyncio.run(quota_preflight())
    assert payload["profile"] == "standard"
    assert payload["store"]["healthy"] is False
