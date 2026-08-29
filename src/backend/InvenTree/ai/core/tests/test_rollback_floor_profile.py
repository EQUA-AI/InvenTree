"""Rollback-floor profile tests (§14): frozen legs, monotonic validation.

The pilot-era capability-tier machinery was removed by owner decision
(2026-08-29); what survives is the floor — armed once, binding forever.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from aimms_capability import (  # noqa: E402
    FLOOR_REQUIREMENTS,
    ROLLBACK_FLOOR,
    validate_rollback_floor,
)
from aimms_flags import REGISTRY  # noqa: E402

SATISFIED_FLOOR = {
    "FEATURE_AI_THREAD_SCOPE_ENFORCE": True,
    "AIMMS_MAINTENANCE_SCOPE_RESOLVER": "tasks.scope.granted_client_scope_resolver",
}


def test_floor_names_are_frozen():
    """A silent edit to what the floor means must fail review."""
    assert ROLLBACK_FLOOR == ("scope_enforce", "shortcut_guard", "fixture_isolation")


def test_every_flag_leg_is_registered():
    """A flag-backed leg nobody bridges would silently never validate."""
    registered = {entry.env_name for entry in REGISTRY}
    for requirement in FLOOR_REQUIREMENTS:
        if requirement.check == "flag":
            assert requirement.env_name in registered, requirement.name


def test_no_registry_entry_can_dark_the_shortcut_guard():
    """The S4 guard is code, not configuration — the strongest floor leg."""
    assert "shortcut_guard" in ROLLBACK_FLOOR
    guard = next(r for r in FLOOR_REQUIREMENTS if r.name == "shortcut_guard")
    assert guard.check == "code"
    for entry in REGISTRY:
        assert "shortcut" not in entry.env_name.lower(), entry.env_name
        assert "safety_policy" not in (entry.description or "").lower(), entry.env_name


def test_satisfied_floor_has_no_violations():
    assert validate_rollback_floor(dict(SATISFIED_FLOOR)) == []


def test_each_dark_leg_is_named():
    for flag, bad_value, expected in (
        ("FEATURE_AI_THREAD_SCOPE_ENFORCE", False, "scope_enforce"),
        (
            "AIMMS_MAINTENANCE_SCOPE_RESOLVER",
            "tasks.scope.single_site_scope_resolver",
            "fixture_isolation",
        ),
    ):
        view = dict(SATISFIED_FLOOR)
        view[flag] = bad_value
        violations = validate_rollback_floor(view)
        assert any(v.startswith(f"{expected}:") for v in violations), violations


def test_invisible_flags_are_the_other_planes_problem():
    """A plane validates only what it bridges; nothing false-positives."""
    assert validate_rollback_floor({}) == []
    only_scope = {"FEATURE_AI_THREAD_SCOPE_ENFORCE": True}
    assert validate_rollback_floor(only_scope) == []
