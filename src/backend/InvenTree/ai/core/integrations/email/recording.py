"""Deterministic mailbox adapter for tests; never contacts a mail server."""

from collections import deque

from .contracts import (
    AccountConfig,
    Capabilities,
    MailboxError,
    PreparedEmail,
    SendObservation,
    SyncPage,
)


class RecordingProvider:
    """Record actual effects and replay explicit observation/page scenarios."""

    capabilities = Capabilities(reconciliation=True, message_identity="mailbox_stable")

    def __init__(self, config: AccountConfig):
        """Keep all state within this account-specific test instance."""
        self.config = config
        self.calls: list[PreparedEmail] = []
        self.observations = deque()
        self.reconciliations = {}
        self.pages = {}

    def submit(self, message):
        """Record every call, so tests detect duplicate dispatch, not hide it."""
        if message.account_id != self.config.account_id:
            raise MailboxError("wrong_account")
        self.calls.append(message)
        observation = (
            self.observations.popleft()
            if self.observations
            else SendObservation(
                "succeeded", ("accepted",) * len(message.recipients), "transport_response"
            )
        )
        if isinstance(observation, Exception):
            raise observation
        return observation

    def reconcile(self, message):
        """Return only scenario-supplied evidence; absence remains uncertain."""
        if message.account_id != self.config.account_id:
            raise MailboxError("wrong_account")
        return self.reconciliations.get(
            message.operation_id, SendObservation.unknown(len(message.recipients))
        )

    def sync(self, collection, cursor):
        """Return the same page for the same cursor, including empty pages."""
        key = (collection, (cursor or {}).get("page", 0))
        result = self.pages.get(key, SyncPage(collection, checkpoint=cursor or {"page": 0}))
        if isinstance(result, Exception):
            raise result
        return result
