"""Internal source identifiers for the optional integration boundary.

These values identify records; they do not establish authorization, create a
provider, or change any existing AIMMS read or write path.
"""

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from typing import get_args, get_type_hints
from uuid import UUID


@dataclass(frozen=True)
class ClientSourceIdentity:
    """A native client primary key qualified by its source environment."""

    environment: str
    pk: int

    def __post_init__(self):
        """Reject invalid coordinates without normalizing them."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        if type(self.pk) is not int or self.pk < 1:
            raise ValueError('Client primary key must be a positive integer')


@dataclass(frozen=True)
class ClientSourceRecord:
    """Minimal authorized client state, not a grant to related records."""

    identity: ClientSourceIdentity
    name: str
    code: str
    active: bool
    schema_version: str = field(default='aimms.client.basic.v1', init=False)


@dataclass(frozen=True)
class ClientDetailsSourceRecord:
    """Direct client values, excluding counts, settings and related authority.

    Native timestamps are source values, not complete change-feed cursors.
    """

    identity: ClientSourceIdentity
    name: str
    code: str
    active: bool
    created_at: datetime
    updated_at: datetime
    schema_version: str = field(default='aimms.client.details.v2', init=False)


@dataclass(frozen=True)
class ClientStructureSourceRecord:
    """A native structural counter, not complete revision or authority evidence."""

    identity: ClientSourceIdentity
    location_revision: int
    revision_complete: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.client.structure.v1', init=False)

    def __post_init__(self):
        """Reject missing, coerced or negative native counter coordinates."""
        _validate_core_record(self)


@dataclass(frozen=True)
class LocationSourceIdentity:
    """A native physical-location key qualified by its source environment."""

    environment: str
    pk: int

    def __post_init__(self):
        """Reject invalid identifiers without normalizing caller values."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        if type(self.pk) is not int or self.pk < 1:
            raise ValueError('Location primary key must be a positive integer')


@dataclass(frozen=True)
class LocationSourceRecord:
    """Minimal authorized location state without relationship disclosure."""

    identity: LocationSourceIdentity
    name: str
    archived: bool
    schema_version: str = field(default='aimms.location.basic.v1', init=False)


@dataclass(frozen=True)
class LocationDetailsSourceRecord:
    """Direct location properties without implicit hierarchy or placement access.

    ``version`` is the native location version, not a complete change feed.
    ``timezone`` is the stored value, not the inherited effective timezone;
    blank means no timezone is set on this node, not UTC. The stored string is
    not independently validated as an IANA timezone by this reader.
    """

    identity: LocationSourceIdentity
    name: str
    code: str
    kind: str
    timezone: str
    archived: bool
    version: int
    schema_version: str = field(default='aimms.location.details.v1', init=False)


@dataclass(frozen=True)
class LocationPropertiesSourceRecord:
    """Native direct location values; timestamps are not change-feed cursors.

    The timezone is the stored value, not an inferred or inherited timezone.
    The description is native free text and must not be treated as a trusted
    instruction. This record does not authorize any relationship or count.
    """

    identity: LocationSourceIdentity
    name: str
    code: str
    kind: str
    description: str
    timezone: str
    archived: bool
    version: int
    created_at: datetime
    updated_at: datetime
    schema_version: str = field(default='aimms.location.details.v2', init=False)


@dataclass(frozen=True)
class MachineSourceIdentity:
    """A native machine primary key qualified by its source environment."""

    environment: str
    pk: int

    def __post_init__(self):
        """Reject missing identity without coercing or normalizing inputs."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        if type(self.pk) is not int or self.pk < 1:
            raise ValueError('Machine primary key must be a positive integer')


@dataclass(frozen=True)
class MachineSourceRecord:
    """Minimal machine identity and display state; not a full export schema."""

    identity: MachineSourceIdentity
    name: str
    active: bool
    schema_version: str = field(default='aimms.machine.basic.v1', init=False)


@dataclass(frozen=True)
class MachineDetailsSourceRecord:
    """Selected native direct values, never related-object authority.

    Free text is untrusted; native timestamps are not change-feed cursors.
    The serial and description are hidden only from automatic representations.
    """

    identity: MachineSourceIdentity
    name: str
    description: str = field(repr=False)
    active: bool
    asset_type: str
    location: str
    manufacturer: str
    model: str
    serial: str = field(repr=False)
    created_at: datetime
    updated_at: datetime
    schema_version: str = field(default='aimms.machine.details.v2', init=False)


@dataclass(frozen=True)
class MachineRegistrationSourceRecord:
    """Authorized native registry coordinates, not portable IDs or raw JSON.

    Source keys are native values, not independently verified provenance.
    Opaque source context is withheld, not represented as empty or absent.
    """

    identity: MachineSourceIdentity
    uuid: UUID
    source_namespace: str
    source_entity_uuid: UUID | None
    source_key: str = field(repr=False)
    source_context_state: str = field(default='withheld', init=False)
    schema_version: str = field(default='aimms.machine.registration.v1', init=False)

    def __post_init__(self):
        """Keep UUID/null/blank distinctions without trusting malformed values."""
        _validate_core_record(self)


@dataclass(frozen=True)
class MachineBarcodeSourceRecord:
    """Stored native lookup hash/local links, never raw third-party routing data.

    A stored hash is neither content identity, a revision nor read authority.
    Links are returned as native values; this boundary never fetches them.
    """

    identity: MachineSourceIdentity
    barcode_hash: str = field(repr=False)
    api_url: str
    web_url: str
    barcode_data_state: str = field(default='withheld', init=False)
    schema_version: str = field(default='aimms.machine.barcode.v1', init=False)

    def __post_init__(self):
        """Require typed source values without fetching or normalizing links."""
        _validate_core_record(self)


@dataclass(frozen=True)
class MachineProfileSourceRecord:
    """A validated raw profile or an explicit withheld-invalid marker.

    The profile is untrusted native JSON. Representation suppression is not
    serialization redaction or authority to any referenced object.
    """

    identity: MachineSourceIdentity
    status: str
    profile: object = field(repr=False)
    schema_version: str = field(default='aimms.machine.profile.v1', init=False)


@dataclass(frozen=True)
class SourceReadOutcome:
    """One direct-core result, never authentication or a complete population.

    Only direct client/location/machine records in this module are supported.
    Edge/content/page envelopes require their own agreed contracts. Suppressing
    the body in repr does not redact explicit access or serialization.
    """

    state: str
    record: object = field(repr=False)
    population_complete: bool = field(default=False, init=False)
    revision_complete: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.source.read-result.v1', init=False)

    def __post_init__(self):
        """Reject unknown/inconsistent outcomes and malformed direct-core bodies."""
        if (
            type(self.schema_version) is not str
            or self.schema_version != 'aimms.source.read-result.v1'
            or type(self.population_complete) is not bool
            or self.population_complete is not False
            or type(self.revision_complete) is not bool
            or self.revision_complete is not False
        ):
            raise ValueError(
                'Direct observations cannot claim complete revisions or populations'
            )
        if type(self.state) is not str or self.state not in (
            'available',
            'unavailable',
        ):
            raise ValueError('Invalid direct-core outcome state')
        if self.state == 'unavailable':
            if self.record is not None:
                raise ValueError('Unavailable outcomes cannot disclose a record')
        else:
            _validate_core_record(self.record)


_CORE_RECORD_TYPES = (
    ClientSourceRecord,
    ClientDetailsSourceRecord,
    ClientStructureSourceRecord,
    LocationSourceRecord,
    LocationDetailsSourceRecord,
    LocationPropertiesSourceRecord,
    MachineSourceRecord,
    MachineDetailsSourceRecord,
    MachineRegistrationSourceRecord,
    MachineBarcodeSourceRecord,
    MachineProfileSourceRecord,
)


def _validate_core_record(record):
    """Validate direct-core shape, not actor authority or deep profile contents.

    No ORM, model imports, reads or profile-reference resolution occur here.
    Constructors/versions from other domains are intentionally unsupported.
    """
    if type(record) not in _CORE_RECORD_TYPES:
        raise ValueError('An admitted direct-core record is required')
    annotations = get_type_hints(type(record))
    for item in fields(record):
        expected = annotations[item.name]
        value = getattr(record, item.name)
        if expected is not object and type(value) not in (
            get_args(expected) or (expected,)
        ):
            raise ValueError('Invalid direct-core field type')
        if item.name == 'schema_version' and value != item.default:
            raise ValueError('Unsupported direct-core record version')
    record.identity.__post_init__()
    for counter in ('location_revision', 'version'):
        if hasattr(record, counter) and getattr(record, counter) < 0:
            raise ValueError('A native counter cannot be negative')
    if hasattr(record, 'revision_complete') and record.revision_complete is not False:
        raise ValueError('Native counters do not establish complete revisions')
    for marker in ('source_context_state', 'barcode_data_state'):
        if hasattr(record, marker) and getattr(record, marker) != 'withheld':
            raise ValueError('Unapproved raw source material must remain withheld')
    if type(record) is MachineProfileSourceRecord:
        if record.status not in ('valid', 'invalid_withheld'):
            raise ValueError('Invalid profile disposition')
        if record.status == 'invalid_withheld' and record.profile is not None:
            raise ValueError('Invalid profiles cannot disclose their body')
        if (
            record.status == 'valid'
            and record.profile is not None
            and type(record.profile) is not dict
        ):
            raise ValueError(
                'A declared valid profile requires a native JSON object or null'
            )


def source_read_outcome(record):
    """Wrap a known direct-core record or nondisclosing unavailable result.

    Pass the actual reader result, not a population or caught operational fault.
    No exception, denied lookup or missing record becomes complete-empty or
    tombstone evidence. This helper performs no reads or request authentication.
    """
    if record is not None and type(record) not in _CORE_RECORD_TYPES:
        raise ValueError('An admitted direct-core record is required')
    return SourceReadOutcome('unavailable' if record is None else 'available', record)


@dataclass(frozen=True)
class MaintenanceSourceIdentity:
    """A native maintenance-record primary key and source environment."""

    environment: str
    pk: int

    def __post_init__(self):
        """Reject missing identity without coercing or normalizing inputs."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        if type(self.pk) is not int or self.pk < 1:
            raise ValueError('Maintenance primary key must be a positive integer')


@dataclass(frozen=True)
class MaintenanceSourceRecord:
    """Minimal authorized maintenance event, without linked Work data."""

    identity: MaintenanceSourceIdentity
    machine: MachineSourceIdentity
    date: date
    summary: str
    schema_version: str = field(default='aimms.maintenance.basic.v1', init=False)


@dataclass(frozen=True)
class AttachmentSourceIdentity:
    """A native attachment key, not an owner identity or authorization."""

    environment: str
    pk: int

    def __post_init__(self):
        """Reject missing identity without coercing or normalizing inputs."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        if type(self.pk) is not int or self.pk < 1:
            raise ValueError('Attachment primary key must be a positive integer')


@dataclass(frozen=True)
class MachineAttachmentSourceRecord:
    """Minimal identity and authorized machine owner; not content authority."""

    identity: AttachmentSourceIdentity
    machine: MachineSourceIdentity
    schema_version: str = field(default='aimms.machine-attachment.basic.v1', init=False)


@dataclass(frozen=True)
class MachineAttachmentContentRecord:
    """Exact bounded bytes from an authorized direct machine attachment."""

    identity: AttachmentSourceIdentity
    machine: MachineSourceIdentity
    content: bytes = field(repr=False)
    content_sha256: str
    schema_version: str = field(
        default='aimms.machine-attachment.content.v1', init=False
    )
