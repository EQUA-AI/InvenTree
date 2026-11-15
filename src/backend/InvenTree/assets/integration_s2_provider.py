"""Native-UI read routing for the optional asset integration boundary.

The native callable retains its existing response/authorization contract. It
must not be used as a strict integration export or AI fallback. No transport,
credentials or provider is created by default.
"""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

MAX_PAGE_BYTES = 1024 * 1024
MAX_JSON_ITEMS = 10000
MAX_PAGES = 256
MAX_PK = (1 << 63) - 1


class ReadError(ValueError):
    """Sanitized refusal; never populate messages from provider exceptions."""

    code = 'invalid'

    def __init__(self):
        """Use only the fixed public error vocabulary."""
        super().__init__(f'Asset read refused: {self.code}')


class ReadAuthorityError(ReadError):
    """Current authority no longer matches the accepted request."""

    code = 'denied'


class ReadUnavailableError(ReadError):
    """A selected source or dependency failed."""

    code = 'unavailable'


class ReadTimeoutError(ReadError):
    """The selected source reported timeout."""

    code = 'timeout'


class ReadIncompleteError(ReadError):
    """The source cannot establish the requested coverage."""

    code = 'incomplete'


class ReadBudgetError(ReadError):
    """A read exceeded its explicit bounded resource contract."""

    code = 'budget'


class ReadStateError(ReadError):
    """A finished, failed or concurrently used traversal cannot advance."""

    code = 'state'


class ReadCancelledError(ReadError):
    """The owner cancelled this read; no successful partial result is emitted."""

    code = 'cancelled'


@dataclass(frozen=True, repr=False)
class ReadShape:
    """Exact endpoint semantics; this does not assert provider support."""

    domain: str
    filters: tuple[tuple[str, str], ...]
    ordering: str
    fields: tuple[str, ...]
    limit: int = 25
    offset: int = 0
    schema_version: str = field(default='aimms.asset.read.v1', init=False)


@dataclass(frozen=True, repr=False)
class ReadBinding:
    """Trusted caller coordinates, never a user-supplied authority token."""

    shape: ReadShape
    environment: str
    actor_pk: int
    client_ids: tuple[int, ...]
    authority: str
    request_scope: str


@dataclass(frozen=True, repr=False)
class ReadCapability:
    """A configured adapter's claim for exactly one bound request."""

    binding: ReadBinding
    generation: str
    offsets: bool = False


@dataclass(frozen=True, repr=False)
class ProviderPage:
    """Source-shaped rows and explicit page coverage; not authenticated proof."""

    binding: ReadBinding
    generation: str
    position: int
    sequence: int
    rows: tuple[dict, ...]
    total: int
    next_cursor: str | None
    coverage_complete: bool = False


@dataclass(frozen=True, repr=False)
class ReadPlan:
    """Trusted adapter functions; source/proof/collation need qualification."""

    capability: ReadCapability
    fetch: Callable
    verify: Callable
    row_key: Callable


@dataclass(frozen=True, repr=False)
class AcceptedPage:
    """A detached row payload, not mutable provider-owned dictionaries."""

    payload: bytes
    total: int
    generation: str
    next_cursor: str | None
    snapshot_complete: bool
    keys: tuple[tuple[str, int], ...] = ()

    @property
    def rows(self):
        """Return a fresh decoding so consumer mutation cannot alter state."""
        return json.loads(self.payload)


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 1024


def _pk(value):
    return type(value) is int and 1 <= value <= MAX_PK


def _binding_key(binding):
    """Validate literals before comparing; return immutable comparison data."""
    if type(binding) is not ReadBinding or type(binding.shape) is not ReadShape:
        raise ReadError()
    shape = binding.shape
    if (
        type(shape.domain) is not str
        or shape.domain not in ('machines', 'locations')
        or type(shape.schema_version) is not str
        or shape.schema_version != 'aimms.asset.read.v1'
        or not _text(shape.ordering)
        or type(shape.limit) is not int
        or not 1 <= shape.limit <= 100
        or type(shape.offset) is not int
        or not 0 <= shape.offset <= MAX_PK
        or type(shape.filters) is not tuple
        or len(shape.filters) > 32
        or type(shape.fields) is not tuple
        or not 2 <= len(shape.fields) <= 64
    ):
        raise ReadError()
    if any(
        type(pair) is not tuple
        or len(pair) != 2
        or not all(type(s) is str and len(s) <= 1024 for s in pair)
        for pair in shape.filters
    ):
        raise ReadError()
    if len({pair[0] for pair in shape.filters}) != len(shape.filters):
        raise ReadError()
    if (
        any(not _text(name) for name in shape.fields)
        or len(set(shape.fields)) != len(shape.fields)
        or not {'pk', 'name'}.issubset(shape.fields)
    ):
        raise ReadError()
    if (
        not all(
            _text(value)
            for value in (binding.environment, binding.authority, binding.request_scope)
        )
        or not _pk(binding.actor_pk)
        or type(binding.client_ids) is not tuple
        or not 1 <= len(binding.client_ids) <= 10000
        or any(not _pk(pk) for pk in binding.client_ids)
        or tuple(sorted(set(binding.client_ids))) != binding.client_ids
    ):
        raise ReadError()
    return (
        shape.domain,
        shape.schema_version,
        shape.filters,
        shape.ordering,
        shape.fields,
        shape.limit,
        shape.offset,
        binding.environment,
        binding.actor_pk,
        binding.client_ids,
        binding.authority,
        binding.request_scope,
    )


def supports(plan, binding):
    """Match the entire request before any adapter data function is invoked."""
    try:
        matching = (
            type(plan) is ReadPlan
            and type(plan.capability) is ReadCapability
            and _binding_key(plan.capability.binding) == _binding_key(binding)
        )
    except ReadError:
        return False
    return matching and (
        _text(plan.capability.generation)
        and type(plan.capability.offsets) is bool
        and (binding.shape.offset == 0 or plan.capability.offsets)
        and all(callable(fn) for fn in (plan.fetch, plan.verify, plan.row_key))
    )


def _reauthorize(binding_factory, expected):
    """Recheck the full trusted authority and request, not a cached user PK."""
    try:
        if _binding_key(binding_factory()) != expected:
            raise ReadAuthorityError()
    except (ReadTimeoutError, ReadUnavailableError):
        raise
    except ReadError:
        raise ReadAuthorityError() from None
    except TimeoutError:
        raise ReadTimeoutError() from None
    except Exception:
        raise ReadUnavailableError() from None


def _payload(rows):
    """Bound a JSON tree before encoding and detach it from the producer."""
    pending = [(list(rows), 0)]
    items = 0
    size = 0
    while pending:
        value, depth = pending.pop()
        items += 1
        if items > MAX_JSON_ITEMS or depth > 16:
            raise ReadBudgetError()
        if type(value) is dict:
            if len(value) > MAX_JSON_ITEMS:
                raise ReadBudgetError()
            for key, item in value.items():
                if type(key) is not str or len(key) > 255:
                    raise ReadError()
                size += len(key.encode('utf-8'))
                pending.append((item, depth + 1))
        elif type(value) is list:
            if len(value) > MAX_JSON_ITEMS:
                raise ReadBudgetError()
            pending.extend((item, depth + 1) for item in value)
        elif type(value) is str:
            if len(value) > MAX_PAGE_BYTES:
                raise ReadBudgetError()
            size += len(value.encode('utf-8'))
        elif type(value) is int:
            if value.bit_length() > 64:
                raise ReadBudgetError()
            size += 24
        elif type(value) is float:
            if not math.isfinite(value):
                raise ReadError()
            size += 32
        elif value is None or type(value) is bool:
            size += 5
        else:
            raise ReadError()
        if size > MAX_PAGE_BYTES:
            raise ReadBudgetError()
    payload = json.dumps(
        rows, ensure_ascii=False, allow_nan=False, separators=(',', ':')
    ).encode('utf-8')
    if len(payload) > MAX_PAGE_BYTES:
        raise ReadBudgetError()
    return payload


def _page_header(page):
    """Retain exact metadata types across adapter key/proof callbacks."""
    return (
        _binding_key(page.binding),
        tuple(
            (type(value), value)
            for value in (
                page.generation,
                page.position,
                page.sequence,
                page.total,
                page.next_cursor,
                page.coverage_complete,
            )
        ),
    )


def _checked_page(page, *, plan, binding, expected, position, sequence, generation):
    """Validate actual rows, coverage and the independently qualified proof."""
    if (
        type(page) is not ProviderPage
        or _binding_key(page.binding) != expected
        or not _text(page.generation)
        or page.generation != generation
        or not _text(plan.capability.generation)
        or plan.capability.generation != generation
        or type(page.position) is not int
        or page.position != position
        or type(page.sequence) is not int
        or page.sequence != sequence
        or type(page.rows) is not tuple
        or len(page.rows) > binding.shape.limit
        or type(page.total) is not int
        or not 0 <= page.total <= MAX_PK
        or (bool(page.rows) and page.total < position + len(page.rows))
        or (page.next_cursor is not None and not _text(page.next_cursor))
    ):
        raise ReadError()
    if page.coverage_complete is not True:
        raise ReadIncompleteError()
    terminal = page.next_cursor is None
    if terminal != (position + len(page.rows) >= page.total) or (
        not terminal and not page.rows
    ):
        raise ReadIncompleteError()
    for row in page.rows:
        if (
            type(row) is not dict
            or set(row) != set(binding.shape.fields)
            or not _pk(row.get('pk'))
            or type(row.get('name')) is not str
            or len(row['name']) > 255
        ):
            raise ReadError()
    payload = _payload(page.rows)
    total = page.total
    next_cursor = page.next_cursor
    detached = ProviderPage(
        binding=page.binding,
        generation=generation,
        position=position,
        sequence=sequence,
        rows=tuple(json.loads(payload)),
        total=total,
        next_cursor=next_cursor,
        coverage_complete=True,
    )
    header = _page_header(detached)
    keys = []
    seen = set()
    for row in detached.rows:
        key = plan.row_key(row)
        if (
            type(key) is not tuple
            or len(key) != 2
            or type(key[0]) is not str
            or len(key[0]) > 1024
            or not _pk(key[1])
            or key[1] != row['pk']
            or (keys and key <= keys[-1])
            or row['pk'] in seen
        ):
            raise ReadError()
        keys.append(key)
        seen.add(row['pk'])
    if plan.verify(detached) is not True:
        raise ReadIncompleteError()
    if (
        _page_header(detached) != header
        or type(detached.rows) is not tuple
        or _payload(detached.rows) != payload
    ):
        raise ReadIncompleteError()
    return AcceptedPage(
        payload=payload,
        total=total,
        generation=generation,
        next_cursor=next_cursor,
        snapshot_complete=terminal and binding.shape.offset == 0,
        keys=tuple(keys),
    )


def _read_one(
    *, plan, binding, binding_factory, expected, cursor, position, sequence, generation
):
    """Classify a selected source failure; never retry via native fallback."""
    _reauthorize(binding_factory, expected)
    try:
        page = plan.fetch(
            binding=binding,
            generation=generation,
            cursor=cursor,
            position=position,
            sequence=sequence,
        )
        _reauthorize(binding_factory, expected)
        accepted = _checked_page(
            page,
            plan=plan,
            binding=binding,
            expected=expected,
            position=position,
            sequence=sequence,
            generation=generation,
        )
        _reauthorize(binding_factory, expected)
        return accepted
    except ReadError:
        _reauthorize(binding_factory, expected)
        raise
    except TimeoutError:
        _reauthorize(binding_factory, expected)
        raise ReadTimeoutError() from None
    except Exception:
        _reauthorize(binding_factory, expected)
        raise ReadUnavailableError() from None


class PageTraversal:
    """A provider traversal owns its continuation; no caller-supplied state."""

    def __init__(
        self,
        *,
        binding,
        plan,
        binding_factory,
        max_pages=MAX_PAGES,
        cancelled=lambda: False,
    ):
        """Bind a source-qualified adapter and current authority callback."""
        if (
            type(max_pages) is not int
            or not 1 <= max_pages <= MAX_PAGES
            or not callable(cancelled)
            or not supports(plan, binding)
        ):
            raise ReadError()
        self._binding = binding
        self._plan = plan
        self._binding_factory = binding_factory
        self._expected = _binding_key(binding)
        self._generation = plan.capability.generation
        self._max_pages = max_pages
        self._cancelled = cancelled
        self._position = binding.shape.offset
        self._sequence = 0
        self._cursor = None
        self._total = None
        self._seen_ids = set()
        self._seen_cursors = set()
        self._last_key = None
        self._terminal = False
        self._failed = False
        self._lock = Lock()

    def __copy__(self):
        """Linear state is not a copyable or serializable page token."""
        raise TypeError('Asset traversal state cannot be copied')

    def __deepcopy__(self, memo):
        """Refuse a second branch, including recursive copying."""
        raise TypeError('Asset traversal state cannot be copied')

    def __reduce__(self):
        """Do not expose private progress as a resumable authority carrier."""
        raise TypeError('Asset traversal state cannot be serialized')

    def read_next(self):
        """Read one bounded page without retaining the returned row payload."""
        if not self._lock.acquire(blocking=False):
            raise ReadStateError()
        try:
            if self._terminal or self._failed:
                raise ReadStateError()
            if self._sequence >= self._max_pages:
                raise ReadBudgetError()
            if self._cancelled() is not False:
                raise ReadCancelledError()
            if _binding_key(self._plan.capability.binding) != self._expected:
                raise ReadError()
            page = _read_one(
                plan=self._plan,
                binding=self._binding,
                binding_factory=self._binding_factory,
                expected=self._expected,
                cursor=self._cursor,
                position=self._position,
                sequence=self._sequence,
                generation=self._generation,
            )
            if self._total is not None and page.total != self._total:
                raise ReadIncompleteError()
            if page.next_cursor is not None and (
                page.next_cursor == self._cursor
                or page.next_cursor in self._seen_cursors
            ):
                raise ReadError()
            for key in page.keys:
                if key[1] in self._seen_ids or (
                    self._last_key is not None and key <= self._last_key
                ):
                    raise ReadError()
                self._seen_ids.add(key[1])
                self._last_key = key
            if self._cancelled() is not False:
                raise ReadCancelledError()
            _reauthorize(self._binding_factory, self._expected)
            self._total = page.total
            self._sequence += 1
            self._position += len(page.keys)
            self._terminal = page.next_cursor is None
            if self._cursor is not None:
                self._seen_cursors.add(self._cursor)
            self._cursor = page.next_cursor
            return page
        except ReadError:
            self._failed = True
            raise
        except Exception:
            self._failed = True
            raise ReadUnavailableError() from None
        finally:
            self._lock.release()


def route_native_read(*, native, binding_factory, provider_factory=None):
    """Choose native before data execution when optional capability is absent."""
    if provider_factory is None:
        return native()
    try:
        binding = binding_factory()
        if binding is not None:
            _binding_key(binding)
            plan = provider_factory(binding)
            if supports(plan, binding):
                return PageTraversal(
                    plan=plan, binding=binding, binding_factory=binding_factory
                ).read_next()
    except ReadError:
        raise
    except TimeoutError:
        raise ReadTimeoutError() from None
    except Exception:
        raise ReadUnavailableError() from None
    # Native validation/errors are outside optional-provider classification.
    return native()
