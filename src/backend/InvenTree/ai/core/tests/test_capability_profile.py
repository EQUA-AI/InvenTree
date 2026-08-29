"""A12 capability-profile tests (S14): tiers, pilot profile, rollback floor.

The declared tier is a deployment profile validated at startup on both
planes; these tests pin the declaration itself, the validator semantics,
the inert-at-default guarantee, and the per-turn stamp.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.config import Settings  # noqa: E402
from aimms_capability import (  # noqa: E402
    PILOT_FORBIDDEN,
    ROLLBACK_FLOOR,
    TIER_REQUIREMENTS,
    validate_capability_profile,
)
from aimms_flags import REGISTRY  # noqa: E402


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **over)  # ty: ignore[unknown-argument]


#: A flag view satisfying every tier-1 flag requirement and prohibition.
SATISFIED_TIER1 = {
    "FEATURE_AI_THREAD_SCOPE_ENFORCE": True,
    "FEATURE_AI_ANALYSIS_ROUTER_ENFORCE": True,
    "AIMMS_EVIDENCE_GATE_MODE": "enforce",
    "FEATURE_TOKEN_STREAMING": False,
    "AIMMS_MAINTENANCE_SCOPE_RESOLVER": "tasks.scope.granted_client_scope_resolver",
    "FEATURE_THREAD_SHARING": False,
    "AIMMS_MEDIA_RAG_ENABLED": False,
    "FEATURE_MEDIA_RAG_INGEST": False,
    "FEATURE_MEDIA_RAG_RETRIEVAL": False,
    "FEATURE_WF1_DIAGNOSTICS": False,
    "MODEL_VERSION_BOOT_PROBE_ENABLED": True,
    "FEATURE_AI_RETENTION_JOBS": True,
}


# --------------------------------------------------------------------------- #
# The declaration itself                                                       #
# --------------------------------------------------------------------------- #
def test_tier_requirement_names_are_frozen_per_tier():
    """A silent edit to what a tier means must fail review."""
    by_tier = {}
    for requirement in TIER_REQUIREMENTS:
        by_tier.setdefault(requirement.min_tier, []).append(requirement.name)
    assert by_tier == {
        1: [
            "scope_enforce",
            "direct_analysis_routing",
            "evidence_gate_enforce",
            "buffered_output",
            "canonical_v2",
            "shortcut_guard",
            "fixture_isolation",
            "final_authorization",
            "retention_cleanup",
        ],
        2: ["analytics_service", "population_enum", "exact_membership", "load_25k_evidence"],
        3: ["applicability_relation", "procedure_evidence", "comparison_gates"],
    }


def test_every_flag_requirement_is_visible_to_a_plane():
    """A flag-backed requirement nobody bridges would silently never run."""
    registered = {entry.env_name for entry in REGISTRY}
    for requirement in TIER_REQUIREMENTS:
        if requirement.check == "flag":
            assert requirement.env_name in registered, requirement.name
    for env_name, _required in PILOT_FORBIDDEN:
        assert env_name in registered or env_name == "MODEL_VERSION_BOOT_PROBE_ENABLED", env_name


def test_no_registry_entry_can_dark_the_shortcut_guard():
    """The S4 guard is code, not configuration — the strongest floor leg."""
    assert "shortcut_guard" in ROLLBACK_FLOOR
    for entry in REGISTRY:
        assert "shortcut" not in entry.env_name.lower(), entry.env_name
        assert "safety_policy" not in (entry.description or "").lower(), entry.env_name


# --------------------------------------------------------------------------- #
# Validator semantics                                                          #
# --------------------------------------------------------------------------- #
def test_tier_zero_validates_nothing():
    assert validate_capability_profile(0, {}) == []
    # Even with every flag dark and visible, tier 0 declares nothing.
    dark = dict.fromkeys(SATISFIED_TIER1, False)
    assert validate_capability_profile(0, dark) == []


def test_tier_one_with_satisfied_flags_has_no_violations():
    """Tier 1 is declarable once every requirement is bound and satisfied.

    S16 bound retention_cleanup to FEATURE_AI_RETENTION_JOBS, removing the
    last deliberately-unbound tier-1 requirement.
    """
    assert validate_capability_profile(1, dict(SATISFIED_TIER1)) == []


@pytest.mark.parametrize(
    ("flag", "bad_value", "expected"),
    [
        ("FEATURE_AI_THREAD_SCOPE_ENFORCE", False, "scope_enforce"),
        ("FEATURE_AI_ANALYSIS_ROUTER_ENFORCE", False, "direct_analysis_routing"),
        ("AIMMS_EVIDENCE_GATE_MODE", "shadow", "evidence_gate_enforce"),
        ("FEATURE_TOKEN_STREAMING", True, "buffered_output"),
        (
            "AIMMS_MAINTENANCE_SCOPE_RESOLVER",
            "tasks.scope.single_site_scope_resolver",
            "fixture_isolation",
        ),
        ("FEATURE_AI_RETENTION_JOBS", False, "retention_cleanup"),
    ],
)
def test_each_unsafe_tier_one_combination_is_named(flag, bad_value, expected):
    view = dict(SATISFIED_TIER1)
    view[flag] = bad_value
    violations = validate_capability_profile(1, view)
    assert any(violation.startswith(f"{expected}:") for violation in violations), violations


@pytest.mark.parametrize(
    ("flag", "bad_value"),
    [
        ("FEATURE_THREAD_SHARING", True),
        ("AIMMS_MEDIA_RAG_ENABLED", True),
        ("FEATURE_MEDIA_RAG_INGEST", True),
        ("FEATURE_MEDIA_RAG_RETRIEVAL", True),
        ("FEATURE_WF1_DIAGNOSTICS", True),
        ("MODEL_VERSION_BOOT_PROBE_ENABLED", False),
    ],
)
def test_each_pilot_prohibition_is_named(flag, bad_value):
    view = dict(SATISFIED_TIER1)
    view[flag] = bad_value
    violations = validate_capability_profile(1, view)
    assert any(f"pilot profile: {flag}=" in violation for violation in violations), violations


def test_declaring_an_unbuildable_tier_fails_loudly():
    violations = validate_capability_profile(2, dict(SATISFIED_TIER1))
    unbound = {v.split(":", 1)[0] for v in violations}
    assert {
        "analytics_service",
        "population_enum",
        "exact_membership",
        "load_25k_evidence",
    } <= unbound


def test_invisible_flags_are_the_other_planes_problem():
    """A plane validates only what it bridges; nothing false-positives."""
    ai_only_view = {
        name: value
        for name, value in SATISFIED_TIER1.items()
        if name not in ("AIMMS_MAINTENANCE_SCOPE_RESOLVER", "AIMMS_MEDIA_RAG_ENABLED")
    }
    violations = validate_capability_profile(1, ai_only_view)
    assert not any("fixture_isolation" in violation for violation in violations)


def test_armed_floor_binds_at_every_tier():
    # Tier 0, floor armed, scope enforcement visible but off -> violation.
    view = {"FEATURE_AI_THREAD_SCOPE_ENFORCE": False}
    violations = validate_capability_profile(0, view, floor_armed=True)
    assert len(violations) == 1
    assert violations[0].startswith("scope_enforce:")
    # Floor legs not visible to this plane stay the other plane's problem.
    assert validate_capability_profile(0, {}, floor_armed=True) == []
    # An armed floor never demands unbound tier capabilities.
    assert (
        validate_capability_profile(0, {"FEATURE_AI_THREAD_SCOPE_ENFORCE": True}, floor_armed=True)
        == []
    )


# --------------------------------------------------------------------------- #
# The AI-plane hook                                                            #
# --------------------------------------------------------------------------- #
def test_default_settings_declare_tier_zero_and_construct_cleanly():
    """The S14 flips-nothing pin: today's dark deployment is untouched."""
    settings = _settings()
    assert settings.capability_tier == 0


def test_declaring_tier_one_on_todays_build_fails_startup():
    import pydantic

    with pytest.raises(pydantic.ValidationError) as excinfo:
        _settings(AIMMS_CAPABILITY_TIER=1)
    message = str(excinfo.value)
    assert "AIMMS_CAPABILITY_TIER=1 is not satisfiable" in message
    # The dark flags are named, not summarized (retention_cleanup is now a
    # dark FLAG on today's build, no longer an unbound requirement).
    assert "scope_enforce" in message
    assert "retention_cleanup" in message


def test_tier_is_bounded():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _settings(AIMMS_CAPABILITY_TIER=4)
    with pytest.raises(pydantic.ValidationError):
        _settings(AIMMS_CAPABILITY_TIER=-1)


# --------------------------------------------------------------------------- #
# The per-turn stamp                                                           #
# --------------------------------------------------------------------------- #
def test_terminal_metadata_stamps_the_declared_tier():
    from ai.core.turn.finalize import _terminal_output_metadata

    metadata = _terminal_output_metadata({"existing": True})
    assert metadata["existing"] is True
    assert metadata["capability_tier"] == 0
