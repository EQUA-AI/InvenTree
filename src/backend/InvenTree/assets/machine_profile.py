"""Schema and validation for the machine knowledge profile (S25).

One JSONField on ``AssetMachine`` holds operator-declared knowledge the
relational schema has no columns for: criticality, maintenance strategy,
component structure, hazardous energy sources, known fault codes and
approved spares. Validation lives HERE and is enforced in the serializer,
never at the DB level -- existing rows, admin edits and migrations must not
be able to brick reads, and a stored value that has drifted out of schema
degrades to "no declared profile" instead of an exception.

The declared profile is also an S27 input: fault codes, spares and energy
sources become enum closure sets that a generated answer's identifiers are
checked against, which is why every list is bounded and every value typed.
"""

from django.core.exceptions import ValidationError

#: Stamped into API payloads and prompts so consumers can detect the shape.
MACHINE_PROFILE_CLASS = 'aimms.machine_profile.v1'

CRITICALITY_VALUES = ('critical', 'high', 'medium', 'low')
MAINTENANCE_STRATEGY_VALUES = ('reactive', 'preventive', 'predictive', 'run_to_failure')

MAX_COMPONENTS = 50
MAX_FAULT_CODES = 50
MAX_APPROVED_SPARES = 50
MAX_NAME_CHARS = 255
MAX_REF_CHARS = 64
MAX_CODE_CHARS = 64
MAX_SPARE_CHARS = 100

#: The complete set of allowed top-level keys. Unknown keys are rejected so a
#: typo ("fault_code") fails loudly at write time instead of storing dead
#: data no reader will ever surface.
ALLOWED_KEYS = frozenset({
    'criticality',
    'maintenance_strategy',
    'components',
    'energy_sources',
    'fault_codes',
    'approved_spares',
})


def _allowed_energy_sources() -> tuple[str, ...]:
    """The LOTO energy-source vocabulary, imported lazily.

    ``repair`` imports ``assets`` (RepairPacket FKs AssetMachine), so a
    module-level import here would be circular at app load.
    """
    from repair.models import LockoutPoint

    return tuple(LockoutPoint.EnergySource.values)


def _require_string_list(
    value, *, key: str, max_items: int, max_chars: int
) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f'{key} must be a list of strings')
    if len(value) > max_items:
        raise ValidationError(f'{key} accepts at most {max_items} entries')
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(f'{key} entries must be non-empty strings')
        if len(item) > max_chars:
            raise ValidationError(
                f'{key} entries are limited to {max_chars} characters'
            )
        cleaned.append(item.strip())
    if len(set(cleaned)) != len(cleaned):
        raise ValidationError(f'{key} entries must be unique')
    return cleaned


def _validate_components(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValidationError('components must be a list')
    if len(value) > MAX_COMPONENTS:
        raise ValidationError(f'components accepts at most {MAX_COMPONENTS} entries')

    cleaned: list[dict] = []
    refs: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValidationError('each component must be an object')
        unknown = set(entry) - {'name', 'ref', 'parent_ref'}
        if unknown:
            raise ValidationError(
                'component keys are name, ref and parent_ref; unknown: '
                + ', '.join(sorted(unknown))
            )
        name = entry.get('name')
        ref = entry.get('ref')
        if not isinstance(name, str) or not name.strip():
            raise ValidationError('each component requires a non-empty name')
        if len(name) > MAX_NAME_CHARS:
            raise ValidationError(
                f'component names are limited to {MAX_NAME_CHARS} characters'
            )
        if not isinstance(ref, str) or not ref.strip():
            raise ValidationError('each component requires a non-empty ref')
        if len(ref) > MAX_REF_CHARS:
            raise ValidationError(
                f'component refs are limited to {MAX_REF_CHARS} characters'
            )
        ref = ref.strip()
        if ref in refs:
            raise ValidationError(f'component ref {ref!r} is declared twice')
        refs.add(ref)
        item = {'name': name.strip(), 'ref': ref}
        if 'parent_ref' in entry:
            parent_ref = entry['parent_ref']
            if not isinstance(parent_ref, str) or not parent_ref.strip():
                raise ValidationError(
                    'parent_ref must be a non-empty string when present'
                )
            item['parent_ref'] = parent_ref.strip()
        cleaned.append(item)

    for item in cleaned:
        parent_ref = item.get('parent_ref')
        if parent_ref is None:
            continue
        if parent_ref == item['ref']:
            raise ValidationError(f'component {item["ref"]!r} cannot be its own parent')
        if parent_ref not in refs:
            # A dangling parent silently flattens the hierarchy for every
            # reader; failing at write time is the only visible moment.
            raise ValidationError(
                f'component {item["ref"]!r} names unknown parent {parent_ref!r}'
            )
    return cleaned


def validate_machine_profile(value) -> dict:
    """Validate and normalize a machine profile; raise ``ValidationError``.

    Returns the cleaned profile (stripped strings, normalized lists). An
    empty dict is explicitly valid: "no declared profile" is the default
    state of every machine.
    """
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValidationError('profile must be an object')

    unknown = set(value) - ALLOWED_KEYS
    if unknown:
        raise ValidationError(
            'unknown profile keys: ' + ', '.join(sorted(str(key) for key in unknown))
        )

    cleaned: dict = {}

    if 'criticality' in value:
        if value['criticality'] not in CRITICALITY_VALUES:
            raise ValidationError(
                'criticality must be one of: ' + ', '.join(CRITICALITY_VALUES)
            )
        cleaned['criticality'] = value['criticality']

    if 'maintenance_strategy' in value:
        if value['maintenance_strategy'] not in MAINTENANCE_STRATEGY_VALUES:
            raise ValidationError(
                'maintenance_strategy must be one of: '
                + ', '.join(MAINTENANCE_STRATEGY_VALUES)
            )
        cleaned['maintenance_strategy'] = value['maintenance_strategy']

    if 'components' in value:
        cleaned['components'] = _validate_components(value['components'])

    if 'energy_sources' in value:
        sources = _require_string_list(
            value['energy_sources'],
            key='energy_sources',
            max_items=len(_allowed_energy_sources()),
            max_chars=MAX_CODE_CHARS,
        )
        allowed = set(_allowed_energy_sources())
        outside = [source for source in sources if source not in allowed]
        if outside:
            raise ValidationError(
                'energy_sources must use the lockout-point vocabulary; unknown: '
                + ', '.join(outside)
            )
        cleaned['energy_sources'] = sources

    if 'fault_codes' in value:
        cleaned['fault_codes'] = _require_string_list(
            value['fault_codes'],
            key='fault_codes',
            max_items=MAX_FAULT_CODES,
            max_chars=MAX_CODE_CHARS,
        )

    if 'approved_spares' in value:
        cleaned['approved_spares'] = _require_string_list(
            value['approved_spares'],
            key='approved_spares',
            max_items=MAX_APPROVED_SPARES,
            max_chars=MAX_SPARE_CHARS,
        )

    return cleaned


def declared_profile(machine) -> dict:
    """The stored profile if it still validates, else ``{}``.

    Reads never raise on stored data: a profile that has drifted out of
    schema (manual DB edit, future version rollback) degrades to "nothing
    declared" rather than failing the tool call that asked.
    """
    try:
        return validate_machine_profile(machine.profile)
    except ValidationError:
        return {}


def observed_energy_sources(machine) -> list[str]:
    """Energy sources actually locked out on this machine's repair packets.

    Server-observed ground truth beside the operator's declaration: a source
    that appears here was physically isolated during a real job, whatever
    the declared profile says.
    """
    from repair.models import LockoutPoint

    return sorted(
        LockoutPoint.objects
        .filter(gate__packet__machine=machine)
        # The model's default ordering (created_at) would join the DISTINCT
        # and duplicate every source once per lockout row.
        .order_by()
        .values_list('energy_source', flat=True)
        .distinct()
    )


def profile_claim_section(machine) -> dict:
    """Compact profile section for the Luna diagnostic machine claim.

    Declared values only where they exist, trimmed to what a diagnosis can
    use; the whole claim is already marked untrusted downstream, so no
    fencing happens here.
    """
    declared = declared_profile(machine)
    section: dict = {'profile_class': MACHINE_PROFILE_CLASS}
    for key in ('criticality', 'maintenance_strategy'):
        if key in declared:
            section[key] = declared[key]
    if 'energy_sources' in declared:
        section['declared_energy_sources'] = declared['energy_sources']
    if 'fault_codes' in declared:
        section['fault_codes'] = declared['fault_codes'][:10]
    if 'approved_spares' in declared:
        section['approved_spares'] = declared['approved_spares'][:10]
    if 'components' in declared:
        section['component_count'] = len(declared['components'])
    observed = observed_energy_sources(machine)
    if observed:
        section['observed_energy_sources'] = observed
    return section


__all__ = [
    'ALLOWED_KEYS',
    'CRITICALITY_VALUES',
    'MACHINE_PROFILE_CLASS',
    'MAINTENANCE_STRATEGY_VALUES',
    'MAX_APPROVED_SPARES',
    'MAX_COMPONENTS',
    'MAX_FAULT_CODES',
    'declared_profile',
    'observed_energy_sources',
    'profile_claim_section',
    'validate_machine_profile',
]
