"""Immutable, provider-neutral mailbox contracts without Django or SDK imports.

Adapters perform synchronous bounded I/O in background workers. Async tools
offload the application service; adapters never grant approval authority.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

Outcome = Literal["succeeded", "partial", "failed_before_effect", "unknown"]
Submission = Literal["accepted", "rejected", "unknown"]


class MailboxError(Exception):
    """A safe, provider-neutral error suitable for application handling."""

    def __init__(self, code: str, *, retry_after: int | None = None):
        """Use fixed error codes; never expose provider bodies or credentials."""
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class Capabilities:
    """Verified adapter features, limits, and identity semantics."""

    features: frozenset[str] = frozenset({"send", "receive", "reply", "attachments"})
    sync_scope: Literal["folder", "mailbox"] = "folder"
    message_identity: Literal["mailbox_stable", "location_scoped"] = "location_scoped"
    sent_copy: Literal["provider", "append_required", "none"] = "none"
    reconciliation: bool = False
    preserves_rfc_message_id: bool = True
    max_message_bytes: int = 20 * 1024 * 1024
    max_recipients: int = 100
    schema_version: int = 1


@dataclass(frozen=True)
class AccountConfig:
    """An explicit account snapshot; no process-wide connection defaults."""

    account_id: str
    provider: str
    address: str
    options: dict = field(default_factory=dict, repr=False)
    credentials: dict = field(default_factory=dict, repr=False)
    token_provider: Callable[[], str] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class PreparedEmail:
    """The reviewed transport effect, including its protected Bcc envelope."""

    account_id: str
    operation_id: str
    sender: str
    recipients: tuple[str, ...]
    rfc_message_id: str
    raw: bytes = field(repr=False)
    fingerprint: str


@dataclass(frozen=True)
class SendObservation:
    """Transport evidence, not an approval or claim of recipient delivery."""

    outcome: Outcome
    recipients: tuple[Submission, ...]
    evidence: str
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    sent_copy_failed: bool = False

    def validate(self, recipient_count: int):
        """Reject missing, contradictory, or fabricated-shape provider results."""
        if (
            not recipient_count
            or len(self.recipients) != recipient_count
            or any(r not in ("accepted", "rejected", "unknown") for r in self.recipients)
            or self.evidence
            not in ("transport_response", "provider_lookup", "pre_dispatch", "inconclusive")
        ):
            raise MailboxError("invalid_observation")
        accepted = self.recipients.count("accepted")
        uncertain = self.recipients.count("unknown")
        expected = (
            "succeeded"
            if accepted == recipient_count
            else "partial"
            if accepted
            else "unknown"
            if uncertain
            else "failed_before_effect"
        )
        if self.outcome != expected:
            raise MailboxError("invalid_observation")
        if accepted and self.evidence not in ("transport_response", "provider_lookup"):
            raise MailboxError("invalid_observation")
        if self.outcome == "failed_before_effect" and self.evidence == "inconclusive":
            raise MailboxError("invalid_observation")
        return self

    @classmethod
    def unknown(cls, count: int):
        """Conservatively describe a lost response without authorizing replay."""
        return cls("unknown", ("unknown",) * count, "inconclusive")


@dataclass(frozen=True)
class MessageChange:
    """One provider observation; folder removal is distinct from deletion."""

    kind: Literal["upsert", "flags", "remove_location", "delete_message"]
    identity: str
    location: str
    raw: bytes = field(default=b"", repr=False)
    is_read: bool = False
    received_at: str | None = None


@dataclass(frozen=True)
class SyncPage:
    """A bounded page and its private continuation or incremental checkpoint."""

    collection: str
    changes: tuple[MessageChange, ...] = ()
    continuation: dict | None = None
    checkpoint: dict | None = None
    complete: bool = True
    gap: bool = False

    def validate(self, collection: str, max_bytes: int):
        """Prevent wrong-scope, oversized, or inconsistent cursor commits."""
        if self.collection != collection or len(self.changes) > 100:
            raise MailboxError("invalid_sync_page")
        if self.complete:
            if self.continuation is not None or self.checkpoint is None:
                raise MailboxError("invalid_sync_page")
        elif self.continuation is None or self.checkpoint is not None:
            raise MailboxError("invalid_sync_page")
        for change in self.changes:
            if (
                change.kind not in ("upsert", "flags", "remove_location", "delete_message")
                or not change.identity
                or not change.location
                or len(change.raw) > max_bytes
            ):
                raise MailboxError("invalid_sync_page")
        return self


class MailboxProvider(Protocol):
    """Account-bound worker adapter; sending is invoked only after a claim."""

    capabilities: Capabilities

    def submit(self, message: PreparedEmail) -> SendObservation:
        """Attempt one transport submission without automatic retries."""
        ...

    def reconcile(self, message: PreparedEmail) -> SendObservation:
        """Read authoritative evidence, without submitting or modifying mail."""
        ...

    def sync(self, collection: str, cursor: dict | None) -> SyncPage:
        """Read one bounded page without changing provider read flags."""
        ...
