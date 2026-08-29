"""AIMMS rollback floor — monotonic safety once enforcement is live (§14).

The floor is the minimum safety configuration that can never be rolled
back once a human operator ARMS it (``manage.py arm_rollback_floor``,
one-way by design): explicit thread-scope enforcement, the S4
unsafe-shortcut guard, and eval-fixture isolation via the granted
resolver. Arming writes a DB marker; from then on the Django system
check in ``aichat.apps`` fails startup loudly whenever a floor leg is
dark — in every configuration, not just some declared profile.

Like ``aimms_flags`` this module is stdlib-only and never reads the
environment itself. The check validates only the flags the calling
plane bridges (a leg whose flag is not in the provided view is the
other plane's problem); the ``code`` leg needs no validation at all —
CI pins that no registry entry can dark the shortcut guard.

The pilot-era capability-tier machinery (``AIMMS_CAPABILITY_TIER``,
per-tier requirement tables, ``PILOT_FORBIDDEN``) was removed by owner
decision 2026-08-29: the pilot is the production test, and nothing may
force features off (or retention on) as a precondition of serving.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FloorRequirement:
    """One rollback-floor leg.

    ``check`` kinds:

    - ``flag`` — an ``aimms_flags`` registry entry must hold
      ``required_value`` (validated by whichever plane bridges it).
    - ``code`` — unconditional shipped code with no disabling flag (the
      strongest form; CI pins that no registry entry can dark it).
    """

    name: str
    check: str  # flag | code
    env_name: str | None = None
    required_value: object = True
    note: str = ''


FLOOR_REQUIREMENTS: tuple[FloorRequirement, ...] = (
    FloorRequirement(
        'scope_enforce',
        'flag',
        env_name='FEATURE_AI_THREAD_SCOPE_ENFORCE',
        note='Explicit thread scope filters retrieval; armed = never dark again.',
    ),
    FloorRequirement(
        'shortcut_guard',
        'code',
        note='S4 unsafe-shortcut refusal has no disabling flag by design.',
    ),
    FloorRequirement(
        'fixture_isolation',
        'flag',
        env_name='AIMMS_MAINTENANCE_SCOPE_RESOLVER',
        required_value='tasks.scope.granted_client_scope_resolver',
        note='Eval fixtures bind to positive grants, never name matching (S6).',
    ),
)

#: Requirement NAMES forming the rollback floor (frozen; a silent edit to
#: what the floor means must fail review).
ROLLBACK_FLOOR: tuple[str, ...] = tuple(r.name for r in FLOOR_REQUIREMENTS)

#: The human-gated floor marker (written by ``manage.py arm_rollback_floor``
#: into ``InvenTreeSetting``; the leading underscore marks an internal key).
ROLLBACK_FLOOR_SETTING = '_AIMMS_ROLLBACK_FLOOR_ARMED'


def validate_rollback_floor(flags: dict[str, object]) -> list[str]:
    """Return every armed-floor violation visible to the calling plane.

    ``flags`` maps registry env names to the caller's effective values;
    legs whose flag is not present are validated by the other plane's
    hook. Empty list = the floor holds as far as this plane can see.
    """
    violations: list[str] = []
    for requirement in FLOOR_REQUIREMENTS:
        if requirement.check != 'flag':
            continue  # code legs ship unconditionally; CI pins undarkability
        if requirement.env_name not in flags:
            continue  # the other plane's hook owns this flag
        actual = flags[requirement.env_name]
        if actual != requirement.required_value:
            violations.append(
                f'{requirement.name}: {requirement.env_name}={actual!r} but the '
                f'armed rollback floor requires {requirement.required_value!r} '
                f'({requirement.note})'
            )
    return violations
