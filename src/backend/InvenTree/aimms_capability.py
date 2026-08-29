"""AIMMS capability-tier profile — the A12 declaration (S14, §14).

The capability tier is a DEPLOYMENT/SERVER profile: it is declared by the
``AIMMS_CAPABILITY_TIER`` environment value, validated at startup on both
planes, and recorded on every terminal turn — never inferred from user
names, quota profiles, or prompt text.

This module declares, per tier, which platform capabilities must hold
before that tier may be served, plus the pilot-profile prohibitions and
the rollback floor. Like ``aimms_flags`` it is stdlib-only and imported by
both planes; it never reads the environment itself:

- AI plane: a ``Settings`` model validator calls
  :func:`validate_capability_profile` with the registry-derived flag view —
  violations abort process startup.
- Django plane: a system check in ``aichat.apps`` does the same with the
  bridged settings — violations fail ``manage.py check`` and startup.

Each hook validates only the requirements whose flag it can SEE (each
plane bridges its own subset of the registry); a CI test asserts every
flag-backed requirement is visible to at least one plane, so nothing
falls through the split.

Semantics that make mis-declaration loud instead of silent:

- Tier 0 (the default) has NO requirements — today's dark deployment is
  untouched, and a test pins that.
- An ``unbound`` requirement at a declared tier is itself a violation:
  declaring a tier this build cannot express fails at startup instead of
  silently serving a weaker profile.
- The rollback floor (scope enforcement, unsafe-shortcut guard, fixture
  isolation) can be ARMED by a human operator (``arm_rollback_floor``);
  once armed it must hold at every tier including 0 — rollback below the
  floor is prohibited (§14).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The declared tier env name (also a FlagEntry in ``aimms_flags``).
CAPABILITY_TIER_ENV = 'AIMMS_CAPABILITY_TIER'


@dataclass(frozen=True)
class TierRequirement:
    """One capability a tier depends on.

    ``check`` kinds:

    - ``flag`` — an ``aimms_flags`` registry entry must hold
      ``required_value`` (validated by whichever plane bridges it).
    - ``code`` — the capability is unconditional shipped code with no
      disabling flag (the strongest form; CI pins that no registry entry
      can dark it).
    - ``unbound`` — the capability is REQUIRED by the tier but this build
      has nothing to bind it to yet; declaring the tier is a violation.
    """

    name: str
    min_tier: int
    check: str  # flag | code | unbound
    env_name: str | None = None
    required_value: object = True
    note: str = ''


TIER_REQUIREMENTS: tuple[TierRequirement, ...] = (
    # --- Tier >= 1: safe supervised lookup -------------------------------
    TierRequirement(
        'scope_enforce',
        1,
        'flag',
        env_name='FEATURE_AI_THREAD_SCOPE_ENFORCE',
        note='Explicit thread scope filters retrieval; the rollback floor once flipped.',
    ),
    TierRequirement(
        'direct_analysis_routing',
        1,
        'flag',
        env_name='FEATURE_AI_ANALYSIS_ROUTER_ENFORCE',
        note='ANALYSIS-intent turns route to the evidence rail, not diagnostics.',
    ),
    TierRequirement(
        'evidence_gate_enforce',
        1,
        'flag',
        env_name='AIMMS_EVIDENCE_GATE_MODE',
        required_value='enforce',
        note='Validated v2 evidence answers; human-gated after the shadow soak.',
    ),
    TierRequirement(
        'buffered_output',
        1,
        'flag',
        env_name='FEATURE_TOKEN_STREAMING',
        required_value=False,
        note='Content-free progress only before validation (§7.5).',
    ),
    TierRequirement(
        'canonical_v2',
        1,
        'code',
        note='The response_version-discriminated union is the shipped path (S10).',
    ),
    TierRequirement(
        'shortcut_guard',
        1,
        'code',
        note='S4 unsafe-shortcut refusal has no disabling flag by design.',
    ),
    TierRequirement(
        'fixture_isolation',
        1,
        'flag',
        env_name='AIMMS_MAINTENANCE_SCOPE_RESOLVER',
        required_value='tasks.scope.granted_client_scope_resolver',
        note='Eval fixtures bind to positive grants, never name matching (S6).',
    ),
    TierRequirement(
        'final_authorization',
        1,
        'code',
        note=(
            'C13 final live reauthorization ships unconditionally inside the '
            'evidence validator; tier 1 requires the gate at enforce, which '
            'makes it binding on every served answer.'
        ),
    ),
    TierRequirement(
        'retention_cleanup',
        1,
        'flag',
        env_name='FEATURE_AI_RETENTION_JOBS',
        note=(
            'Q48/S16 purge and reconciliation jobs must be OPERATING (flag '
            'on) in any deployment declaring tier >= 1 — shipped-but-dark '
            'does not satisfy the retention gate.'
        ),
    ),
    # --- Tier >= 2: complete-population historical analysis --------------
    TierRequirement('analytics_service', 2, 'unbound', note='S7 analytics service.'),
    TierRequirement(
        'population_enum', 2, 'unbound', note='S7 population coverage enum.'
    ),
    TierRequirement(
        'exact_membership', 2, 'unbound', note='S7 exact evidence membership.'
    ),
    TierRequirement(
        'load_25k_evidence', 2, 'unbound', note='§13.1 25k load/storage evidence.'
    ),
    # --- Tier >= 3: cross-source comparison ------------------------------
    TierRequirement(
        'applicability_relation', 3, 'unbound', note='S8b verified applicability.'
    ),
    TierRequirement(
        'procedure_evidence',
        3,
        'unbound',
        note='Structured applied-procedure evidence.',
    ),
    TierRequirement(
        'comparison_gates', 3, 'unbound', note='S9 comparison eligibility gate.'
    ),
)

#: Pilot-profile prohibitions (§14): while any tier >= 1 is declared, these
#: must hold in addition to the tier requirements.
PILOT_FORBIDDEN: tuple[tuple[str, object], ...] = (
    ('FEATURE_THREAD_SHARING', False),
    ('AIMMS_MEDIA_RAG_ENABLED', False),
    ('FEATURE_MEDIA_RAG_INGEST', False),
    ('FEATURE_MEDIA_RAG_RETRIEVAL', False),
    ('FEATURE_WF1_DIAGNOSTICS', False),
    ('MODEL_VERSION_BOOT_PROBE_ENABLED', True),
)

#: Requirement NAMES forming the rollback floor: once armed, these must hold
#: at every tier and can never be disabled again (§14 monotonic safety).
ROLLBACK_FLOOR: tuple[str, ...] = (
    'scope_enforce',
    'shortcut_guard',
    'fixture_isolation',
)

#: The human-gated floor marker (written by ``manage.py arm_rollback_floor``
#: into ``InvenTreeSetting``; the leading underscore marks an internal key).
ROLLBACK_FLOOR_SETTING = '_AIMMS_ROLLBACK_FLOOR_ARMED'


def _violation(requirement: TierRequirement, actual: object) -> str:
    return (
        f'{requirement.name}: {requirement.env_name}={actual!r} but the declared '
        f'capability profile requires {requirement.required_value!r}'
    )


def validate_capability_profile(
    tier: int, flags: dict[str, object], floor_armed: bool = False
) -> list[str]:
    """Return every violation of the declared tier against visible flags.

    ``flags`` maps registry env names to the calling plane's effective
    values; requirements whose flag is not present are validated by the
    other plane's hook. Empty list = the profile is valid as far as this
    plane can see.
    """
    violations: list[str] = []
    tier = int(tier)

    for requirement in TIER_REQUIREMENTS:
        required_here = requirement.min_tier <= tier
        floor_here = floor_armed and requirement.name in ROLLBACK_FLOOR
        if not required_here and not floor_here:
            continue
        if requirement.check == 'code':
            continue
        if requirement.check == 'unbound':
            if required_here:
                violations.append(
                    f'{requirement.name}: required by tier {requirement.min_tier} '
                    f'but no implementation is bound in this build'
                    + (f' ({requirement.note})' if requirement.note else '')
                )
            continue
        if requirement.env_name not in flags:
            continue  # the other plane's hook owns this flag
        actual = flags[requirement.env_name]
        if actual != requirement.required_value:
            violations.append(_violation(requirement, actual))

    if tier >= 1:
        for env_name, required in PILOT_FORBIDDEN:
            if env_name in flags and flags[env_name] != required:
                violations.append(
                    f'pilot profile: {env_name}={flags[env_name]!r} but the pilot '
                    f'requires {required!r}'
                )

    return violations


__all__ = [
    'CAPABILITY_TIER_ENV',
    'PILOT_FORBIDDEN',
    'ROLLBACK_FLOOR',
    'ROLLBACK_FLOOR_SETTING',
    'TIER_REQUIREMENTS',
    'TierRequirement',
    'validate_capability_profile',
]
