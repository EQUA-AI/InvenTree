"""S3 request-consumer boundary; results are observations, never authority.

This source-local adapter uses S1's actual authenticated GET intake. It is selected only by trusted server configuration in S4 and does not
change native routes or provide historical attribution.
Unavailable/unsupported output never establishes a complete empty population.
"""

import json
from dataclasses import dataclass, field
from functools import partial

from assets.integration_authority import (
    integration_actor_from_current_ai,
    integration_actor_from_request,
)
from assets.integration_sources import (
    MAX_MACHINE_ATTACHMENT_CONTENT_BYTES,
    AttachmentContentAbsentError,
    AttachmentContentLimitError,
    AttachmentContentUnavailableError,
)
from tasks import integration_s3_wire as wire
from tasks.integration_s3_details import DirectDetailsUnavailableError


@dataclass(frozen=True)
class S3ReadOutcome:
    """A single-record outcome, not a cache grant, population or change receipt."""

    state: str
    payload: bytes | None = field(repr=False)
    population_complete: bool = field(default=False, init=False)
    revision_complete: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.s3.read-result.v1', init=False)

    def __post_init__(self):
        """Keep opaque unavailability distinct from available bytes or capability."""
        if (
            type(self.schema_version) is not str
            or self.schema_version != 'aimms.s3.read-result.v1'
            or type(self.population_complete) is not bool
            or self.population_complete is not False
            or type(self.revision_complete) is not bool
            or self.revision_complete is not False
        ):
            raise ValueError(
                'An observation cannot claim complete revisions or populations'
            )
        if type(self.state) is not str or self.state not in (
            'available',
            'unavailable',
            'unsupported',
        ):
            raise ValueError('Invalid S3 read state')
        if self.state == 'available':
            if type(self.payload) is not bytes:
                raise ValueError('Available results require source bytes')
        elif self.payload is not None:
            raise ValueError('Nonavailable results cannot disclose source data')


def _checked_record(payload, operation, environment, source_pk):
    """Check the owned producer's envelope and request binding, not remote authority."""
    schemas = {
        'work.core': 'aimms.work.core_details.v1',
        'work.context': 'aimms.work.context.v1',
        'maintenance.direct': 'aimms.maintenance.direct_details.v1',
        'work_document.metadata': 'aimms.document.metadata.v1',
        'machine_document.metadata': 'aimms.document.metadata.v1',
        'work_document.content': 'aimms.document.content.v1',
        'machine_document.content': 'aimms.document.content.v1',
    }
    try:
        if type(payload) is not bytes:
            raise ValueError('Invalid source payload')
        envelope = json.loads(payload.decode('utf-8'))
        if type(envelope) is not dict or envelope['wire_schema'] != wire.WIRE_SCHEMA:
            raise ValueError('Invalid wire schema')
        record = envelope['record']
        if type(record) is not dict or record['schema_version'] != schemas[operation]:
            raise ValueError('Invalid record schema')
        subject = record['metadata'] if operation.endswith('.content') else record
        identity = subject['identity']
        domain = (
            'work'
            if operation in ('work.core', 'work.context')
            else 'maintenance'
            if operation == 'maintenance.direct'
            else 'attachment'
        )
        if (
            type(identity) is not dict
            or identity['domain'] != domain
            or identity['environment'] != environment
            or type(identity['pk']) is not int
            or identity['pk'] != source_pk
        ):
            raise ValueError('Wrong source identity')
        if domain == 'attachment' and (
            subject['source_class'] != 'native_attachment'
            or subject['owner']['domain'] != operation.split('_document.')[0]
            or subject['owner']['environment'] != environment
        ):
            raise ValueError('Wrong source classification or owner')
        return record
    except (AttributeError, KeyError, TypeError, ValueError):
        raise wire.SourceSerializationError(
            'Source observation is unavailable'
        ) from None


def _source_operations():
    """Resolve actual native readers afresh, with their own source/byte fences."""
    return {
        'work.context': (
            wire.read_work_context_json,
            'work_pk',
            wire.read_work_context_json,
        ),
        'work.core': (
            wire.read_work_details_json,
            'work_pk',
            wire.read_work_details_json,
        ),
        'maintenance.direct': (
            wire.read_maintenance_details_json,
            'record_pk',
            wire.read_maintenance_details_json,
        ),
        'work_document.metadata': (
            wire.read_work_document_metadata_json,
            'attachment_pk',
            wire.read_work_document_metadata_json,
        ),
        'machine_document.metadata': (
            wire.read_machine_document_metadata_json,
            'attachment_pk',
            wire.read_machine_document_metadata_json,
        ),
        'work_document.content': (
            wire.read_work_document_content_json,
            'attachment_pk',
            wire.read_work_document_metadata_json,
        ),
        'machine_document.content': (
            wire.read_machine_document_content_json,
            'attachment_pk',
            wire.read_machine_document_metadata_json,
        ),
    }


def _read_source(resolve_actor, *, environment, operation, source_pk, max_bytes=None):
    """Share source behavior while keeping real credential intakes independent."""
    if type(operation) is not str:
        raise ValueError('An exact source operation is required')
    operations = _source_operations()
    if operation not in operations:
        return S3ReadOutcome('unsupported', None)
    if type(environment) is not str or not environment.strip():
        raise ValueError('A nonempty source environment is required')
    if type(source_pk) is not int or source_pk < 1:
        raise ValueError('A positive source primary key is required')
    content = operation.endswith('.content')
    if content and (
        type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_MACHINE_ATTACHMENT_CONTENT_BYTES
    ):
        raise ValueError('A bounded positive content limit is required')
    if not content and max_bytes is not None:
        raise ValueError('This operation does not accept a content limit')
    actor = resolve_actor()
    if actor is None:
        return S3ReadOutcome('unavailable', None)
    reader, key, observer = operations[operation]
    kwargs = {'environment': environment, key: source_pk}
    observe = partial(observer, actor, **kwargs)
    before = None
    try:
        before = observe()
        if before is None:
            return S3ReadOutcome('unavailable', None)
        current_actor = resolve_actor()
        if current_actor is None or current_actor.pk != actor.pk:
            return S3ReadOutcome('unavailable', None)
        payload = reader(
            actor, **kwargs, **({'max_bytes': max_bytes} if content else {})
        )
        if payload is None:
            return S3ReadOutcome('unavailable', None)
        body = _checked_record(payload, operation, environment, source_pk)
        observer_operation = (
            operation.replace('.content', '.metadata') if content else operation
        )
        original = _checked_record(before, observer_operation, environment, source_pk)
        result = S3ReadOutcome('available', payload)
        same = body['metadata'] == original if content else payload == before
        current = observe()
    except (
        wire.SourceSerializationError,
        DirectDetailsUnavailableError,
        AttachmentContentAbsentError,
        AttachmentContentLimitError,
        AttachmentContentUnavailableError,
    ):
        current_actor = resolve_actor()
        if current_actor is None or current_actor.pk != actor.pk:
            return S3ReadOutcome('unavailable', None)
        if before is not None and observe() != before:
            return S3ReadOutcome('unavailable', None)
        current_actor = resolve_actor()
        if current_actor is None or current_actor.pk != actor.pk:
            return S3ReadOutcome('unavailable', None)
        raise
    current_actor = resolve_actor()
    if (
        not same
        or current != before
        or current_actor is None
        or current_actor.pk != actor.pk
    ):
        return S3ReadOutcome('unavailable', None)
    return result


def read_source(request, *, environment, operation, source_pk, max_bytes=None):
    """Consume one declared operation through the unchanged real DRF intake.

    The trusted caller supplies the source environment. Unsupported capability
    is not authorization to export a native fallback response.
    """
    return _read_source(
        partial(integration_actor_from_request, request, required_role='work_order'),
        environment=environment,
        operation=operation,
        source_pk=source_pk,
        max_bytes=max_bytes,
    )


_OUTCOME_TYPE = S3ReadOutcome


def _outcome_key(value):
    """Admit exact immutable source primitives, including fixed refusal claims."""
    if (
        type(value) is not _OUTCOME_TYPE
        or type(value.state) is not str
        or value.state not in ('available', 'unavailable', 'unsupported')
        or value.population_complete is not False
        or value.revision_complete is not False
        or type(value.schema_version) is not str
        or value.schema_version != 'aimms.s3.read-result.v1'
        or (
            type(value.payload) is not bytes
            if value.state == 'available'
            else value.payload is not None
        )
    ):
        raise wire.SourceSerializationError('S3 source observation is unavailable')
    return value.state, value.payload


class _AIReadObservation:
    """Private invocation-bound observation, never serialized read authority."""

    __slots__ = ('_expected', '_outcome', '_read_again')

    def __init__(self, outcome, read_again):
        self._expected = _outcome_key(outcome)
        self._outcome = outcome
        self._read_again = read_again

    @property
    def outcome(self):
        return self._outcome

    def reauthorize(self):
        """Refresh actual credentials, source policy and full retained content.

        Re-reading bounded file bytes detects changed content at an unchanged
        storage reference. Metadata revision alone cannot establish freshness.
        These sequential observations do not establish atomicity or ABA safety.
        """
        if _outcome_key(self._outcome) != self._expected:
            return False
        if self._expected[0] != 'available':
            return False
        current = self._read_again()
        return (
            _outcome_key(current) == self._expected
            and current.state == 'available'
            and _outcome_key(self._outcome) == self._expected
        )


def _refresh_source(resolve_actor, *, environment, operation, source_pk, max_bytes):
    """Reobserve one already validated selection; never reuse a read grant.

    Initial capture still performs the full before/read/after observation. Each
    later check reads through the actual native reader again, whose own current
    authorization and field/file fences remain in force, then compares that
    complete result with the invocation's immutable original observation. No
    cache survives an await and same-path content bytes are read again.
    """
    actor = resolve_actor()
    if actor is None:
        return S3ReadOutcome('unavailable', None)
    reader, key, _observer = _source_operations()[operation]
    payload = reader(
        actor,
        environment=environment,
        **{key: source_pk},
        **({'max_bytes': max_bytes} if operation.endswith('.content') else {}),
    )
    if payload is None:
        return S3ReadOutcome('unavailable', None)
    _checked_record(payload, operation, environment, source_pk)
    current = resolve_actor()
    if current is None or current.pk != actor.pk:
        return S3ReadOutcome('unavailable', None)
    return S3ReadOutcome('available', payload)


def read_source_for_current_ai(*, environment, operation, source_pk, max_bytes=None):
    """Read under the actual current ASGI lifetime, never a principal-ID GET.

    Trusted server code selects these coordinates. No route, cache, provider,
    historical proof, native action or general result envelope is installed.
    """
    from ai.core.auth import get_current_principal

    principal = get_current_principal()

    def resolve_actor():
        if principal is None or get_current_principal() is not principal:
            return None
        return integration_actor_from_current_ai(required_role='work_order')

    def read_again():
        try:
            return _refresh_source(
                resolve_actor,
                environment=environment,
                operation=operation,
                source_pk=source_pk,
                max_bytes=max_bytes,
            )
        except (
            wire.SourceSerializationError,
            DirectDetailsUnavailableError,
            AttachmentContentAbsentError,
            AttachmentContentLimitError,
            AttachmentContentUnavailableError,
        ):
            if resolve_actor() is None:
                return S3ReadOutcome('unavailable', None)
            raise wire.SourceSerializationError(
                'S3 source observation is unavailable'
            ) from None

    initial = _read_source(
        resolve_actor,
        environment=environment,
        operation=operation,
        source_pk=source_pk,
        max_bytes=max_bytes,
    )
    observed = _AIReadObservation(initial, read_again)
    if observed.outcome.state == 'available' and not observed.reauthorize():
        return _AIReadObservation(S3ReadOutcome('unavailable', None), read_again)
    return observed
