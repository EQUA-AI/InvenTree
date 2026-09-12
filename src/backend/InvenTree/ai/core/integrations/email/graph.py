"""Microsoft Graph adapter with folder-scoped delta and immutable IDs."""

from typing import ClassVar
from urllib.parse import quote, urlencode

from .contracts import Capabilities, MailboxError, MessageChange, SyncPage
from .http_mail import APIMailProvider


class GraphProvider(APIMailProvider):
    """Operate on one explicit mailbox, never the token owner's implicit default."""

    host = "graph.microsoft.com"
    prefix = "/v1.0/users/"
    headers: ClassVar[dict] = {"Prefer": 'IdType="ImmutableId", odata.maxpagesize=20'}
    # Conservative MIME cap; large-attachment upload sessions are not advertised.
    capabilities = Capabilities(
        message_identity="mailbox_stable", sent_copy="provider", max_message_bytes=2 * 1024 * 1024
    )

    @property
    def root(self):
        """Use the configured mailbox for send, read and cursor validation."""
        return f"https://{self.host}{self.prefix}{quote(self.config.address, safe='')}"

    def submit(self, message):
        """An empty 202 response is submission evidence, not recipient delivery."""
        return self._send(message, self.root + "/sendMail")

    def sync(self, collection, cursor):
        """Commit folder removals as locations, preserving stable local identity."""
        options = self.config.options
        folders = {options.get("inbox", "Inbox"): "inbox", options.get("sent", "Sent"): "sentitems"}
        if collection not in folders:
            raise MailboxError("invalid_collection")
        path = self.root + "/mailFolders/" + folders[collection] + "/messages/delta"
        cursor = cursor or {}
        url = cursor.get("url")
        if url and not url.startswith(path + "?"):
            raise MailboxError("invalid_provider_url")
        if not url:
            query = {"$select": "id,isRead", "$top": "20"}
            if cursor.get("since"):
                query["$filter"] = "receivedDateTime ge " + cursor["since"].replace("+00:00", "Z")
            url = path + "?" + urlencode(query)
        result = self.get_json(url)
        changes = []
        for item in result.get("value", []):
            identity = item["id"]
            if "@removed" in item:
                changes.append(MessageChange("remove_location", identity, identity))
                continue
            status, raw = self.request(
                "GET",
                self.root + "/messages/" + quote(identity, safe="") + "/$value",
                headers=self.headers,
            )
            if status == 404:
                changes.append(MessageChange("remove_location", identity, identity))
            elif status == 200:
                changes.append(
                    MessageChange("upsert", identity, identity, raw, bool(item.get("isRead")))
                )
            else:
                raise MailboxError("provider_unavailable")
        next_url, delta_url = result.get("@odata.nextLink"), result.get("@odata.deltaLink")
        if bool(next_url) == bool(delta_url):
            raise MailboxError("invalid_sync_page")
        return SyncPage(
            collection,
            tuple(changes),
            continuation={"url": next_url} if next_url else None,
            checkpoint={"url": delta_url} if delta_url else None,
            complete=not bool(next_url),
        )
