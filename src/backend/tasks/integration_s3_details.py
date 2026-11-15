"""Unwired S3 direct details behind the existing historical proof gates.

No identity, version or prior observation grants authority. These additive
contracts leave native routes and basic schemas unchanged. Related records,
protected properties, evidence and collection completeness are not supplied.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from assets.integration_contracts import MaintenanceSourceIdentity
from tasks.integration_sources import WorkSourceIdentity

WORK_FIELDS = (
    'reference',
    'title',
    'description',
    'status',
    'lifecycle_status',
    'is_active',
    'priority',
    'due_date',
    'work_order_type',
    'created_at',
    'updated_at',
    'scheduled_start',
    'scheduled_end',
    'actual_started_at',
    'actual_completed_at',
    'estimated_minutes',
    'lifecycle_version',
    'hold_reason',
    'affected_component',
    'affected_component_ref',
)


class DirectDetailsUnavailableError(Exception):
    """The currently authorized source has invalid direct fields or revision."""


def _require_fields(record, names, expected, *, nullable=False):
    """Validate exact source scalar types without changing blank/null semantics."""
    for name in names:
        value = getattr(record, name)
        if not (nullable and value is None) and type(value) is not expected:
            raise ValueError('Invalid direct source field')


@dataclass(frozen=True)
class WorkDetailsSourceRecord:
    """Direct core observation, not a complete Work/history or change watermark.

    Lifecycle version is the native command counter, not a universal revision.
    Source clocks retain their actual date/datetime/null values. Native text is
    untrusted content, not an instruction, approval or authority to act.
    """

    identity: WorkSourceIdentity
    reference: str | None
    title: str = field(repr=False)
    description: str = field(repr=False)
    status: str
    lifecycle_status: str
    is_active: bool
    priority: str
    due_date: date | None
    work_order_type: str
    created_at: datetime
    updated_at: datetime
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    actual_started_at: datetime | None
    actual_completed_at: datetime | None
    estimated_minutes: int | None
    lifecycle_version: int
    hold_reason: str = field(repr=False)
    affected_component: str = field(repr=False)
    affected_component_ref: str = field(repr=False)
    schema_version: str = field(default='aimms.work.core_details.v1', init=False)
    withheld_fields: tuple[str, ...] = field(
        default=(
            'assignee',
            'assigned_to',
            'requested_by',
            'company',
            'company_contact_name',
            'company_contact_phone',
            'job_number',
            'service_quote',
            'tags',
            'required_trades',
            'trade_requirements_confirmed',
            'machine',
            'customer',
            'installed_part',
            'recurrence_of',
            'recurrence_occurrence',
            'corrective_origin_kind',
            'corrective_finding',
            'corrective_reading',
            'corrective_step',
            'evidence',
            'history',
        ),
        init=False,
    )

    def __post_init__(self):
        """Reject invalid identity/scalars without guessing enum or timezone semantics."""
        if type(self.identity) is not WorkSourceIdentity:
            raise ValueError('Work source identity is required')
        _require_fields(
            self,
            (
                'title',
                'description',
                'status',
                'lifecycle_status',
                'priority',
                'work_order_type',
                'hold_reason',
                'affected_component',
                'affected_component_ref',
            ),
            str,
        )
        _require_fields(self, ('reference',), str, nullable=True)
        _require_fields(self, ('is_active',), bool)
        _require_fields(self, ('due_date',), date, nullable=True)
        _require_fields(self, ('created_at', 'updated_at'), datetime)
        _require_fields(
            self,
            (
                'scheduled_start',
                'scheduled_end',
                'actual_started_at',
                'actual_completed_at',
            ),
            datetime,
            nullable=True,
        )
        _require_fields(self, ('lifecycle_version',), int)
        _require_fields(self, ('estimated_minutes',), int, nullable=True)
        if self.lifecycle_version < 0 or (
            self.estimated_minutes is not None and self.estimated_minutes < 0
        ):
            raise ValueError('Invalid native counter or estimate')


def _work_snapshot(pk):
    """Read direct fields and private scope coordinates only after admission."""
    from tasks.models import WorkOrder

    return (
        WorkOrder.objects
        .filter(pk=pk)
        .values(*WORK_FIELDS, 'machine_id', 'machine__client_id', 'customer_id')
        .first()
    )


def _project_work(identity, row):
    """Detach only the core allowlist, excluding private binding coordinates."""
    return WorkDetailsSourceRecord(
        identity=identity, **{name: row[name] for name in WORK_FIELDS}
    )


def _read_details(authorize, snapshot, project):
    """Recheck current source policy and data; this is not an atomic snapshot."""
    first = authorize()
    if first is None:
        return None
    before = snapshot()
    if before is None:
        return None
    # Initial authority precedes the raw snapshot; the fresh final authority
    # and snapshot below fence the complete synchronous projection. There is
    # no intermediate disclosure or await to justify a duplicate full proof.
    try:
        result = project(first.identity, before)
    except (ValueError, TypeError, KeyError):
        if authorize() != first or snapshot() != before:
            return None
        raise DirectDetailsUnavailableError(
            'Direct source details are unavailable'
        ) from None
    if authorize() != first or snapshot() != before:
        return None
    return result


def read_work_details(actor, *, environment, work_pk):
    """Observe core Work fields only after fresh source authorization.

    The unchanged proof gate currently withholds all historical Work. In
    particular, this wrapper does not repair the existing narrow shared-Work
    prefilters or authorize people, machine fields, history or evidence.
    """
    from tasks.integration_sources import read_work_order

    return _read_details(
        lambda: read_work_order(actor, environment=environment, work_pk=work_pk),
        lambda: _work_snapshot(work_pk),
        _project_work,
    )


MAINTENANCE_FIELDS = ('date', 'summary', 'details', 'created_at', 'updated_at')


@dataclass(frozen=True)
class MaintenanceDetailsSourceRecord:
    """Direct maintenance fields; no related-Work or personnel authorization."""

    identity: MaintenanceSourceIdentity
    date: date
    summary: str = field(repr=False)
    details: str = field(repr=False)
    created_at: datetime
    updated_at: datetime
    schema_version: str = field(
        default='aimms.maintenance.direct_details.v1', init=False
    )
    withheld_fields: tuple[str, ...] = field(
        default=('machine', 'work_order', 'performed_by', 'closeout', 'evidence'),
        init=False,
    )

    def __post_init__(self):
        """Preserve native date/text/clock types and the maintenance identity domain."""
        if type(self.identity) is not MaintenanceSourceIdentity:
            raise ValueError('Maintenance source identity is required')
        _require_fields(self, ('date',), date)
        _require_fields(self, ('summary', 'details'), str)
        _require_fields(self, ('created_at', 'updated_at'), datetime)


def _maintenance_snapshot(pk):
    """Bind source row and private machine/Work association coordinates."""
    from assets.models import AssetMaintenanceRecord

    return (
        AssetMaintenanceRecord.objects
        .filter(pk=pk)
        .values(
            *MAINTENANCE_FIELDS, 'machine_id', 'machine__client_id', 'work_order_id'
        )
        .first()
    )


def _project_maintenance(identity, row):
    """Detach direct fields without revealing a withheld relationship's presence."""
    return MaintenanceDetailsSourceRecord(
        identity=identity, **{name: row[name] for name in MAINTENANCE_FIELDS}
    )


def read_maintenance_details(actor, *, environment, record_pk):
    """Read direct maintenance fields behind the unchanged S1 source proof gate.

    Related machine/Work/closeout/evidence and performed-by properties are not
    included; omission is not an assertion that their source values are null.
    """
    from assets.integration_sources import read_maintenance_record

    return _read_details(
        lambda: read_maintenance_record(
            actor, environment=environment, record_pk=record_pk
        ),
        lambda: _maintenance_snapshot(record_pk),
        _project_maintenance,
    )
