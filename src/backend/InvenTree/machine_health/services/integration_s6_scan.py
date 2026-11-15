"""Source-authorized recorded-window IDs through existing private S5/S7 scans.

Unregistered, fixture-only and source-read-only. Transport completion is never
native population, incarnation, historical ownership, removal or erasure proof.
Only original literal IDs and private progress persist; source bodies stay in
this invocation's already qualified private S6 bytes.
"""

import hashlib
import json
from dataclasses import dataclass, field

from integration_state.s7_contracts import SourceAspectIdentity, exact_epoch, literal
from integration_state.s7_reconciliation import (
    MAX_PAGE_RECORDS,
    MAX_SCAN_RECORDS,
    ScanBinding,
    ScanPage,
    ScanProgress,
)
from integration_state.shared_contracts import integer
from machine_health.services import integration_s6_delivery as delivery
from machine_health.services.integration_s6_wire import MAX_HISTORY_BYTES

ASPECT = ('telemetry_observation', 'recorded_window')
_BINDING_TYPE = ScanBinding
_PAGE_TYPE = ScanPage
_PROGRESS_TYPE = ScanProgress
_IDENTITY_TYPE = SourceAspectIdentity


class HistoryScanError(ValueError):
    """Private scan composition failed without exposing original source data."""


class HistoryScanUnavailableError(HistoryScanError):
    """Current native source authority is unavailable, never a deletion signal."""


@dataclass(frozen=True)
class _WindowCoordinates:
    """A pure bounded projection, not authentication or historical authority."""

    environment: str = field(repr=False)
    actor_pk: int = field(repr=False)
    client_pk: int = field(repr=False)
    generation: str = field(repr=False)
    digest: str = field(repr=False)
    identities: tuple[str, ...] = field(repr=False)
    complete: bool
    page_size: int


_COORDINATES_TYPE = _WindowCoordinates


def _coordinates_key(value):
    if type(value) is not _COORDINATES_TYPE:
        raise HistoryScanError('Recorded window coordinates are unavailable')
    literal(value.environment, 128)
    literal(value.generation, 128)
    literal(value.digest, 64)
    integer(value.actor_pk)
    integer(value.client_pk)
    integer(value.page_size)
    if (
        type(value.complete) is not bool
        or value.page_size > MAX_PAGE_RECORDS
        or type(value.identities) is not tuple
        or len(value.identities) > MAX_SCAN_RECORDS
    ):
        raise HistoryScanError('Recorded window coordinates are unavailable')
    for identity in value.identities:
        literal(identity, 256)
    if len(set(value.identities)) != len(value.identities):
        raise HistoryScanError('Recorded window identities are unavailable')
    return (
        value.environment,
        value.actor_pk,
        value.client_pk,
        value.generation,
        value.digest,
        value.identities,
        value.complete,
        value.page_size,
    )


def _window_from_payload(payload, page_size):
    """Project actual original bytes; callers must independently supply authority."""
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_HISTORY_BYTES:
        raise HistoryScanError('Recorded window scan is unavailable')
    integer(page_size)
    if page_size > MAX_PAGE_RECORDS:
        raise HistoryScanError('Recorded window page budget is unsupported')
    body = json.loads(payload)
    if (
        type(body) is not dict
        or body.get('schema_version') != 'aimms.s6.history-source.v1'
        or type(body.get('complete')) is not bool
        or body.get('population_complete') is not False
        or body.get('revision_complete') is not False
        or type(body.get('observations')) is not list
        or len(body['observations']) > MAX_SCAN_RECORDS
    ):
        raise HistoryScanError('Recorded window scan is unavailable')
    context, mapping = body['context'], body['mapping']
    environment, generation = context['environment'], context['generation']
    actor_pk, client_pk = context['actor_pk'], mapping['client_pk']
    literal(environment, 128)
    literal(generation, 128)
    integer(actor_pk)
    integer(client_pk)
    identities = []
    for observation in body['observations']:
        identity = observation['sample']['source_observation_id']
        literal(identity, 256)
        identities.append(identity)
    if len(identities) != len(set(identities)):
        raise HistoryScanError('Recorded window identities are unsupported')
    expected = (
        environment,
        actor_pk,
        client_pk,
        generation,
        hashlib.sha256(payload).hexdigest(),
        tuple(identities),
        body['complete'],
        page_size,
    )
    result = _WindowCoordinates(*expected)
    if _coordinates_key(result) != expected:
        raise HistoryScanError('Recorded window constructor changed source binding')
    return result


def _binding_key(binding):
    if type(binding) is not _BINDING_TYPE:
        raise HistoryScanError('Recorded window binding is unavailable')
    binding.__post_init__()
    return (
        binding.environment,
        binding.epoch.hex,
        binding.entity_kind,
        binding.aspect,
        binding.actor_key,
        binding.client_id,
        binding.filter_key,
        binding.generation,
    )


def _page_key(page):
    if page is None:
        return None
    if type(page) is not _PAGE_TYPE:
        raise HistoryScanError('Recorded window page is unavailable')
    page.__post_init__()
    return (
        _binding_key(page.binding),
        page.offset,
        page.total,
        tuple(item.source_id for item in page.items),
        page.next_offset,
        page.state,
    )


def _snapshot_key(snapshot):
    from integration_state.shared_reconciliation import DurableScan

    if type(snapshot) is not DurableScan:
        raise HistoryScanError('Recorded window checkpoint is unavailable')
    snapshot.__post_init__()
    progress = snapshot.progress
    if type(progress) is not _PROGRESS_TYPE:
        raise HistoryScanError('Recorded window progress is unavailable')
    progress.__post_init__()
    return (
        snapshot.scan_id,
        snapshot.namespace_id,
        snapshot.version,
        _binding_key(progress.binding),
        progress.max_pages,
        progress.max_records,
        progress.attempts_used,
        progress.offset,
        progress.expected_total,
        tuple(sorted(item.source_id for item in progress.seen)),
        progress.state,
        None if progress.pending_attempt is None else progress.pending_attempt.hex,
        None if progress.last_attempt is None else progress.last_attempt.hex,
        _page_key(progress.last_page),
    )


@dataclass(frozen=True)
class _PreparedHistoryScan:
    """A private handle requiring its original actual authenticated invocation."""

    _source: object = field(repr=False)
    _window: _WindowCoordinates = field(repr=False)

    def _check(self):
        """Recheck actual native authority and independently rebuilt source binding."""
        if type(self._source) is not delivery._PREPARED_TYPE:
            raise HistoryScanUnavailableError('Recorded window scan is unavailable')
        try:
            self._source._check()
            current = _coordinates_key(self._window)
            expected = _coordinates_key(
                _window_from_payload(self._source._payload, self._window.page_size)
            )
            if current != expected:
                raise HistoryScanUnavailableError('Recorded window scan is unavailable')
            self._source._check()
        except delivery.HistoryDeliveryError:
            raise HistoryScanUnavailableError(
                'Recorded window scan is unavailable'
            ) from None

    def _binding(self, epoch):
        exact_epoch(epoch)
        window = self._window
        filter_key = hashlib.sha256(
            f'{window.digest}:{window.page_size}'.encode()
        ).hexdigest()
        expected = (
            window.environment,
            epoch.hex,
            ASPECT[0],
            ASPECT[1],
            f's6-user:{window.actor_pk}',
            window.client_pk,
            filter_key,
            window.generation,
        )
        result = ScanBinding(
            environment=window.environment,
            epoch=epoch,
            entity_kind=ASPECT[0],
            aspect=ASPECT[1],
            actor_key=f's6-user:{window.actor_pk}',
            client_id=window.client_pk,
            filter_key=filter_key,
            generation=window.generation,
        )
        if _binding_key(result) != expected:
            raise HistoryScanError('Recorded window constructor changed source binding')
        return result

    def _authorize(self, binding):
        self._check()
        if _binding_key(binding) != _binding_key(self._binding(binding.epoch)):
            raise HistoryScanUnavailableError('Recorded window scan is unavailable')
        self._check()
        return True

    def _shape(self, snapshot):
        _snapshot_key(snapshot)
        progress = snapshot.progress
        self._authorize(progress.binding)
        expected = self._window.identities[: progress.offset]
        if (
            any(type(item.source_id) is not str for item in progress.seen)
            or {item.source_id for item in progress.seen} != set(expected)
            or progress.offset > len(self._window.identities)
            or (
                progress.expected_total is not None
                and progress.expected_total != len(self._window.identities)
            )
            or (progress.state == 'transport_complete' and not self._window.complete)
        ):
            raise HistoryScanError(
                'Recorded window checkpoint changed source identities'
            )

    def _work(self, action, *, namespace_id, scan_id=None, request_key=None):
        from integration_state import shared_reconciliation as store
        from integration_state.models import ReconciliationScan
        from integration_state.shared_events import SharedStateError

        self._check()
        try:
            result = action()
            self._shape(result)
            if result.namespace_id != namespace_id or (
                scan_id is not None and result.scan_id != scan_id
            ):
                raise HistoryScanError('Recorded window checkpoint changed owner')
            row = ReconciliationScan.objects.filter(
                pk=result.scan_id, namespace_id=namespace_id
            ).first()
            if row is None or (
                request_key is not None and row.request_key != request_key
            ):
                raise HistoryScanError('Recorded window checkpoint changed request')
            actual = store.read_scan(
                result.scan_id,
                binding=self._binding(result.progress.binding.epoch),
                authorize=self._authorize,
            )
            if _snapshot_key(result) != _snapshot_key(actual):
                raise HistoryScanError(
                    'Recorded window checkpoint changed stored progress'
                )
            self._check()
            return result
        except MemoryError:
            raise
        except Exception as error:
            self._check()
            if isinstance(error, (HistoryScanError, SharedStateError)):
                raise
            raise HistoryScanError('Recorded window scan failed') from None

    def begin(self, *, namespace_id, epoch, request_key, max_pages, max_records):
        """Persist an exact private request with fixed source, page and retry budgets."""
        from integration_state import shared_reconciliation as store

        integer(namespace_id)
        self._check()
        binding = self._binding(epoch)
        return self._work(
            lambda: store.begin_scan(
                namespace_id,
                request_key,
                binding,
                max_pages=max_pages,
                max_records=max_records,
                authorize=self._authorize,
            ),
            namespace_id=namespace_id,
            request_key=request_key,
        )

    def read(self, scan_id, *, namespace_id, epoch):
        """Resume only against a freshly authorized identical recorded window."""
        from integration_state import shared_reconciliation as store

        integer(scan_id)
        integer(namespace_id)
        self._check()
        binding = self._binding(epoch)
        return self._work(
            lambda: store.read_scan(
                scan_id, binding=binding, authorize=self._authorize
            ),
            namespace_id=namespace_id,
            scan_id=scan_id,
        )

    def reserve(self, snapshot):
        """Let S5 commit the consumed attempt before page projection/acceptance."""
        from integration_state import shared_reconciliation as store

        self._check()
        self._shape(snapshot)
        return self._work(
            lambda: store.reserve_scan_page(snapshot, authorize=self._authorize),
            namespace_id=snapshot.namespace_id,
            scan_id=snapshot.scan_id,
        )

    def _page(self, snapshot, attempt):
        exact_epoch(attempt)
        progress = snapshot.progress
        if progress.pending_attempt == attempt:
            offset = progress.offset
        elif progress.last_attempt == attempt and progress.last_page is not None:
            offset = progress.last_page.offset
        else:
            raise HistoryScanError('Recorded window attempt is unavailable')
        binding = self._binding(progress.binding.epoch)
        if not self._window.complete:
            result = ScanPage(
                binding=binding,
                offset=offset,
                total=None,
                items=(),
                next_offset=None,
                state='incomplete',
            )
            if _page_key(result) != (
                _binding_key(binding),
                offset,
                None,
                (),
                None,
                'incomplete',
            ):
                raise HistoryScanError(
                    'Recorded window constructor changed incomplete source'
                )
            return result
        ids = self._window.identities[offset : offset + self._window.page_size]
        items = tuple(
            SourceAspectIdentity(
                environment=binding.environment,
                epoch=binding.epoch,
                entity_kind=binding.entity_kind,
                aspect=binding.aspect,
                source_id=value,
            )
            for value in ids
        )
        if any(type(item) is not _IDENTITY_TYPE for item in items):
            raise HistoryScanError(
                'Recorded window identity constructor changed source'
            )
        end = offset + len(items)
        total = len(self._window.identities)
        result = ScanPage(
            binding=binding,
            offset=offset,
            total=total,
            items=items,
            next_offset=end if end < total else None,
        )
        expected = (
            _binding_key(binding),
            offset,
            total,
            ids,
            end if end < total else None,
            'available',
        )
        if _page_key(result) != expected:
            raise HistoryScanError('Recorded window page constructor changed source')
        return result

    def accept(self, snapshot, *, attempt):
        """Accept only a page independently derived from the qualified original bytes."""
        from integration_state import shared_reconciliation as store

        self._check()
        self._shape(snapshot)
        page = self._page(snapshot, attempt)
        self._check()
        return self._work(
            lambda: store.accept_scan_page(
                snapshot, page, attempt=attempt, authorize=self._authorize
            ),
            namespace_id=snapshot.namespace_id,
            scan_id=snapshot.scan_id,
        )

    def abandon(self, snapshot, *, attempt):
        """Consume a lost private reservation without claiming a source response."""
        from integration_state import shared_reconciliation as store

        self._check()
        self._shape(snapshot)
        return self._work(
            lambda: store.abandon_scan_page(
                snapshot, attempt=attempt, authorize=self._authorize
            ),
            namespace_id=snapshot.namespace_id,
            scan_id=snapshot.scan_id,
        )

    def retry(self, snapshot):
        """Resume only the actual retained checkpoint without resetting budgets."""
        from integration_state import shared_reconciliation as store

        self._check()
        self._shape(snapshot)
        return self._work(
            lambda: store.retry_scan(snapshot, authorize=self._authorize),
            namespace_id=snapshot.namespace_id,
            scan_id=snapshot.scan_id,
        )


_PREPARED_TYPE = _PreparedHistoryScan


def prepare_history_scan(
    *,
    environment,
    machine_pk,
    source_pk,
    binding_pk,
    window,
    page_size=100,
    adapter=None,
    **budgets,
):
    """Obtain actual current native source proof; never accept caller actor labels."""
    try:
        source = delivery.prepare_history_delivery(
            environment=environment,
            machine_pk=machine_pk,
            source_pk=source_pk,
            binding_pk=binding_pk,
            window=window,
            adapter=adapter,
            **budgets,
        )
    except delivery.HistoryDeliveryUnavailableError:
        raise HistoryScanUnavailableError(
            'Recorded window scan is unavailable'
        ) from None
    except delivery.HistoryDeliveryError:
        raise HistoryScanError('Recorded window scan failed') from None
    result = _PreparedHistoryScan(
        source, _window_from_payload(source._payload, page_size)
    )
    if type(result) is not _PREPARED_TYPE or result._source is not source:
        raise HistoryScanError('Recorded window scan is unavailable')
    result._check()
    return result
