"""D6 (M1 gate): the token estimator degrades to None when tiktoken is absent.

Budgets are char-based by design (``estimate_tokens`` is telemetry only), so
an import failure must never change behaviour — only the estimate goes
missing. The assembler-side half of this golden (protected sections still
emit, the 4,000/24,000-char ceilings hold) lands with M1 PR B.
"""

from __future__ import annotations

import sys

import pytest
from ai.core import usage


@pytest.fixture(autouse=True)
def _fresh_encoder():
    usage._token_encoder.cache_clear()
    yield
    usage._token_encoder.cache_clear()


def test_import_failure_degrades_to_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    assert usage._token_encoder() is None
    assert usage.estimate_tokens("twelve words of perfectly ordinary operational text here") is None


def test_encoder_failure_after_import_is_also_none(monkeypatch):
    class _Broken:
        @staticmethod
        def get_encoding(_name):
            raise RuntimeError("no vocabulary cache and no network")

    monkeypatch.setitem(sys.modules, "tiktoken", _Broken())
    assert usage._token_encoder() is None
    assert usage.estimate_tokens("anything") is None


def test_installed_encoder_counts_o200k_tokens():
    tiktoken = pytest.importorskip("tiktoken")
    try:
        expected = len(tiktoken.get_encoding("o200k_base").encode("offline boot proof"))
    except Exception as exc:  # pragma: no cover - no cache and no egress
        pytest.skip(f"o200k_base unavailable here: {type(exc).__name__}")
    assert usage.estimate_tokens("offline boot proof") == expected
    assert usage.estimate_tokens("") == 0
