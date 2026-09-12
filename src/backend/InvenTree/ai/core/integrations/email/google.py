"""Google API continuity adapter independent of the legacy Gmail singleton."""

import base64
from typing import ClassVar
from urllib.parse import quote, urlencode

from .contracts import Capabilities, MailboxError, MessageChange, SyncPage
from .http_mail import APIMailProvider


class GoogleProvider(APIMailProvider):
    """Mailbox history is checkpointed separately for each monitored label."""

    host = "gmail.googleapis.com"
    prefix = "/gmail/v1/users/"
    headers: ClassVar[dict] = {}
    capabilities = Capabilities(
        message_identity="mailbox_stable", sent_copy="provider", sync_scope="mailbox"
    )

    @property
    def root(self):
        """Every request carries the configured account identity."""
        return f"https://{self.host}{self.prefix}{quote(self.config.address, safe='')}"

    def submit(self, message):
        """Submit the frozen MIME and complete envelope with no automatic retry."""
        return self._send(message, self.root + "/messages/send", google=True)

    def sync(self, collection, cursor):
        """Bound full sync by time and recover expired history with an explicit gap."""
        options = self.config.options
        labels = {options.get("inbox", "Inbox"): "INBOX", options.get("sent", "Sent"): "SENT"}
        if collection not in labels:
            raise MailboxError("invalid_collection")
        label = labels[collection]
        cursor = dict(cursor or {})
        changes = []
        if cursor.get("history") and not cursor.get("full"):
            query = {"startHistoryId": cursor["history"], "maxResults": 20}
            if cursor.get("page"):
                query["pageToken"] = cursor["page"]
            # Read all history so label-removal events are not lost by label filtering.
            result = self.get_json(self.root + "/history?" + urlencode(query))
            ids = set()
            for event in result.get("history", []):
                for key in ("messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
                    ids.update(item["message"]["id"] for item in event.get(key, []))
            if len(ids) > 100:
                raise MailboxError("history_page_too_large")
        else:
            if not cursor.get("history"):
                cursor["history"] = self.get_json(self.root + "/profile")["historyId"]
            cursor["full"] = True
            query = {"labelIds": label, "maxResults": 20}
            if cursor.get("since"):
                from datetime import datetime

                query["q"] = "after:" + str(
                    int(datetime.fromisoformat(cursor["since"]).timestamp())
                )
            if cursor.get("page"):
                query["pageToken"] = cursor["page"]
            result = self.get_json(self.root + "/messages?" + urlencode(query))
            ids = {item["id"] for item in result.get("messages", [])}
        for identity in sorted(ids):
            try:
                item = self.get_json(
                    self.root + "/messages/" + quote(identity, safe="") + "?format=raw"
                )
            except MailboxError as exc:
                if exc.code != "cursor_expired":
                    raise
                changes.append(MessageChange("delete_message", identity, identity))
                continue
            if label not in item.get("labelIds", []):
                changes.append(MessageChange("remove_location", identity, identity))
                continue
            raw = base64.urlsafe_b64decode(item["raw"] + "===")
            changes.append(
                MessageChange(
                    "upsert", identity, identity, raw, "UNREAD" not in item.get("labelIds", [])
                )
            )
        if result.get("nextPageToken"):
            cursor["page"] = result["nextPageToken"]
            return SyncPage(collection, tuple(changes), continuation=cursor, complete=False)
        history = cursor["history"] if cursor.get("full") else result["historyId"]
        return SyncPage(collection, tuple(changes), checkpoint={"history": history})
