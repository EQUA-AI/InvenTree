"""Unwired bounded traversal for a source-qualified recorded-history adapter.

This module performs no authentication, source discovery or external I/O. Its
callbacks must be supplied by trusted code. A source adapter must independently
prove stable sample identities, a reproducible generation and truthful complete
window responses before this algorithm can be used for real history. The native
trend connector does not establish those capabilities.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace

MAX_HISTORY_READS = 256
MAX_HISTORY_SAMPLES_PER_READ = 1000
MAX_HISTORY_SAMPLES_TOTAL = 10000
_SOURCE_GAP_REASONS = frozenset({
    'SOURCE_UNAVAILABLE',
    'SOURCE_TIMEOUT',
    'HISTORY_UNSUPPORTED',
    'RETENTION_UNAVAILABLE',
})


@dataclass(frozen=True)
class HistoryWindow:
    """A nonempty half-open window in the source's original millisecond clock."""

    start_ms: int
    end_ms: int

    def __post_init__(self):
        """Reject malformed coordinates rather than coercing them."""
        if type(self.start_ms) is not int or type(self.end_ms) is not int:
            raise ValueError('History window boundaries must be integer milliseconds')
        if self.start_ms >= self.end_ms:
            raise ValueError('History window must be nonempty')


@dataclass(frozen=True)
class HistoryReadContext:
    """Trusted actor, source and binding coordinates for one history operation.

    This value is not an authorization credential or proof of source snapshot.
    """

    environment: str
    actor_pk: int
    machine_pk: int
    binding_pk: int
    source_pk: int
    generation: str

    def __post_init__(self):
        """Reject invalid coordinates without normalizing caller values."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A trusted source environment is required')
        if type(self.generation) is not str or not self.generation.strip():
            raise ValueError('A source-qualified generation is required')
        for pk in (self.actor_pk, self.machine_pk, self.binding_pk, self.source_pk):
            if type(pk) is not int or pk < 1:
                raise ValueError('History context IDs must be positive integers')


@dataclass(frozen=True)
class HistorySample:
    """One adapter-reported identity and its unshifted observation."""

    source_id: str
    observed_ms: int
    value: object = field(repr=False)
    quality: str | None
    unit: str | None


@dataclass(frozen=True)
class HistoryPage:
    """A bounded source response for exactly the requested window."""

    generation: str
    samples: tuple[HistorySample, ...]
    truncated: bool


@dataclass(frozen=True)
class HistoryGap:
    """A half-open interval whose history was not established as complete."""

    window: HistoryWindow
    reason: str


@dataclass(frozen=True)
class HistoryResult:
    """Only samples from complete intervals, with every uncovered gap explicit.

    Scoped traversal also supplies its invocation coordinates; they are not
    credentials, proof of current authority or proof of a source snapshot.
    """

    samples: tuple[HistorySample, ...]
    gaps: tuple[HistoryGap, ...]
    context: HistoryReadContext | None = field(default=None, repr=False)
    requested_window: HistoryWindow | None = field(default=None, repr=False)

    def __post_init__(self):
        """Accept either both invocation coordinates or neither."""
        if self.context is None and self.requested_window is None:
            return
        if not isinstance(self.context, HistoryReadContext) or not isinstance(
            self.requested_window, HistoryWindow
        ):
            raise ValueError(
                'A valid history context and requested window are required'
            )

    @property
    def complete(self):
        """Whether the qualified adapter completed all requested intervals."""
        return not self.gaps


class HistorySourceUnavailableError(Exception):
    """A trusted adapter reports an unavailable interval with a safe code."""

    def __init__(self, reason):
        """Accept only fixed, non-sensitive classifications."""
        if type(reason) is not str or reason not in _SOURCE_GAP_REASONS:
            raise ValueError('Unrecognized history source gap')
        self.reason = reason
        super().__init__(reason)


class HistoryAuthorizationError(Exception):
    """The trusted current-authority check failed; no partial result is returned."""


class HistoryProtocolError(Exception):
    """The adapter returned an invalid, inconsistent or mixed-generation page."""


class HistoryAdapterError(Exception):
    """An unclassified adapter failure; no incomplete data is returned."""


def _require_current_authority(authorize):
    """Fail closed when the trusted authority callback denies or fails."""
    try:
        if authorize() is True:
            return
    except Exception:
        pass
    raise HistoryAuthorizationError(
        'Current history authority is unavailable'
    ) from None


def _validate_page(
    page: HistoryPage, window: HistoryWindow, expected_generation: str, limit: int
):
    if (
        not isinstance(page, HistoryPage)
        or type(page.generation) is not str
        or page.generation != expected_generation
        or type(page.truncated) is not bool
        or type(page.samples) is not tuple
        or len(page.samples) > limit
    ):
        raise HistoryProtocolError('Invalid history page')
    previous = None
    seen = set()
    for sample in page.samples:
        if (
            not isinstance(sample, HistorySample)
            or type(sample.source_id) is not str
            or not sample.source_id.strip()
            or type(sample.observed_ms) is not int
            or not window.start_ms <= sample.observed_ms < window.end_ms
            or (sample.quality is not None and not isinstance(sample.quality, str))
            or (sample.unit is not None and not isinstance(sample.unit, str))
        ):
            raise HistoryProtocolError('Invalid history sample')
        position = (sample.observed_ms, sample.source_id)
        if (previous is not None and position <= previous) or sample.source_id in seen:
            raise HistoryProtocolError('History samples are unordered or repeated')
        seen.add(sample.source_id)
        previous = position


def _traverse_history(
    window: HistoryWindow,
    *,
    read_window: Callable[[HistoryWindow, int], HistoryPage],
    authorize: Callable[[], bool],
    expected_generation: str,
    max_samples_per_read: int,
    max_samples_total: int,
    max_reads: int,
    split_recoverable_errors: bool = False,
) -> HistoryResult:
    """Low-level unbound traversal; it must not be exposed to a caller.

    ``read_window(window, limit)`` must use one source generation and report
    truncation correctly, including exact-cap reads. ``authorize()`` must
    revalidate the trusted actor, binding, machine and source before and after
    every source operation and before the result. The result is not a page
    token, snapshot proof or authorization for a subsequent operation.
    Retrying smaller timeout or retention windows is opt-in and consumes the
    same read budget; the adapter must support those bounded retries.
    """
    if not isinstance(window, HistoryWindow):
        raise ValueError('A valid history window is required')
    if type(expected_generation) is not str or not expected_generation.strip():
        raise ValueError('A source-qualified generation is required')
    for value, maximum in (
        (max_samples_per_read, MAX_HISTORY_SAMPLES_PER_READ),
        (max_samples_total, MAX_HISTORY_SAMPLES_TOTAL),
        (max_reads, MAX_HISTORY_READS),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError('History budget outside supported bounds')
    if not callable(read_window) or not callable(authorize):
        raise ValueError('Trusted source and authority callbacks are required')
    if type(split_recoverable_errors) is not bool:
        raise ValueError('Retry policy must be a boolean')

    pending = [window]
    samples = []
    gaps = []
    seen = set()
    reads = 0
    while pending:
        current = pending.pop()
        if reads >= max_reads:
            gaps.append(HistoryGap(current, 'READ_BUDGET_EXHAUSTED'))
            continue
        _require_current_authority(authorize)
        reads += 1
        try:
            page = read_window(current, max_samples_per_read)
        except HistorySourceUnavailableError as exc:
            _require_current_authority(authorize)
            if type(exc.reason) is not str or exc.reason not in _SOURCE_GAP_REASONS:
                raise HistoryProtocolError('Invalid history gap reason') from None
            if (
                split_recoverable_errors
                and exc.reason in {'SOURCE_TIMEOUT', 'RETENTION_UNAVAILABLE'}
                and current.end_ms - current.start_ms > 1
            ):
                midpoint = (current.start_ms + current.end_ms) // 2
                pending.append(HistoryWindow(midpoint, current.end_ms))
                pending.append(HistoryWindow(current.start_ms, midpoint))
            else:
                gaps.append(HistoryGap(current, exc.reason))
            continue
        except Exception:
            _require_current_authority(authorize)
            raise HistoryAdapterError('History source failed') from None
        _require_current_authority(authorize)
        _validate_page(page, current, expected_generation, max_samples_per_read)
        if page.truncated:
            if current.end_ms - current.start_ms == 1:
                gaps.append(HistoryGap(current, 'UNRESOLVED_TIMESTAMP_TIE'))
                continue
            midpoint = (current.start_ms + current.end_ms) // 2
            pending.append(HistoryWindow(midpoint, current.end_ms))
            pending.append(HistoryWindow(current.start_ms, midpoint))
            continue
        if len(samples) + len(page.samples) > max_samples_total:
            if (
                len(samples) < max_samples_total
                and current.end_ms - current.start_ms > 1
            ):
                midpoint = (current.start_ms + current.end_ms) // 2
                pending.append(HistoryWindow(midpoint, current.end_ms))
                pending.append(HistoryWindow(current.start_ms, midpoint))
            else:
                gaps.append(HistoryGap(current, 'SAMPLE_BUDGET_EXHAUSTED'))
            continue
        for sample in page.samples:
            if sample.source_id in seen:
                raise HistoryProtocolError(
                    'History sample identity repeats across windows'
                )
            seen.add(sample.source_id)
            samples.append(sample)
    _require_current_authority(authorize)
    return HistoryResult(tuple(samples), tuple(gaps))


def traverse_scoped_history(
    context: HistoryReadContext,
    window: HistoryWindow,
    *,
    read_window: Callable[[HistoryReadContext, HistoryWindow, int], HistoryPage],
    authorize: Callable[[HistoryReadContext, HistoryWindow], bool],
    max_samples_per_read: int,
    max_samples_total: int,
    max_reads: int,
    split_recoverable_errors: bool = False,
) -> HistoryResult:
    """Bind trusted context and full requested window to source and checks.

    The authorization callback must compare these coordinates with the trusted
    acting user and current machine, source and binding, and authorize the
    entire requested window. Neither this value nor the adapter's generation
    label proves authority, snapshot completeness or real-source compatibility.
    """
    if not isinstance(context, HistoryReadContext):
        raise ValueError('A valid trusted history context is required')
    if not isinstance(window, HistoryWindow):
        raise ValueError('A valid history window is required')
    if not callable(read_window) or not callable(authorize):
        raise ValueError('Trusted source and authority callbacks are required')
    result = _traverse_history(
        window,
        read_window=lambda part, limit: read_window(context, part, limit),
        authorize=lambda: authorize(context, window),
        expected_generation=context.generation,
        max_samples_per_read=max_samples_per_read,
        max_samples_total=max_samples_total,
        max_reads=max_reads,
        split_recoverable_errors=split_recoverable_errors,
    )
    return replace(result, context=context, requested_window=window)
