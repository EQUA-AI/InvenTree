"""S12/S13 WP-B0: the quota/admission wire contract (`ai.core.quota.wire`).

Pins the machine-readable limiter codes to the code that actually serves
them and to the generated TypeScript union — the same discipline as
``test_scope_wire.py``.
"""

# ruff: noqa: E402

from __future__ import annotations

import inspect
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.quota import wire

_EXPECTED_CODES = (
    "token_budget_exhausted",
    "rate_limited",
    "ai_capacity_busy",
    "quota_store_unavailable",
)


def test_error_code_union_is_pinned() -> None:
    assert wire.QUOTA_ERROR_CODES == _EXPECTED_CODES
    assert len(set(wire.QUOTA_ERROR_CODES)) == len(wire.QUOTA_ERROR_CODES)


def test_limiter_codes_are_served_by_the_middleware() -> None:
    """The two 429 codes must be the literals the middleware writes."""
    import sys

    import ai.core.middleware.rate_limit  # noqa: F401 - ensure loaded

    # The middleware package re-exports a symbol named ``rate_limit``, so an
    # ``import ... as`` binding resolves to that attribute (PEP 328 semantics);
    # go through sys.modules for the actual module object.
    source = inspect.getsource(sys.modules["ai.core.middleware.rate_limit"])
    assert '"code": "token_budget_exhausted"' in source
    assert '"code": "rate_limited"' in source


def test_generated_ts_union_matches() -> None:
    # tests -> core -> ai -> InvenTree -> backend -> src
    generated = Path(__file__).resolve().parents[5] / ("frontend/lib/types/AimmsWire.generated.ts")
    text = generated.read_text()
    assert "export type QuotaErrorCode" in text
    for code in wire.QUOTA_ERROR_CODES:
        assert f"'{code}'" in text or f'"{code}"' in text, code
    for interface in (
        "QuotaWindowStatus",
        "QuotaTokenLevel",
        "QuotaStoreStatus",
        "QuotaPreflightPayload",
    ):
        assert f"export interface {interface}" in text, interface


def test_preflight_payload_round_trips() -> None:
    payload = wire.QuotaPreflightPayload(
        profile="standard",
        policy_version=1,
        tokens={
            "user": wire.QuotaTokenLevel(
                used=100, reserved=32_000, remaining=867_900, cap=1_000_000, reset_after_s=3600
            )
        },
        requests={
            "per_minute": wire.QuotaWindowStatus(limit=10, used=2, remaining=8, reset_after_s=42)
        },
        store=wire.QuotaStoreStatus(healthy=True, shared=False),
        fits=None,
    )
    again = wire.QuotaPreflightPayload.model_validate(payload.model_dump())
    assert again == payload
    assert again.fits is None
