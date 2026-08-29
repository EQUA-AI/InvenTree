"""S5b (WP-A4): thread-stable identity pseudonyms."""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.analysis.pseudonyms import thread_pseudonymizer


def test_stable_within_a_thread() -> None:
    label = thread_pseudonymizer(41)
    assert label("user", 7) == label("user", 7)
    assert label("user", 7) != label("user", 8)


def test_uncorrelatable_across_threads() -> None:
    assert thread_pseudonymizer(41)("user", 7) != thread_pseudonymizer(42)("user", 7)


def test_kinds_partition_namespaces() -> None:
    label = thread_pseudonymizer(1)
    assert label("user", "7") != label("text", "7")


def test_label_shape_leaks_nothing() -> None:
    """No pk, username fragment, or thread id survives into the label."""
    label = thread_pseudonymizer(12345)("user", 67890)
    assert label.startswith("person-")
    assert len(label) == len("person-") + 10
    assert "67890" not in label
    assert "12345" not in label


def test_none_thread_is_still_deterministic() -> None:
    assert thread_pseudonymizer(None)("user", 7) == thread_pseudonymizer(None)("user", 7)
