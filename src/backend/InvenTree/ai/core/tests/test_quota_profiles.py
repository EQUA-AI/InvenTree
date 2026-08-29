"""S12 WP-B1: cached policy resolution (`ai.core.quota.profiles`).

The island contract: resolution never touches the ORM directly — the lazy
loader is the only durable read, and a loader/cache double failure raises
``QuotaSourceUnavailable`` for the CALLER's shadow/enforce posture.
"""

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
from ai.core.quota import profiles
from ai.core.quota.assignment_source import PolicySnapshot

_SETTINGS = SimpleNamespace(
    ai_user_daily_token_budget=1_000_000,
    ai_quota_policy_cache_ttl_s=60,
)

_EVAL = PolicySnapshot(
    profile="evaluation",
    version=3,
    user_cap=5_000_000,
    tenant_cap=20_000_000,
    global_cap=20_000_000,
    requests_per_minute=200,
    requests_per_hour=2_000,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _settings():
    with mock.patch("ai.core.config.get_settings", return_value=_SETTINGS):
        yield


def test_standard_default_mirrors_the_v1_budget() -> None:
    snapshot = profiles.standard_snapshot()
    assert snapshot.profile == "standard"
    assert snapshot.user_cap == 1_000_000
    assert snapshot.tenant_cap > snapshot.user_cap  # finite, not unlimited
    assert snapshot.requests_per_minute == 10
    assert snapshot.requests_per_hour == 100


def test_no_assignment_resolves_standard_and_caches_the_absence() -> None:
    calls = []

    def loader(user_pk):
        calls.append(user_pk)
        return None

    with mock.patch.object(profiles, "load_assignment", loader):
        first = profiles.resolve_profile(7)
        second = profiles.resolve_profile(7)
    assert first.profile == "standard"
    assert second.profile == "standard"
    assert calls == [7], "absence must be cached, not re-queried per turn"


def test_assignment_resolves_and_caches() -> None:
    calls = []

    def loader(user_pk):
        calls.append(user_pk)
        return _EVAL

    with mock.patch.object(profiles, "load_assignment", loader):
        assert profiles.resolve_profile(9) == _EVAL
        assert profiles.resolve_profile(9) == _EVAL
    assert calls == [9]


def test_invalidate_forces_a_reload() -> None:
    responses = [_EVAL, None]

    def loader(user_pk):
        return responses.pop(0)

    with mock.patch.object(profiles, "load_assignment", loader):
        assert profiles.resolve_profile(4).profile == "evaluation"
        profiles.invalidate_profile(4)
        assert profiles.resolve_profile(4).profile == "standard"


def test_loader_failure_raises_for_the_caller_posture() -> None:
    def loader(user_pk):
        raise RuntimeError("db down")

    with (
        mock.patch.object(profiles, "load_assignment", loader),
        pytest.raises(profiles.QuotaSourceUnavailable),
    ):
        profiles.resolve_profile(2)


def test_cached_value_survives_a_loader_outage() -> None:
    """The cache is the availability buffer: within TTL no loader runs."""
    with mock.patch.object(profiles, "load_assignment", lambda _pk: _EVAL):
        profiles.resolve_profile(3)

    def broken(user_pk):
        raise RuntimeError("db down")

    with mock.patch.object(profiles, "load_assignment", broken):
        assert profiles.resolve_profile(3) == _EVAL
