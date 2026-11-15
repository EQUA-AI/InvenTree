"""Unwired bounded page-consistency checks for a future machine provider.

Tokens, scope/filter strings and generation are assertions by trusted callers,
not credentials or proof of a source snapshot. This helper does not fetch data,
authorize a user, validate returned row contents, or change native routing.
"""

from dataclasses import dataclass, field, replace

MAX_PAGE_SIZE = 100
MAX_PAGES = 256
MAX_COORDINATE_LENGTH = 1024
MAX_MACHINE_NAME_LENGTH = 255
MAX_MACHINE_PK = (1 << 63) - 1


class PageBudgetExhaustedError(ValueError):
    """Traversal did not reach a provider-declared terminal page in budget."""


class PageAlreadyTerminalError(ValueError):
    """A provider-declared terminal state cannot accept another page."""


def _text(value):
    return (
        type(value) is str
        and bool(value.strip())
        and len(value) <= MAX_COORDINATE_LENGTH
    )


def _valid_key(key):
    return (
        type(key) is tuple
        and len(key) == 2
        and type(key[0]) is str
        and len(key[0]) <= MAX_MACHINE_NAME_LENGTH
        and type(key[1]) is int
        and 1 <= key[1] <= MAX_MACHINE_PK
    )


@dataclass(frozen=True, repr=False)
class MachinePageContext:
    """Caller-bound traversal coordinates, not evidence of authorization."""

    environment: str
    actor_pk: int
    scope: str
    filters: str
    generation: str

    def __post_init__(self):
        """Validate literal binding coordinates without coercion."""
        if not all(
            _text(value)
            for value in (self.environment, self.scope, self.filters, self.generation)
        ):
            raise ValueError('Invalid machine page context')
        if type(self.actor_pk) is not int or self.actor_pk < 1:
            raise ValueError('Invalid machine page actor')


@dataclass(frozen=True, repr=False)
class MachinePage:
    """Provider-declared ordered keys and continuation for one page."""

    context: MachinePageContext
    keys: tuple[tuple[str, int], ...]
    next_cursor: str | None


@dataclass(frozen=True)
class PageCursor:
    """Bounded local traversal state; never serialize it as authority."""

    context: MachinePageContext = field(repr=False)
    page_limit: int
    expected_cursor: str | None = field(default=None, repr=False)
    last_key: tuple[str, int] | None = field(default=None, repr=False)
    provider_terminal: bool = False
    page_count: int = 0
    seen_ids: frozenset[int] = field(default_factory=frozenset, repr=False)
    seen_cursors: frozenset[str] = field(default_factory=frozenset, repr=False)

    @classmethod
    def start(cls, context, *, page_limit):
        """Start a bounded traversal with trusted, validated coordinates."""
        if type(context) is not MachinePageContext:
            raise ValueError('Invalid machine page context')
        context.__post_init__()
        if type(page_limit) is not int or not 1 <= page_limit <= MAX_PAGE_SIZE:
            raise ValueError('Invalid page size')
        return cls(context=context, page_limit=page_limit)

    def accept(self, page, *, requested_cursor):
        """Validate one page and return new state without retaining row data."""
        if (
            type(self.context) is not MachinePageContext
            or type(self.page_limit) is not int
            or not 1 <= self.page_limit <= MAX_PAGE_SIZE
            or type(self.page_count) is not int
            or not 0 <= self.page_count <= MAX_PAGES
            or type(self.provider_terminal) is not bool
            or (self.expected_cursor is not None and not _text(self.expected_cursor))
            or (self.last_key is not None and not _valid_key(self.last_key))
            or type(self.seen_ids) is not frozenset
            or len(self.seen_ids) > self.page_count * self.page_limit
            or type(self.seen_cursors) is not frozenset
            or len(self.seen_cursors) > self.page_count
        ):
            raise ValueError('Invalid machine page traversal state')
        if any(
            type(pk) is not int or not 1 <= pk <= MAX_MACHINE_PK for pk in self.seen_ids
        ):
            raise ValueError('Invalid machine page traversal state')
        if any(not _text(cursor) for cursor in self.seen_cursors):
            raise ValueError('Invalid machine page traversal state')
        if self.provider_terminal:
            raise PageAlreadyTerminalError('Machine page traversal is already terminal')
        if self.page_count >= MAX_PAGES:
            raise PageBudgetExhaustedError('Machine page traversal budget exhausted')
        if (
            type(page) is not MachinePage
            or type(page.context) is not MachinePageContext
        ):
            raise ValueError('Invalid machine page')
        self.context.__post_init__()
        page.context.__post_init__()
        if page.context != self.context:
            raise ValueError('Machine page context changed')
        if requested_cursor is not None and not _text(requested_cursor):
            raise ValueError('Invalid requested cursor')
        if requested_cursor != self.expected_cursor:
            raise ValueError('Machine page cursor mismatch')
        if page.next_cursor is not None and (
            not _text(page.next_cursor)
            or page.next_cursor == requested_cursor
            or page.next_cursor in self.seen_cursors
        ):
            raise ValueError('Invalid machine page continuation')
        if type(page.keys) is not tuple or len(page.keys) > self.page_limit:
            raise ValueError('Invalid machine page keys')
        previous = self.last_key
        seen = set(self.seen_ids)
        for key in page.keys:
            if not _valid_key(key):
                raise ValueError('Invalid machine page key')
            if (previous is not None and key <= previous) or key[1] in seen:
                raise ValueError('Repeated or unordered machine key')
            previous = key
            seen.add(key[1])
        if not page.keys and page.next_cursor is not None:
            raise ValueError('Empty machine page cannot continue')
        cursors = self.seen_cursors
        if requested_cursor is not None:
            cursors = cursors | {requested_cursor}
        return replace(
            self,
            expected_cursor=page.next_cursor,
            last_key=previous,
            provider_terminal=page.next_cursor is None,
            page_count=self.page_count + 1,
            seen_ids=frozenset(seen),
            seen_cursors=frozenset(cursors),
        )
