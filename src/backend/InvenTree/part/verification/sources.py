"""Read-only source adapters and fingerprints for verification.

Adapters read authoritative owners (catalog, BOM, assets, generic parameters,
accepted evidence) through narrow, allowlisted projections and never mutate
them. Every projection is canonicalizable and fingerprintable so decisions can
snapshot exactly what was read (spec section 8.2).
"""

from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from part.verification.schema import EvidenceDecision, HashDomains, hash_canonical


@dataclass(frozen=True)
class SourceFact:
    """One raw fact read from an authoritative source."""

    key: str
    raw_value: object
    unit: str
    authority: str
    source_kind: str
    source_model: str
    source_object_id: str
    source_field: str = ''
    source_fingerprint: str = ''
    evidence_id: int | None = None
    provenance_extra: dict = field(default_factory=dict)

    def provenance(self) -> dict:
        """Return the canonical provenance reference for this fact."""
        ref = {
            'authority': self.authority,
            'source_kind': self.source_kind,
            'source_model': self.source_model,
            'source_object_id': self.source_object_id,
            'source_field': self.source_field,
            'source_fingerprint': self.source_fingerprint,
        }
        if self.evidence_id is not None:
            ref['evidence_id'] = self.evidence_id
        return ref


def snapshot_part(part) -> dict:
    """Return the canonical identity snapshot of a Part."""
    if part is None:
        return {}
    return {
        'id': part.pk,
        'name': part.name,
        'ipn': part.IPN or '',
        'revision': part.revision or '',
        'revision_of': part.revision_of_id,
        'variant_of': part.variant_of_id,
        'category': part.category_id,
        'units': part.units or '',
        'active': part.active,
        'locked': part.locked,
        'component': part.component,
        'purchaseable': part.purchaseable,
        'trackable': part.trackable,
        'description': part.description or '',
    }


def snapshot_part_parameters(part) -> list:
    """Return the canonical projection of a part's enabled generic parameters.

    Parameter values are technical source facts (spec section 14.2): they must
    participate in source fingerprints so a rating change after evaluation or
    confirmation is detected by revalidation, not silently accepted.
    """
    from common.models import Parameter

    if part is None:
        return []

    content_type = ContentType.objects.get_for_model(type(part))
    rows = (
        Parameter.objects
        .filter(model_type=content_type, model_id=part.pk, template__enabled=True)
        .select_related('template')
        .order_by('template__name', 'pk')
    )
    return [
        {
            'template': row.template.name,
            'data': row.data,
            'units': row.template.units or '',
        }
        for row in rows
    ]


def snapshot_candidate(part) -> dict:
    """Return the full candidate snapshot: identity plus parameter facts."""
    return {
        'identity': snapshot_part(part),
        'parameters': snapshot_part_parameters(part),
    }


def snapshot_bom_item(bom_item) -> dict:
    """Return the canonical snapshot of a BOM line and its explicit substitutes."""
    if bom_item is None:
        return {}
    return {
        'id': bom_item.pk,
        'assembly': bom_item.part_id,
        'sub_part': bom_item.sub_part_id,
        'reference': bom_item.reference or '',
        'quantity': str(bom_item.quantity),
        'allow_variants': bom_item.allow_variants,
        'inherited': bom_item.inherited,
        'validated': bom_item.validated,
        'checksum': bom_item.checksum or '',
        'substitutes': sorted(bom_item.substitutes.values_list('part_id', flat=True)),
    }


def snapshot_machine(machine) -> dict:
    """Return the canonical snapshot of an asset machine."""
    if machine is None:
        return {}
    return {
        'id': machine.pk,
        'name': machine.name,
        'client': machine.client_id,
        'manufacturer': machine.manufacturer or '',
        'model': machine.model or '',
        'serial': machine.serial or '',
        'active': machine.active,
    }


def snapshot_machine_part(machine_part) -> dict:
    """Return the canonical snapshot of an installed machine part row."""
    if machine_part is None:
        return {}
    return {
        'id': machine_part.pk,
        'machine': machine_part.machine_id,
        'part': machine_part.part_id,
        'quantity': machine_part.quantity,
    }


def fingerprint(snapshot: dict) -> str:
    """Return the canonical source fingerprint of one snapshot."""
    return hash_canonical(HashDomains.SOURCE, snapshot)


def accepted_evidence(session):
    """Return current accepted, unexpired evidence rows for a session."""
    now = timezone.now()
    rows = session.evidence_items.filter(
        decision=EvidenceDecision.ACCEPTED, superseded_by__isnull=True
    )
    return [row for row in rows if row.expires_at is None or row.expires_at > now]


def _parameter_rows(part, template_name: str):
    """Return generic Parameter rows for a part and template name."""
    from common.models import Parameter

    if part is None:
        return Parameter.objects.none()

    content_type = ContentType.objects.get_for_model(type(part))
    return Parameter.objects.filter(
        model_type=content_type,
        model_id=part.pk,
        template__name=template_name,
        template__enabled=True,
    ).select_related('template')


def parameter_facts(part, template_name: str, requirement_key: str) -> list[SourceFact]:
    """Read generic parameter values for a part as source facts.

    Generic parameters are evidence through the policy's typed mapping, not a
    compatibility ontology by themselves (spec section 1.2).
    """
    facts = []
    for row in _parameter_rows(part, template_name):
        snapshot = {
            'id': row.pk,
            'template': row.template.name,
            'data': row.data,
            'units': row.template.units or '',
        }
        facts.append(
            SourceFact(
                key=requirement_key,
                raw_value=row.data,
                unit=row.template.units or '',
                authority=f'parameter:{template_name}',
                source_kind='parameter',
                source_model='common.parameter',
                source_object_id=str(row.pk),
                source_field='data',
                source_fingerprint=fingerprint(snapshot),
            )
        )
    return facts


def machine_facts(machine, field_name: str, requirement_key: str) -> list[SourceFact]:
    """Read one allowlisted machine field as a source fact."""
    allowed = {'manufacturer', 'model', 'serial', 'name'}
    if machine is None or field_name not in allowed:
        return []

    value = getattr(machine, field_name, '') or ''
    if not value:
        return []

    return [
        SourceFact(
            key=requirement_key,
            raw_value=value,
            unit='',
            authority=f'machine:{field_name}',
            source_kind='machine',
            source_model='assets.assetmachine',
            source_object_id=str(machine.pk),
            source_field=field_name,
            source_fingerprint=fingerprint(snapshot_machine(machine)),
        )
    ]


def bom_identity_facts(bom_item, requirement_key: str) -> list[SourceFact]:
    """Read the requested identity of a BOM line as a source fact."""
    if bom_item is None:
        return []

    sub_part = bom_item.sub_part
    value = sub_part.IPN or sub_part.name

    return [
        SourceFact(
            key=requirement_key,
            raw_value=value,
            unit='',
            authority='bom',
            source_kind='bom',
            source_model='part.bomitem',
            source_object_id=str(bom_item.pk),
            source_field='sub_part',
            source_fingerprint=fingerprint(snapshot_bom_item(bom_item)),
        )
    ]


def observation_facts(session, requirement_key: str) -> list[SourceFact]:
    """Read accepted evidence rows for a requirement key as source facts."""
    facts = []
    for row in accepted_evidence(session):
        if row.requirement_key != requirement_key:
            continue
        value = (
            row.canonical_value if row.canonical_value is not None else row.raw_value
        )
        facts.append(
            SourceFact(
                key=requirement_key,
                raw_value=value,
                unit=row.unit or '',
                authority='observation',
                source_kind=row.source_kind,
                source_model='part.partverificationevidence',
                source_object_id=str(row.pk),
                source_field='canonical_value',
                source_fingerprint=row.source_fingerprint or '',
                evidence_id=row.pk,
            )
        )
    return facts


def facts_for_source(session, source_spec: dict, requirement_key: str):
    """Dispatch one policy source spec to its adapter."""
    kind = source_spec['kind']
    if kind == 'observation':
        return observation_facts(session, requirement_key)
    if kind == 'parameter':
        return parameter_facts(
            session.requested_part, source_spec['template'], requirement_key
        )
    if kind == 'machine':
        return machine_facts(session.machine, source_spec['field'], requirement_key)
    if kind == 'bom':
        return bom_identity_facts(session.bom_item, requirement_key)
    return []


def build_source_observation(session) -> dict:
    """Build the canonical current observation of every bound source.

    This single projection is used both as the session source fingerprint at
    evaluation time and as the current observation during revalidation, so the
    two can never diverge structurally.
    """
    evidence_rows = accepted_evidence(session)

    observation = {
        'schema_version': 1,
        'purpose': session.purpose,
        'scope_fingerprint': session.scope_fingerprint,
        'requested_part': snapshot_part(session.requested_part),
        'requested_part_parameters': snapshot_part_parameters(session.requested_part),
        'machine': snapshot_machine(session.machine),
        'machine_part': snapshot_machine_part(session.machine_part),
        'bom_item': snapshot_bom_item(session.bom_item),
        'policy': {
            'key': session.policy.key,
            'version': session.policy.version,
            'status': session.policy.status,
            'hash': session.policy.definition_hash,
        },
        'evidence': sorted(
            (
                {
                    'id': row.pk,
                    'requirement_key': row.requirement_key,
                    'fingerprint': row.source_fingerprint or '',
                    'value_fingerprint': hash_canonical(
                        HashDomains.EVIDENCE,
                        {
                            'value': _jsonable(row.canonical_value),
                            'unit': row.unit or '',
                        },
                    ),
                }
                for row in evidence_rows
            ),
            key=lambda entry: entry['id'],
        ),
    }
    return observation


def _jsonable(value):
    """Coerce stored JSON values into canonicalizable form.

    JSONField round-trips floats for numeric literals; stored canonical values
    are strings by construction, but raw client payloads may not be. Convert
    floats to their string form so fingerprints never fail on stored rows.
    """
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def source_fingerprint_for_session(session) -> str:
    """Return the combined canonical source fingerprint for a session."""
    return hash_canonical(HashDomains.SOURCE, build_source_observation(session))
