"""Detail-profile enrichment for dataset-owned demo work orders.

Section 2.6 of the maintenance plan asks every demo work order to carry a
complete, context-appropriate profile for its class: the component it concerns,
the findings that were recorded, and - where the class supports one - the repair
scope that was approved.

Three rules make re-running this safe:

* **Ownership.** Only cards carrying the full dataset tag set are touched.
  Operator-authored records and cards outside the manifest are never modified,
  and a natural-key collision with unowned data fails rather than overwriting it.
* **Applicability.** A profile must match the record's class. Closeout facts on
  an active repair, or a repair diagnosis on a procurement child, are rejected -
  the demo may not depict a lifecycle state that could not really exist.
* **Idempotence.** Findings are keyed, scope is versioned, and a rerun updates
  what it wrote rather than adding a second copy. Operator edits to schedule,
  assignment, lifecycle and board stage are outside the enrichment boundary and
  are never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

PROFILE_VERSION = 1

# Record classes from the plan's profile matrix, with what each may carry.
CLASS_HISTORICAL_INSPECTION = 'historical_inspection'
CLASS_HISTORICAL_PREVENTIVE = 'historical_preventive'
CLASS_HISTORICAL_CORRECTIVE = 'historical_corrective'
CLASS_ACTIVE_CORRECTIVE = 'active_corrective'
CLASS_PROCUREMENT = 'procurement'

PROFILE_CLASSES = frozenset({
    CLASS_HISTORICAL_INSPECTION,
    CLASS_HISTORICAL_PREVENTIVE,
    CLASS_HISTORICAL_CORRECTIVE,
    CLASS_ACTIVE_CORRECTIVE,
    CLASS_PROCUREMENT,
})

#: Classes whose records may carry an approved repair scope. A procurement child
#: has no machine diagnosis to approve, and an inspection has no repair scope.
_SCOPE_CLASSES = frozenset({CLASS_HISTORICAL_CORRECTIVE, CLASS_ACTIVE_CORRECTIVE})

#: Classes whose records may carry investigation findings. Procurement children
#: record sourcing activity, not observations about the machine.
_FINDING_CLASSES = frozenset({
    CLASS_HISTORICAL_INSPECTION,
    CLASS_HISTORICAL_PREVENTIVE,
    CLASS_HISTORICAL_CORRECTIVE,
    CLASS_ACTIVE_CORRECTIVE,
})


class EnrichmentError(Exception):
    """A profile is malformed or incompatible with its record."""

    code = 'ENRICHMENT_INVALID'


@dataclass
class CoverageReport:
    """What one enrichment pass discovered and changed."""

    discovered: int = 0
    enriched: int = 0
    missing_profile: list[str] = field(default_factory=list)
    findings_written: int = 0
    scopes_written: int = 0
    unchanged: int = 0
    by_class: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        """Serialize for the command's summary output."""
        return {
            'discovered': self.discovered,
            'enriched': self.enriched,
            'missing_profile': sorted(self.missing_profile),
            'findings_written': self.findings_written,
            'scopes_written': self.scopes_written,
            'unchanged': self.unchanged,
            'by_class': dict(sorted(self.by_class.items())),
        }

    @property
    def complete(self) -> bool:
        """Whether every discovered owned card carried a profile."""
        return not self.missing_profile


def validate_profile(profile, *, reference: str, card_kind: str, is_terminal: bool):
    """Validate one profile and return it normalized.

    Raises :class:`EnrichmentError` naming the record, so a manifest mistake
    points at the row that caused it rather than at the loader.
    """
    if not isinstance(profile, dict):
        raise EnrichmentError(f'{reference}: detail_profile must be an object.')

    record_class = profile.get('class')
    if record_class not in PROFILE_CLASSES:
        raise EnrichmentError(
            f'{reference}: unknown profile class {record_class!r}; expected one '
            f'of {", ".join(sorted(PROFILE_CLASSES))}.'
        )

    version = profile.get('profile_version', PROFILE_VERSION)
    if version != PROFILE_VERSION:
        raise EnrichmentError(
            f'{reference}: unsupported profile_version {version!r}; '
            f'expected {PROFILE_VERSION}.'
        )

    # Lifecycle truth: completed work never shows active-repair controls, and
    # active work never shows facts that only closeout can establish.
    if record_class == CLASS_ACTIVE_CORRECTIVE and is_terminal:
        raise EnrichmentError(
            f'{reference}: an active_corrective profile cannot be applied to a '
            'completed or canceled record.'
        )
    if record_class.startswith('historical_') and not is_terminal:
        raise EnrichmentError(
            f'{reference}: a historical profile cannot be applied to work that '
            'is still open.'
        )
    if record_class == CLASS_PROCUREMENT and card_kind != 'procurement':
        raise EnrichmentError(
            f'{reference}: a procurement profile requires a procurement card.'
        )

    findings = profile.get('findings') or []
    if findings and record_class not in _FINDING_CLASSES:
        raise EnrichmentError(
            f'{reference}: a {record_class} record may not carry investigation '
            'findings.'
        )
    if not isinstance(findings, list):
        raise EnrichmentError(f'{reference}: findings must be a list.')

    scope = profile.get('approved_scope')
    if scope is not None and record_class not in _SCOPE_CLASSES:
        raise EnrichmentError(
            f'{reference}: a {record_class} record may not carry an approved '
            'repair scope.'
        )

    component = profile.get('affected_component') or {}
    if not isinstance(component, dict):
        raise EnrichmentError(f'{reference}: affected_component must be an object.')

    normalized_findings = []
    seen_keys = set()
    for index, entry in enumerate(findings):
        if not isinstance(entry, dict):
            raise EnrichmentError(f'{reference}: finding {index} must be an object.')
        key = str(entry.get('key') or '').strip()
        if not key:
            raise EnrichmentError(
                f'{reference}: finding {index} needs a stable key so a rerun '
                'updates it rather than duplicating it.'
            )
        if key in seen_keys:
            raise EnrichmentError(f'{reference}: finding key {key!r} is repeated.')
        seen_keys.add(key)
        if not str(entry.get('observation') or '').strip():
            raise EnrichmentError(f'{reference}: finding {key} needs an observation.')
        normalized_findings.append(entry)

    return {
        'class': record_class,
        'profile_version': version,
        'affected_component': {
            'name': str(component.get('name') or '')[:200],
            'external_id': str(component.get('external_id') or '')[:64],
        },
        'findings': normalized_findings,
        'approved_scope': scope,
    }


@transaction.atomic
def apply_profile(card, profile, *, dataset: str, report: CoverageReport, actor=None):
    """Apply one validated profile to an owned card.

    Only the enrichment boundary is written: the affected component on the card,
    and findings / approved scope on its packet. Schedule, assignment, lifecycle
    and board stage belong to the operator and are left alone.
    """
    from repair import investigation

    component = profile['affected_component']
    updates = {}
    if component['name'] and card.affected_component != component['name']:
        updates['affected_component'] = component['name']
    if (
        component['external_id']
        and card.affected_component_ref != component['external_id']
    ):
        updates['affected_component_ref'] = component['external_id']

    if updates:
        for name, value in updates.items():
            setattr(card, name, value)
        card.save(update_fields=[*updates, 'updated_at'])

    packet = getattr(card, 'repair_packet', None)
    changed = bool(updates)

    if profile['findings']:
        if packet is None:
            # Findings live on the fault-to-fix aggregate. A record without one
            # cannot hold them, and inventing a packet for a completed
            # inspection would misrepresent how that work was actually governed.
            raise EnrichmentError(
                f'{card.reference}: findings require a repair packet, and this '
                'record has none.'
            )
        for sequence, entry in enumerate(profile['findings'], start=1):
            _, created = investigation.record_finding(
                packet,
                finding_key=str(entry['key'])[:64],
                observation=str(entry['observation']),
                category=entry.get('category', 'other'),
                value=entry.get('value'),
                unit=str(entry.get('unit') or ''),
                evidence_source=str(entry.get('evidence_source') or ''),
                verification=entry.get('verification', 'unverified'),
                actor=actor,
                sequence=sequence,
            )
            report.findings_written += 1
            changed = changed or created

    scope = profile['approved_scope']
    if scope:
        if packet is None:
            raise EnrichmentError(
                f'{card.reference}: an approved scope requires a repair packet.'
            )
        current = investigation.current_scope(packet)
        # Re-approving an identical scope would create a pointless new version
        # and a misleading second approval event.
        if current is None or _scope_differs(current, scope):
            investigation.approve_repair_scope(
                packet,
                scope_lines=scope.get('lines') or [],
                verified_cause=str(scope.get('verified_cause') or ''),
                failure_codes=scope.get('failure_codes'),
                crew_size=scope.get('crew_size'),
                planned_elapsed_minutes=scope.get('planned_elapsed_minutes'),
                actor=actor,
                note=f'Seeded by {dataset} profile v{profile["profile_version"]}',
            )
            report.scopes_written += 1
            changed = True

    if changed:
        report.enriched += 1
    else:
        report.unchanged += 1

    report.by_class[profile['class']] = report.by_class.get(profile['class'], 0) + 1


def _scope_differs(current, desired) -> bool:
    """Whether a desired scope differs from the one currently in force."""
    desired_actions = [
        line if isinstance(line, str) else str(line.get('action') or '')
        for line in (desired.get('lines') or [])
    ]
    current_actions = [line.get('action') for line in (current.scope_lines or [])]
    return (
        desired_actions != current_actions
        or str(desired.get('verified_cause') or '').strip() != current.verified_cause
        or desired.get('crew_size') != current.crew_size
        or desired.get('planned_elapsed_minutes') != current.planned_elapsed_minutes
    )
