"""Opt-in real Redis CAS proof; isolates and removes only its unique test keys."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from ai.core.decisions.store import CachedPendingDecisionStore
from ai.core.tests.test_decision_store import _decision
from django.core.cache import cache
from django.test import override_settings


@pytest.mark.skipif(
    not os.getenv("VOICE_DECISION_TEST_REDIS_URL"), reason="explicit isolated Redis URL required"
)
def test_real_redis_atomic_install_and_compare_set():
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": os.environ["VOICE_DECISION_TEST_REDIS_URL"],
                "KEY_PREFIX": "voice-decision-test-" + uuid4().hex,
            }
        }
    ):
        store = CachedPendingDecisionStore()
        d = _decision(thread_id="cas")
        try:
            assert store.install(d, None)
            assert store.read("cas") == d
            assert not store.install(replace(d, decision_id=str(uuid4())), None)
            replacement = replace(d, sequence=d.sequence + 1, review_acknowledged=True)
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(
                    pool.map(lambda _: store.replace_if("cas", d.sequence, replacement), range(8))
                )
            assert results.count(True) == 1
            assert store.read("cas") == replacement
        finally:
            cache.delete(store._key("cas"))
            cache.delete(store._lock_key("cas"))
