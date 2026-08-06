"""S15: the semantic cache is deleted, and deletion is the safety rule.

The cache these tests used to govern carried a latent trap S6 had to fence at
runtime: serving machine A's cached diagnosis for machine B. S15 deletes the
plane outright, so the never-cache rules become absence pins — there is no
configuration in which a cached diagnosis can exist, and these tests fail the
moment anyone brings the module or its endpoints back.
"""

# ruff: noqa: E402

from __future__ import annotations

import importlib.util
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest


def test_semantic_cache_module_is_gone() -> None:
    """The module cannot be imported; absence beats any runtime fence."""
    assert importlib.util.find_spec("ai.core.memory.semantic_cache") is None


def test_quarantined_persistence_plane_is_gone() -> None:
    """The quarantined conversation persistence/search plane stays deleted."""
    for module in (
        "ai.core.infrastructure.persistence",
        "ai.core.infrastructure.idempotency",
        "ai.core.infrastructure.message_store",
        "ai.core.infrastructure.checkpoints",
        "ai.core.integrations.search.azure_ai_search",
    ):
        assert importlib.util.find_spec(module) is None, module


def test_memory_package_exports_no_cache() -> None:
    import ai.core.memory as memory

    for name in ("SemanticCache", "get_semantic_cache", "HITLSafetyRules"):
        assert not hasattr(memory, name), name


def test_cache_endpoints_are_absent() -> None:
    """/cache/stats and /cache/invalidate 404: the dead surface is unreachable."""
    from ai.core.app import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/cache/stats" not in paths
    assert "/cache/invalidate" not in paths


def test_deleted_settings_do_not_return() -> None:
    """The cache/persistence settings died with their plane."""
    from ai.core.config import Settings

    fields = set(Settings.model_fields)
    for name in (
        "semantic_cache_enabled",
        "semantic_cache_similarity_threshold",
        "semantic_cache_ttl_hours",
        "conversation_persistence_enabled",
        "conversation_search_enabled",
        "conversation_sync_batch_size",
        "azure_search_index_name",
    ):
        assert name not in fields, name


def test_wf5_module_is_gone() -> None:
    """S13 retired wf5 from the registry; S15 deletes the file itself."""
    assert importlib.util.find_spec("ai.core.workflows.wf5_cpq") is None
    with pytest.raises(ImportError):
        from ai.core.workflows import T5CPQWorkflow  # noqa: F401
