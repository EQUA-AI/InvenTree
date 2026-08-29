"""WP1: the analysis-scope wire contract stays pinned to the live shapes.

The generated TypeScript (``AimmsWire.generated.ts``) is emitted from
``ai.core.analysis.wire``; these tests pin those mirror models to the real
payload builders (``scope_to_payload``) and to the ``app.py`` literals so a
serving-side change cannot drift silently past the byte-exact ``--check``.
"""

# ruff: noqa: E402

from __future__ import annotations

import inspect
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis import scope as scope_contract
from ai.core.analysis import wire
from pydantic import ValidationError


def _payload(**overrides):
    request = {
        "mode": scope_contract.MODE_EXPLICIT,
        "machine_ids": [12, 13],
        "date_window": {"from": "2025-01-01", "to": "2026-01-01"},
        "display_label": "Solar central inverters",
        **overrides,
    }
    return scope_contract.scope_to_payload(scope_contract.normalize_scope_request(request))


def test_stored_payload_validates_against_the_wire_model() -> None:
    model = wire.AnalysisScopePayload.model_validate(_payload())
    assert model.mode == scope_contract.MODE_EXPLICIT
    assert model.machine_ids == [12, 13]
    assert model.date_window.from_ == "2025-01-01"
    # And the wire model serializes back to the exact stored keys.
    assert model.model_dump(by_alias=True) == _payload()


def test_wire_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        wire.AnalysisScopePayload.model_validate({**_payload(), "surprise": 1})


def test_wire_modes_cover_exactly_the_contract_modes() -> None:
    """The ordered wire tuple and the mode sets can never drift apart."""
    assert set(scope_contract.WIRE_MODES) == (
        set(scope_contract.REQUESTABLE_MODES)
        | {scope_contract.MODE_LEGACY, scope_contract.MODE_SITE_GROUP}
    )
    assert len(scope_contract.WIRE_MODES) == len(set(scope_contract.WIRE_MODES))


def test_update_request_mirror_matches_the_route_model() -> None:
    from ai.core.app import ThreadScopeUpdateRequest as route_model

    assert set(wire.ThreadScopeUpdateRequest.model_fields) == set(route_model.model_fields)


def test_error_codes_are_the_app_literals() -> None:
    import ai.core.app as app_module

    source = inspect.getsource(app_module)
    for code in wire.SCOPE_ERROR_CODES:
        assert f'"{code}"' in source, f"{code} not served by app.py"
    assert '"thread_scope": True' in source, "capability advertisement missing"
