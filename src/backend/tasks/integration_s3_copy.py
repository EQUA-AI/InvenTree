"""Unregistered synthetic S3 retained-copy planning through the S5/S7 owners.

Current source-read authority permits private fixture bookkeeping, not genuine
historical attribution, native copy discovery, approval, deletion or erasure.
An independently labelled planning client never replaces unknown source clients.
"""

import hashlib
from dataclasses import dataclass, field

from assets.integration_contracts import ClientSourceIdentity
from integration_state import s7_deletion_requests as peer
from integration_state.s7_contracts import SourceAspectIdentity
from integration_state.shared_contracts import exact_uuid, literal
from tasks import integration_s3_delivery as delivery

_CLIENT_TYPE = ClientSourceIdentity
_IDENTITY_TYPE = SourceAspectIdentity
_INTENT_TYPE = peer.DeletionIntent
_IMPACT_TYPE = peer.CopyImpact
_ASSESSMENT_TYPE = peer.ReadinessAssessment
_PROGRESS_TYPE = peer.CopyProgress
_SOURCE_TYPE = delivery._PREPARED_TYPE
MAX_COPY_BYTES = 2 * 1024 * 1024
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


class SourceCopyError(ValueError):
    """Private planning failed without exposing source bodies or fixture bytes."""


class SourceCopyUnavailableError(SourceCopyError):
    """Current source/actor authority is unavailable, never deleted or empty."""


def _client_key(value):
    if type(value) is not _CLIENT_TYPE:
        raise SourceCopyError('An exact synthetic planning client is required')
    value.__post_init__()
    return value.environment, value.pk


def _source_key(value):
    if type(value) is not _IDENTITY_TYPE:
        raise SourceCopyError('An exact private source identity is required')
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
        raise SourceCopyError('An exact private request is required')
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
        raise SourceCopyError('An exact private copy claim is required')
    value.__post_init__()
    return (
        value.copy_key,
        _source_key(value.source),
        value.copy_revision,
        None if value.owners is None else tuple(_client_key(x) for x in value.owners),
        value.attribution,
        value.retention,
    )


def _progress_fields(value):
    if type(value) is not _PROGRESS_TYPE:
        raise SourceCopyError('An exact synthetic checkpoint is required')
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


def _result_key(value):
    from integration_state import shared_requests as store

    if type(value) not in (
        store.DurablePrivateIntent,
        store.DurableCopyImpact,
        store.DurableCopyProgress,
    ):
        raise SourceCopyError('An actual private owner result is required')
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
    return (
        'progress',
        value.attempt_pk,
        value.impact_pk,
        value.namespace_id,
        value.version,
        _progress_fields(value.progress),
    )


@dataclass(frozen=True, kw_only=True)
class FixtureSourceCopy:
    """Keep independent private bytes and unknown ownership/retention explicit."""

    copy_key: str = field(repr=False)
    copy_kind: str
    source_digest: str = field(repr=False)
    content: bytes | None = field(default=None, repr=False)
    owners: tuple[ClientSourceIdentity, ...] | None = field(default=None, repr=False)
    attribution: str = 'unknown'
    retention: str = 'indefinite'
    synthetic_only: bool = field(default=True, init=False)

    def __post_init__(self):
        """Refuse inferred authority, inexact scalars and unsupported fixture values."""
        literal(self.copy_key, 256)
        literal(self.copy_kind, 16)
        if self.copy_kind not in _KINDS:
            raise SourceCopyError('The synthetic copy kind is unsupported')
        peer._digest(self.source_digest)
        if self.content is not None and (
            type(self.content) is not bytes or len(self.content) > MAX_COPY_BYTES
        ):
            raise SourceCopyError('The synthetic copy bytes are unsupported')
        if self.owners is not None:
            if type(self.owners) is not tuple or len(self.owners) > peer.MAX_IMPACTS:
                raise SourceCopyError('The synthetic copy owners are unsupported')
            for owner in self.owners:
                _client_key(owner)
            if len(set(self.owners)) != len(self.owners):
                raise SourceCopyError('The synthetic copy owners repeat')
        if type(self.attribution) is not str or self.attribution not in (
            'unknown',
            'source_claimed',
        ):
            raise SourceCopyError('The synthetic copy attribution is unsupported')
        if type(self.retention) is not str or self.retention not in (
            'indefinite',
            'protected',
            'unknown',
            'reviewable',
        ):
            raise SourceCopyError('The synthetic copy retention is unsupported')
        if self.synthetic_only is not True:
            raise SourceCopyError('Native copy discovery is unsupported')


_COPY_TYPE = FixtureSourceCopy


def _copy_key(value):
    if type(value) is not _COPY_TYPE:
        raise SourceCopyError('An exact synthetic copy fixture is required')
    value.__post_init__()
    return (
        value.copy_key,
        value.copy_kind,
        value.source_digest,
        value.content,
        None if value.owners is None else tuple(_client_key(x) for x in value.owners),
        value.attribution,
        value.retention,
        value.synthetic_only,
    )


def _coordinates(source):
    if type(source) is not _SOURCE_TYPE:
        raise SourceCopyError('An actual S3 source handle is required')
    domain, aspect = delivery._coordinates(
        source._environment, source._operation, source._source_pk
    )
    observed = delivery.metadata_from_source_payload(source._payload)
    if observed.client_ids is not None or observed.source_revision is not None:
        raise SourceCopyError('Historical source attribution is unqualified')
    return (
        source._environment,
        source._policy[1],
        domain,
        aspect,
        source._source_pk,
        hashlib.sha256(source._payload).hexdigest(),
    )


@dataclass(frozen=True)
class _PreparedSourceCopy:
    """Invocation-local S3 authority and separately supplied synthetic claims."""

    _source: delivery._PreparedSourceDelivery = field(repr=False)
    _copy: FixtureSourceCopy = field(repr=False)
    _client: ClientSourceIdentity = field(repr=False)
    _coordinates: tuple = field(repr=False)
    _fixture: tuple = field(repr=False)
    _planning_client: tuple = field(repr=False)

    def _reauthorize(self):
        if type(self._source) is not _SOURCE_TYPE:
            raise SourceCopyUnavailableError('Current copy planning is unavailable')
        try:
            self._source._check()
            self._source._check_policy()
        except MemoryError:
            raise
        except Exception:
            raise SourceCopyUnavailableError(
                'Current copy planning is unavailable'
            ) from None

    def _binding(self):
        """Validate captured primitive bindings, never perform source/file IO."""
        if (
            _copy_key(self._copy) != self._fixture
            or _coordinates(self._source) != self._coordinates
            or self._copy.source_digest != self._coordinates[-1]
            or _client_key(self._client) != self._planning_client
            or self._planning_client[0] != self._coordinates[0]
            or self._planning_client[1] not in self._source._policy[2]
        ):
            raise SourceCopyError('The synthetic source or planning binding changed')

    def _check(self):
        self._reauthorize()
        try:
            self._binding()
        except MemoryError:
            raise
        except Exception as error:
            self._reauthorize()
            if isinstance(error, SourceCopyError):
                raise
            raise SourceCopyError('Private copy validation failed') from None
        self._reauthorize()

    def _intent(self, *, epoch, request_key, request_id, request_revision):
        exact_uuid(epoch)
        exact_uuid(request_id)
        environment, actor, kind, aspect, source_pk, digest = self._coordinates
        scope_values = (
            (environment, epoch.hex, kind, aspect, source_pk),
            (environment, epoch.hex, 's3_fixture_wire', 'source_bytes', digest),
        )
        scope = tuple(
            SourceAspectIdentity(
                environment=env,
                epoch=epoch,
                entity_kind=domain,
                aspect=label,
                source_id=identity,
            )
            for env, _, domain, label, identity in scope_values
        )
        expected = (
            request_id.hex,
            request_key,
            request_revision,
            f's3-synthetic-planning-user:{actor}',
            self._planning_client,
            scope_values,
            'indefinite',
        )
        result = peer.DeletionIntent(
            request_id=request_id,
            request_key=request_key,
            request_revision=request_revision,
            actor_key=f's3-synthetic-planning-user:{actor}',
            client=self._client,
            scope=scope,
        )
        if _intent_key(result) != expected:
            raise SourceCopyError(
                'The request constructor changed its original binding'
            )
        return result

    def _request(self, value):
        from integration_state import shared_requests as store

        if type(value) is not store.DurablePrivateIntent:
            raise SourceCopyError('An actual private request is required')
        _result_key(value)
        intent = value.intent
        expected = self._intent(
            epoch=intent.scope[0].epoch,
            request_key=intent.request_key,
            request_id=intent.request_id,
            request_revision=intent.request_revision,
        )
        if _intent_key(intent) != _intent_key(expected):
            raise SourceCopyError('The request changed its source/actor/client binding')
        return intent

    def _authorize(self, intent):
        """Fence real native policy/primitive claims inside metadata ownership only."""
        try:
            self._source._check_policy()
            self._binding()
            _intent_key(intent)
            expected = self._intent(
                epoch=intent.scope[0].epoch,
                request_key=intent.request_key,
                request_id=intent.request_id,
                request_revision=intent.request_revision,
            )
            if _intent_key(intent) != _intent_key(expected):
                raise SourceCopyUnavailableError('Current copy planning is unavailable')
            self._source._check_policy()
        except MemoryError:
            raise
        except Exception:
            raise SourceCopyUnavailableError(
                'Current copy planning is unavailable'
            ) from None
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
            raise SourceCopyError('The copy constructor changed its original binding')
        return result

    def _work(self, intent, action, validate, reread):
        from integration_state.shared_events import SharedStateError

        self._check()
        try:
            # S5 alone owns independent commits and refuses an enclosing
            # transaction. Its callbacks are native-policy/primitive-only;
            # never move full S3 reauthorization into those callbacks. A
            # refusal after an owner commit preserves that committed phase.
            self._authorize(intent)
            result = action()
            _result_key(result)
            validate(result)
            self._authorize(intent)
            self._check()
            actual = reread(result)
            if _result_key(actual) != _result_key(result):
                raise SourceCopyError('The private response changed persisted metadata')
            self._check()
            return result
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, (SourceCopyError, SharedStateError)):
                raise
            raise SourceCopyError('Private copy planning failed') from None

    def record_request(
        self, *, namespace_id, epoch, request_key, request_id, request_revision
    ):
        """Retain/replay an exact synthetic scope, never accept a native request."""
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
                raise SourceCopyError('The stored request changed its original intake')

        return self._work(
            intent,
            lambda: store.record_private_intent(
                namespace_id, intent, authorize=self._authorize
            ),
            validate,
            lambda value: store.read_private_intent(
                value.request_pk, intent=intent, authorize=self._authorize
            ),
        )

    def read_request(self, request):
        """Recover a real private request under current matching source authority."""
        from integration_state import shared_requests as store

        self._check()
        intent = self._request(request)
        expected = _result_key(request)

        def validate(value):
            if _result_key(value) != expected:
                raise SourceCopyError('The stored request changed its identity')

        action = lambda: store.read_private_intent(
            request.request_pk, intent=intent, authorize=self._authorize
        )
        return self._work(intent, action, validate, lambda value: action())

    def record_copy(self, request):
        """Retain only this independent fixture claim, not native copy inventory."""
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
                raise SourceCopyError('The stored copy changed its original claim')

        def reread(value):
            with store.shared_transaction(durable=True):
                stored_request, stored_copy = store._open_impact(
                    value.impact_pk, intent, impact
                )
                return store._restore_impact(
                    stored_copy, intent, stored_request.namespace_id
                )

        return self._work(
            intent,
            lambda: store.record_copy_impact(
                request.request_pk,
                impact,
                intent=intent,
                copy_kind=self._copy.copy_kind,
                authorize=self._authorize,
            ),
            validate,
            reread,
        )

    def assess(self, request):
        """Classify this fixture only; native authority/complete coverage stay false."""
        self.read_request(request)
        impact = self._impact(request)
        self._check()
        try:
            result = peer.assess_readiness(
                request.intent, (impact,), authorize=self._authorize
            )
            if type(result) is not _ASSESSMENT_TYPE:
                raise SourceCopyError('The private assessment is unavailable')
            result.__post_init__()
            fixture, owners = self._copy, self._fixture[4]
            expected = (
                'unresolved'
                if owners is None or fixture.attribution == 'unknown'
                else 'out_of_scope'
                if self._planning_client not in owners
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
                raise SourceCopyError(
                    'The private assessment changed its original claim'
                )
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, SourceCopyError):
                raise
            raise SourceCopyError('Private copy assessment failed') from None
        self._check()
        return result

    def _copy_impact(self, request, value):
        from integration_state import shared_requests as store

        self._request(request)
        if type(value) is not store.DurableCopyImpact:
            raise SourceCopyError('An actual private copy impact is required')
        _result_key(value)
        if (
            value.request_pk != request.request_pk
            or value.namespace_id != request.namespace_id
            or value.copy_kind != self._copy.copy_kind
            or _impact_key(value.impact) != _impact_key(self._impact(request))
        ):
            raise SourceCopyError('The private copy belongs to another fixture')

    def _progress(self, request, impact, value):
        from integration_state import shared_requests as store

        self._copy_impact(request, impact)
        if type(value) is not store.DurableCopyProgress:
            raise SourceCopyError('An actual private checkpoint is required')
        _result_key(value)
        if (
            value.impact_pk != impact.impact_pk
            or value.namespace_id != request.namespace_id
        ):
            raise SourceCopyError('The checkpoint belongs to another fixture')
        peer._progress(request.intent, impact.impact, value.progress)
        self._evidence(value.progress)

    def _evidence(self, progress):
        _progress_fields(progress)
        expected = (
            None
            if self._copy.content is None
            else hashlib.sha256(self._copy.content).hexdigest()
        )
        if (progress.status == 'synthetic_verified' and expected is None) or (
            progress.evidence_digest is not None
            and progress.evidence_digest != expected
        ):
            raise SourceCopyError(
                'The fixture evidence differs from original copy bytes'
            )

    def start(self, request, impact, *, attempt, max_attempts):
        """Commit a bounded synthetic S7 attempt without operating on native copies."""
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
                raise SourceCopyError(
                    'The private attempt changed its original binding'
                )

        return self._work(
            request.intent,
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
        """Read the actual owner's checkpoint under fresh S3 source authority."""
        from integration_state import shared_requests as store

        self._check()
        self._copy_impact(request, impact)
        action = lambda: store.read_private_copy_progress(
            impact.impact_pk,
            intent=request.intent,
            impact=impact.impact,
            authorize=self._authorize,
        )
        return self._work(
            request.intent,
            action,
            lambda value: self._progress(request, impact, value),
            lambda value: action(),
        )

    def transition(self, request, impact, snapshot, incoming):
        """CAS real S7 synthetic progress without certifying native copy effects."""
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
                raise SourceCopyError('The transition changed its original checkpoint')

        return self._work(
            request.intent,
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
        """Keep fixed cumulative attempts through S5, never resubmit a native effect."""
        from integration_state import shared_requests as store

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
                raise SourceCopyError('The retry changed its original checkpoint')

        return self._work(
            request.intent,
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


_PREPARED_TYPE = _PreparedSourceCopy


def prepare_source_copy(*, fixture_copy, fixture_client, **source_arguments):
    """Obtain actual source authority plus an explicitly independent planning client."""
    try:
        source = delivery.prepare_source_delivery(**source_arguments)
    except MemoryError:
        raise
    except delivery.SourceDeliveryUnavailableError:
        raise SourceCopyUnavailableError(
            'Current copy planning is unavailable'
        ) from None
    except Exception:
        raise SourceCopyError('Copy planning source failed') from None
    if type(source) is not _SOURCE_TYPE:
        raise SourceCopyUnavailableError('Current copy planning is unavailable')
    try:
        coordinates = _coordinates(source)
        fixture = _copy_key(fixture_copy)
        client = _client_key(fixture_client)
        result = _PreparedSourceCopy(
            source, fixture_copy, fixture_client, coordinates, fixture, client
        )
        if (
            type(result) is not _PREPARED_TYPE
            or result._source is not source
            or result._copy is not fixture_copy
            or result._client is not fixture_client
            or result._coordinates != coordinates
            or result._fixture != fixture
            or result._planning_client != client
        ):
            raise SourceCopyError(
                'The planner constructor changed its original binding'
            )
        result._check()
        return result
    except MemoryError:
        raise
    except Exception as error:
        try:
            source._check()
            source._check_policy()
        except MemoryError:
            raise
        except Exception:
            raise SourceCopyUnavailableError(
                'Current copy planning is unavailable'
            ) from None
        if isinstance(error, SourceCopyError):
            raise
        raise SourceCopyError('Copy planning source failed') from None
