"""Source-bound synthetic copy planning through existing private S5/S7 owners.

Unregistered and fixture-only. No native copy discovery, deletion request
acceptance, approval, erasure, source writer or competing ledger is installed.
Read eligibility authorizes private synthetic bookkeeping only, not deletion.
"""

import hashlib
import json
from dataclasses import dataclass, field

from assets.integration_contracts import ClientSourceIdentity
from integration_state import s7_deletion_requests as peer
from integration_state.s7_contracts import SourceAspectIdentity, literal
from integration_state.shared_contracts import integer
from machine_health.services import integration_s6_delivery as delivery
from machine_health.services.integration_s6_wire import MAX_HISTORY_BYTES

_CLIENT_TYPE = ClientSourceIdentity
_IDENTITY_TYPE = SourceAspectIdentity
_INTENT_TYPE = peer.DeletionIntent
_IMPACT_TYPE = peer.CopyImpact
_ASSESSMENT_TYPE = peer.ReadinessAssessment
_PROGRESS_TYPE = peer.CopyProgress
_KINDS = (
    'original',
    'derivative',
    'index',
    'cache',
    'report',
    'audit',
    'ledger',
    'backup',
    'unknown',
)


class HistoryCopyError(ValueError):
    """Private fixture planning failed without exposing source or copy bodies."""


class HistoryCopyUnavailableError(HistoryCopyError):
    """Actual current native source authority or supported fixture is unavailable."""


def _client_key(value):
    if type(value) is not _CLIENT_TYPE:
        raise HistoryCopyError('Fixture copy owner is unavailable')
    value.__post_init__()
    return value.environment, value.pk


def _source_key(value):
    if type(value) is not _IDENTITY_TYPE:
        raise HistoryCopyError('Fixture source identity is unavailable')
    value.__post_init__()
    return (
        value.environment,
        value.epoch.hex,
        value.entity_kind,
        value.aspect,
        value.source_id,
    )


def _intent_key(value):
    if type(value) is not _INTENT_TYPE:
        raise HistoryCopyError('Fixture request is unavailable')
    value.__post_init__()
    return (
        value.request_id.hex,
        value.request_key,
        value.request_revision,
        value.actor_key,
        _client_key(value.client),
        tuple(_source_key(item) for item in value.scope),
        value.default_retention,
    )


def _impact_key(value):
    if type(value) is not _IMPACT_TYPE:
        raise HistoryCopyError('Fixture copy impact is unavailable')
    value.__post_init__()
    return (
        value.copy_key,
        _source_key(value.source),
        value.copy_revision,
        None
        if value.owners is None
        else tuple(_client_key(item) for item in value.owners),
        value.attribution,
        value.retention,
    )


@dataclass(frozen=True, kw_only=True)
class FixtureHistoryCopy:
    """Explicit independent synthetic copy truth, never discovered native storage."""

    copy_key: str = field(repr=False)
    copy_kind: str
    source_digest: str = field(repr=False)
    content: bytes | None = field(default=None, repr=False)
    owners: tuple[ClientSourceIdentity, ...] | None = field(default=None, repr=False)
    attribution: str = 'unknown'
    retention: str = 'indefinite'
    synthetic_only: bool = field(default=True, init=False)

    def __post_init__(self):
        """Preserve unknowns and exact bounded bytes without manufacturing ownership."""
        literal(self.copy_key, 256)
        literal(self.copy_kind, 16)
        if self.copy_kind not in _KINDS:
            raise HistoryCopyError('Fixture copy kind is unsupported')
        peer._digest(self.source_digest)
        if self.content is not None and (
            type(self.content) is not bytes or len(self.content) > MAX_HISTORY_BYTES
        ):
            raise HistoryCopyError('Fixture copy content is unsupported')
        if self.owners is not None:
            if type(self.owners) is not tuple or len(self.owners) > peer.MAX_IMPACTS:
                raise HistoryCopyError('Fixture copy owners are unsupported')
            for owner in self.owners:
                _client_key(owner)
            if len(set(self.owners)) != len(self.owners):
                raise HistoryCopyError('Fixture copy owners repeat')
        if type(self.attribution) is not str or self.attribution not in (
            'unknown',
            'source_claimed',
        ):
            raise HistoryCopyError('Fixture attribution is unsupported')
        if type(self.retention) is not str or self.retention not in (
            'indefinite',
            'protected',
            'unknown',
            'reviewable',
        ):
            raise HistoryCopyError('Fixture retention is unsupported')
        if self.synthetic_only is not True:
            raise HistoryCopyError('Native copy discovery is unsupported')


_COPY_TYPE = FixtureHistoryCopy


def _copy_key(value):
    if type(value) is not _COPY_TYPE:
        raise HistoryCopyError('An exact synthetic copy fixture is required')
    value.__post_init__()
    return (
        value.copy_key,
        value.copy_kind,
        value.source_digest,
        value.content,
        None
        if value.owners is None
        else tuple(_client_key(item) for item in value.owners),
        value.attribution,
        value.retention,
        value.synthetic_only,
    )


def _coordinates(payload):
    observed = delivery.metadata_from_recorded_payload(payload)
    if observed.state != 'available':
        raise HistoryCopyUnavailableError('A complete selected fixture is required')
    body = json.loads(payload)
    if body['source_evidence']['kind'] != 'synthetic_fixture':
        raise HistoryCopyUnavailableError('Native copy discovery is unsupported')
    context, mapping = body['context'], body['mapping']
    result = (
        context['environment'],
        context['actor_pk'],
        mapping['client_pk'],
        context['binding_pk'],
        hashlib.sha256(payload).hexdigest(),
    )
    literal(result[0], 128)
    for value in result[1:4]:
        integer(value)
    return result


def _result_key(value):
    from integration_state import shared_requests as store

    if type(value) not in (
        store.DurablePrivateIntent,
        store.DurableCopyImpact,
        store.DurableCopyProgress,
    ):
        raise HistoryCopyError('Private result is unavailable')
    value.__post_init__()
    if type(value) is store.DurablePrivateIntent:
        return (
            'request',
            value.request_pk,
            value.namespace_id,
            _intent_key(value.intent),
            value.intent_digest,
        )
    if type(value) is store.DurableCopyImpact:
        return (
            'copy',
            value.impact_pk,
            value.request_pk,
            value.namespace_id,
            _impact_key(value.impact),
            value.copy_kind,
            value.copy_digest,
        )
    progress = value.progress
    if type(progress) is not _PROGRESS_TYPE:
        raise HistoryCopyError('Private progress is unavailable')
    progress.__post_init__()
    return (
        'progress',
        value.attempt_pk,
        value.impact_pk,
        value.namespace_id,
        value.version,
        progress.request_fingerprint,
        progress.copy_fingerprint,
        progress.copy_key,
        progress.copy_revision,
        progress.attempt_id.hex,
        tuple(item.hex for item in progress.attempt_history),
        progress.max_attempts,
        progress.sequence,
        progress.status,
        progress.evidence_digest,
        progress.synthetic_only,
        progress.deletion_applied,
        progress.erasure_complete,
    )


@dataclass(frozen=True)
class _PreparedHistoryCopy:
    """Invocation-local actual source proof plus independent synthetic copy truth."""

    _source: delivery._PreparedHistoryDelivery = field(repr=False)
    _copy: FixtureHistoryCopy = field(repr=False)
    _coordinates: tuple = field(repr=False)
    _fixture: tuple = field(repr=False)

    def _reauthorize(self):
        if type(self._source) is not delivery._PREPARED_TYPE:
            raise HistoryCopyUnavailableError('Current copy planning is unavailable')
        try:
            self._source._check()
        except MemoryError:
            raise
        except Exception:
            raise HistoryCopyUnavailableError(
                'Current copy planning is unavailable'
            ) from None

    def _check(self):
        self._reauthorize()
        try:
            if (
                _copy_key(self._copy) != self._fixture
                or _coordinates(self._source._payload) != self._coordinates
                or self._copy.source_digest != self._coordinates[-1]
            ):
                raise HistoryCopyError('The fixture source or copy binding changed')
        except MemoryError:
            raise
        except Exception as error:
            self._reauthorize()
            if isinstance(error, HistoryCopyError):
                raise
            raise HistoryCopyError('Fixture copy validation failed') from None
        self._reauthorize()

    def _intent(self, *, epoch, request_key, request_id, request_revision):
        peer.exact_epoch(epoch)
        peer.exact_epoch(request_id)
        environment, actor, client, binding, digest = self._coordinates
        scope_values = (
            (environment, epoch.hex, delivery.ASPECT[0], delivery.ASPECT[1], binding),
            (environment, epoch.hex, 's6_fixture_window', 'source_bytes', digest),
        )
        scope = tuple(
            SourceAspectIdentity(
                environment=env,
                epoch=epoch,
                entity_kind=kind,
                aspect=aspect,
                source_id=identity,
            )
            for env, _, kind, aspect, identity in scope_values
        )
        expected = (
            request_id.hex,
            request_key,
            request_revision,
            f's6-user:{actor}',
            (environment, client),
            scope_values,
            'indefinite',
        )
        result = peer.DeletionIntent(
            request_id=request_id,
            request_key=request_key,
            request_revision=request_revision,
            actor_key=f's6-user:{actor}',
            client=ClientSourceIdentity(environment=environment, pk=client),
            scope=scope,
        )
        if _intent_key(result) != expected:
            raise HistoryCopyError(
                'The request constructor changed native source binding'
            )
        return result

    def _request(self, value):
        from integration_state import shared_requests as store

        if type(value) is not store.DurablePrivateIntent:
            raise HistoryCopyError('An actual private request is required')
        _result_key(value)
        intent = value.intent
        expected = self._intent(
            epoch=intent.scope[0].epoch,
            request_key=intent.request_key,
            request_id=intent.request_id,
            request_revision=intent.request_revision,
        )
        if _intent_key(intent) != _intent_key(expected):
            raise HistoryCopyError('The request changed source or actor binding')
        return intent

    def _authorize(self, intent):
        self._check()
        if type(intent) is not _INTENT_TYPE:
            raise HistoryCopyError('The private request is unavailable')
        expected = self._intent(
            epoch=intent.scope[0].epoch,
            request_key=intent.request_key,
            request_id=intent.request_id,
            request_revision=intent.request_revision,
        )
        if _intent_key(intent) != _intent_key(expected):
            raise HistoryCopyUnavailableError('Current copy planning is unavailable')
        self._check()
        return True

    def _impact(self, request):
        intent = self._request(request)
        fixture = self._copy
        revision = (
            None
            if fixture.content is None
            else 'fixture-sha256:' + hashlib.sha256(fixture.content).hexdigest()
        )
        expected = (
            fixture.copy_key,
            _source_key(intent.scope[0]),
            revision,
            self._fixture[4],
            fixture.attribution,
            fixture.retention,
        )
        result = peer.CopyImpact(
            copy_key=fixture.copy_key,
            source=intent.scope[0],
            copy_revision=revision,
            owners=fixture.owners,
            attribution=fixture.attribution,
            retention=fixture.retention,
        )
        if _impact_key(result) != expected:
            raise HistoryCopyError('The copy constructor changed its fixture binding')
        return result

    def _work(self, action, validate, reread):
        from integration_state.shared_events import SharedStateError

        self._check()
        try:
            result = action()
            _result_key(result)
            validate(result)
            self._check()
            actual = reread(result)
            if _result_key(actual) != _result_key(result):
                raise HistoryCopyError(
                    'The returned private result changed persisted metadata'
                )
            self._check()
            return result
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, (HistoryCopyError, SharedStateError)):
                raise
            raise HistoryCopyError('Private copy planning failed') from None

    def record_request(
        self, *, namespace_id, epoch, request_key, request_id, request_revision
    ):
        """Persist/replay an exact synthetic scope, never accept a native request."""
        from integration_state import shared_requests as store

        self._check()
        intent = self._intent(
            epoch=epoch,
            request_key=request_key,
            request_id=request_id,
            request_revision=request_revision,
        )
        expected = _intent_key(intent)

        def validate(value):
            if (
                type(value) is not store.DurablePrivateIntent
                or value.namespace_id != namespace_id
                or _intent_key(value.intent) != expected
            ):
                raise HistoryCopyError('The stored request changed its original intake')

        return self._work(
            lambda: store.record_private_intent(
                namespace_id, intent, authorize=self._authorize
            ),
            validate,
            lambda value: store.read_private_intent(
                value.request_pk, intent=intent, authorize=self._authorize
            ),
        )

    def read_request(self, request):
        """Read only a freshly source-bound actual private planning request."""
        from integration_state import shared_requests as store

        self._check()
        intent = self._request(request)
        expected = _result_key(request)

        def validate(value):
            if _result_key(value) != expected:
                raise HistoryCopyError('The private request changed persisted identity')

        return self._work(
            lambda: store.read_private_intent(
                request.request_pk, intent=intent, authorize=self._authorize
            ),
            validate,
            lambda value: store.read_private_intent(
                request.request_pk, intent=intent, authorize=self._authorize
            ),
        )

    def record_copy(self, request):
        """Retain only the explicitly supplied synthetic copy claim, not inventory."""
        from integration_state import shared_requests as store

        self.read_request(request)
        impact = self._impact(request)
        expected = _impact_key(impact)
        intent = request.intent

        def validate(value):
            if (
                type(value) is not store.DurableCopyImpact
                or value.request_pk != request.request_pk
                or value.namespace_id != request.namespace_id
                or value.copy_kind != self._copy.copy_kind
                or _impact_key(value.impact) != expected
            ):
                raise HistoryCopyError('The stored copy changed its original fixture')

        def reread(value):
            with store.shared_transaction(durable=True):
                stored_request, stored_copy = store._open_impact(
                    value.impact_pk, intent, impact
                )
                return store._restore_impact(
                    stored_copy, intent, stored_request.namespace_id
                )

        action = lambda: store.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind=self._copy.copy_kind,
            authorize=self._authorize,
        )
        return self._work(action, validate, reread)

    def assess(self, request):
        """Classify this fixture only; coverage/native authorization remain false."""
        self.read_request(request)
        impact = self._impact(request)
        self._check()
        try:
            result = peer.assess_readiness(
                request.intent, (impact,), authorize=self._authorize
            )
            if type(result) is not _ASSESSMENT_TYPE:
                raise HistoryCopyError('The fixture assessment is unavailable')
            result.__post_init__()
            fixture = self._copy
            owners = self._fixture[4]
            client = self._coordinates[0], self._coordinates[2]
            expected = (
                'unresolved'
                if owners is None or fixture.attribution == 'unknown'
                else 'out_of_scope'
                if client not in owners
                else 'shared_retained'
                if len(owners) > 1
                else 'policy_retained'
                if fixture.retention in ('indefinite', 'protected')
                else 'unresolved'
                if fixture.content is None or fixture.retention == 'unknown'
                else 'review_required'
            )
            if (
                _intent_key(result.request) != _intent_key(request.intent)
                or len(result.decisions) != 1
                or result.decisions[0].copy_key != fixture.copy_key
                or result.decisions[0].disposition != expected
            ):
                raise HistoryCopyError(
                    'The fixture assessment changed its original claim'
                )
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, HistoryCopyError):
                raise
            raise HistoryCopyError('Fixture assessment failed') from None
        self._check()
        return result

    def _copy_impact(self, request, value):
        from integration_state import shared_requests as store

        self._request(request)
        if type(value) is not store.DurableCopyImpact:
            raise HistoryCopyError('An actual private copy impact is required')
        _result_key(value)
        if (
            value.request_pk != request.request_pk
            or value.namespace_id != request.namespace_id
            or value.copy_kind != self._copy.copy_kind
            or _impact_key(value.impact) != _impact_key(self._impact(request))
        ):
            raise HistoryCopyError('The copy impact belongs to another fixture')

    def start(self, request, impact, *, attempt, max_attempts):
        """Commit an actual bounded synthetic attempt, never perform copy deletion."""
        from integration_state import shared_requests as store

        self._check()
        self._copy_impact(request, impact)
        expected = _progress_fields(
            peer.start_copy_progress(
                request.intent,
                impact.impact,
                attempt=attempt,
                max_attempts=max_attempts,
                authorize=self._authorize,
            )
        )

        def validate(value):
            self._progress(request, impact, value)
            if _progress_fields(value.progress) != expected:
                raise HistoryCopyError(
                    'The private attempt changed its original fixture'
                )

        return self._work(
            lambda: store.start_private_copy_attempt(
                impact.impact_pk,
                intent=request.intent,
                impact=impact.impact,
                attempt=attempt,
                max_attempts=max_attempts,
                authorize=self._authorize,
            ),
            validate,
            lambda value: store.read_private_copy_progress(
                impact.impact_pk,
                intent=request.intent,
                impact=impact.impact,
                authorize=self._authorize,
            ),
        )

    def read_progress(self, request, impact):
        """Recover actual private progress under fresh identical source authority."""
        from integration_state import shared_requests as store

        self._check()
        self._copy_impact(request, impact)

        def validate(value):
            self._progress(request, impact, value)

        action = lambda: store.read_private_copy_progress(
            impact.impact_pk,
            intent=request.intent,
            impact=impact.impact,
            authorize=self._authorize,
        )
        return self._work(action, validate, lambda value: action())

    def _progress(self, request, impact, value):
        from integration_state import shared_requests as store

        self._copy_impact(request, impact)
        if type(value) is not store.DurableCopyProgress:
            raise HistoryCopyError('An actual private checkpoint is required')
        _result_key(value)
        if (
            value.impact_pk != impact.impact_pk
            or value.namespace_id != request.namespace_id
        ):
            raise HistoryCopyError('The progress belongs to another fixture')
        peer._progress(request.intent, impact.impact, value.progress)
        self._evidence(value.progress)

    def _evidence(self, progress):
        if type(progress) is not _PROGRESS_TYPE:
            raise HistoryCopyError('An exact synthetic checkpoint is required')
        progress.__post_init__()
        expected = (
            None
            if self._copy.content is None
            else hashlib.sha256(self._copy.content).hexdigest()
        )
        if (progress.status == 'synthetic_verified' and expected is None) or (
            progress.evidence_digest is not None
            and progress.evidence_digest != expected
        ):
            raise HistoryCopyError('The fixture evidence does not match original bytes')

    def transition(self, request, impact, snapshot, incoming):
        """CAS actual S7 synthetic progress; never infer approval or physical erasure."""
        from integration_state import shared_requests as store

        self._check()
        self._progress(request, impact, snapshot)
        self._evidence(incoming)
        peer._progress(request.intent, impact.impact, incoming)
        expected = _progress_fields(
            incoming
            if incoming.sequence > snapshot.progress.sequence
            else snapshot.progress
        )

        def validate(value):
            self._progress(request, impact, value)
            if _progress_fields(value.progress) != expected:
                raise HistoryCopyError('The transition changed its original checkpoint')

        return self._work(
            lambda: store.record_private_copy_progress(
                snapshot,
                incoming,
                intent=request.intent,
                impact=impact.impact,
                authorize=self._authorize,
            ),
            validate,
            lambda value: store.read_private_copy_progress(
                impact.impact_pk,
                intent=request.intent,
                impact=impact.impact,
                authorize=self._authorize,
            ),
        )

    def retry(self, request, impact, snapshot, *, attempt):
        """Retain fixed cumulative attempt history through the real S5 retry owner."""
        from integration_state import shared_requests as store
        from integration_state.shared_contracts import exact_uuid

        self._check()
        self._progress(request, impact, snapshot)
        exact_uuid(attempt)
        prior = snapshot.progress
        expected = (
            prior.request_fingerprint,
            prior.copy_fingerprint,
            prior.copy_key,
            prior.copy_revision,
            attempt.hex,
            (*tuple(item.hex for item in prior.attempt_history), attempt.hex),
            prior.max_attempts,
            0,
            'planned',
            None,
            True,
            False,
            False,
        )

        def validate(value):
            self._progress(request, impact, value)
            if _progress_fields(value.progress) != expected:
                raise HistoryCopyError('The retry changed its original checkpoint')

        return self._work(
            lambda: store.retry_private_copy_attempt(
                snapshot,
                intent=request.intent,
                impact=impact.impact,
                attempt=attempt,
                authorize=self._authorize,
            ),
            validate,
            lambda value: store.read_private_copy_progress(
                impact.impact_pk,
                intent=request.intent,
                impact=impact.impact,
                authorize=self._authorize,
            ),
        )


def _progress_fields(value):
    if type(value) is not _PROGRESS_TYPE:
        raise HistoryCopyError('An exact synthetic checkpoint is required')
    value.__post_init__()
    return (
        value.request_fingerprint,
        value.copy_fingerprint,
        value.copy_key,
        value.copy_revision,
        value.attempt_id.hex,
        tuple(item.hex for item in value.attempt_history),
        value.max_attempts,
        value.sequence,
        value.status,
        value.evidence_digest,
        value.synthetic_only,
        value.deletion_applied,
        value.erasure_complete,
    )


_PREPARED_TYPE = _PreparedHistoryCopy


def prepare_history_copy(*, fixture_copy, **source_arguments):
    """Obtain actual native source proof for an explicitly synthetic copy fixture."""
    try:
        source = delivery.prepare_history_delivery(**source_arguments)
    except MemoryError:
        raise
    except delivery.HistoryDeliveryUnavailableError:
        raise HistoryCopyUnavailableError(
            'Current copy planning is unavailable'
        ) from None
    except Exception:
        raise HistoryCopyError('Copy planning source failed') from None
    if type(source) is not delivery._PREPARED_TYPE:
        raise HistoryCopyUnavailableError('Current copy planning is unavailable')
    try:
        coordinates = _coordinates(source._payload)
        fixture = _copy_key(fixture_copy)
        if fixture_copy.source_digest != coordinates[-1]:
            raise HistoryCopyError('The fixture refers to another original source')
        result = _PreparedHistoryCopy(source, fixture_copy, coordinates, fixture)
        if (
            type(result) is not _PREPARED_TYPE
            or result._source is not source
            or result._copy is not fixture_copy
            or result._coordinates != coordinates
            or result._fixture != fixture
        ):
            raise HistoryCopyError('The planner constructor changed source binding')
        result._check()
        return result
    except MemoryError:
        raise
    except Exception as error:
        try:
            source._check()
        except MemoryError:
            raise
        except Exception:
            raise HistoryCopyUnavailableError(
                'Current copy planning is unavailable'
            ) from None
        if isinstance(error, HistoryCopyError):
            raise
        raise HistoryCopyError('Copy planning source failed') from None
