"""Standards-based adapter with explicit TLS and conservative SMTP evidence."""

import contextlib
import imaplib
import re
import smtplib
import ssl
from datetime import datetime

from .contracts import Capabilities, MailboxError, MessageChange, SendObservation, SyncPage
from .network import connect


class _SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return connect(host, port, timeout)


class _SMTPS(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        return self.context.wrap_socket(connect(host, port, timeout), server_hostname=host)


class _IMAP(imaplib.IMAP4_SSL):
    def _create_socket(self, timeout):
        return self.ssl_context.wrap_socket(
            connect(self.host, self.port, timeout), server_hostname=self.host
        )


class SMTPIMAPProvider:
    """Fresh connections per operation; no Django notification configuration."""

    def __init__(self, config):
        """Construction does not connect or resolve DNS."""
        self.config = config
        self.capabilities = Capabilities(
            sent_copy="append_required" if config.options.get("sent_copy") == "append" else "none"
        )

    def _smtp(self):
        options, credentials = self.config.options, self.config.credentials
        mode = options.get("smtp_tls", "starttls")
        if mode not in ("starttls", "implicit"):
            raise MailboxError("tls_required")
        host = options.get("smtp_host")
        port = options.get("smtp_port", 465 if mode == "implicit" else 587)
        context = ssl.create_default_context()
        client = (
            _SMTPS(host, port, timeout=20, context=context)
            if mode == "implicit"
            else _SMTP(host, port, timeout=20)
        )
        try:
            client.ehlo()
            if mode == "starttls":
                client.starttls(context=context)
                client.ehlo()
            client.login(credentials["smtp_username"], credentials["smtp_password"])
            return client
        except Exception:
            client.close()
            raise

    def _imap(self):
        options, credentials = self.config.options, self.config.credentials
        client = _IMAP(
            options.get("imap_host"),
            options.get("imap_port", 993),
            ssl_context=ssl.create_default_context(),
            timeout=20,
        )
        try:
            client.login(credentials["imap_username"], credentials["imap_password"])
            return client
        except Exception:
            with contextlib.suppress(Exception):
                client.logout()
            raise

    def submit(self, message):
        """Acceptance requires successful final DATA, not just RCPT acceptance."""
        if message.account_id != self.config.account_id:
            raise MailboxError("account_mismatch")
        count = len(message.recipients)
        try:
            client = self._smtp()
        except Exception:
            return SendObservation("failed_before_effect", ("rejected",) * count, "pre_dispatch")
        recipients = ["rejected"] * count
        data_started = False
        try:
            code, _ = client.mail(message.sender)
            if code != 250:
                return SendObservation(
                    "failed_before_effect", tuple(recipients), "transport_response"
                )
            for index, address in enumerate(message.recipients):
                code, _ = client.rcpt(address)
                if code in (250, 251):
                    recipients[index] = "unknown"
            if "unknown" not in recipients:
                return SendObservation(
                    "failed_before_effect", tuple(recipients), "transport_response"
                )
            data_started = True
            code, _ = client.data(message.raw)
            if code == 250:
                recipients = ["accepted" if r == "unknown" else r for r in recipients]
            elif 400 <= code <= 599:
                recipients = ["rejected"] * count
        except smtplib.SMTPDataError as exc:
            if 400 <= exc.smtp_code <= 599:
                recipients = ["rejected"] * count
        except Exception:
            if not data_started:
                recipients = ["rejected"] * count
        finally:
            # A cleanup failure cannot undo the final DATA acceptance already observed.
            with contextlib.suppress(Exception):
                client.close()
        outcome = (
            "succeeded"
            if all(r == "accepted" for r in recipients)
            else "partial"
            if "accepted" in recipients
            else "unknown"
            if "unknown" in recipients
            else "failed_before_effect"
        )
        copy_failed = False
        if "accepted" in recipients and self.capabilities.sent_copy == "append_required":
            try:
                with self._imap() as imap:
                    status, _ = imap.append(
                        self.config.options.get("sent", "Sent"), "\\Seen", None, message.raw
                    )
                    copy_failed = status != "OK"
            except Exception:
                copy_failed = True
        return SendObservation(
            outcome,
            tuple(recipients),
            "inconclusive" if outcome == "unknown" else "transport_response",
            sent_copy_failed=copy_failed,
        ).validate(count)

    def reconcile(self, message):
        """An IMAP Sent copy does not prove SMTP delivery or recipient acceptance."""
        return SendObservation.unknown(len(message.recipients))

    def sync(self, collection, cursor):
        """UID-based bounded BODY.PEEK reads never mark messages as read."""
        if collection not in {
            self.config.options.get("inbox", "Inbox"),
            self.config.options.get("sent", "Sent"),
        } or any(c in collection for c in ("\r", "\n", "\x00")):
            raise MailboxError("invalid_collection")
        cursor = cursor or {}
        with self._imap() as client:
            status, _ = client.select(
                '"' + collection.replace("\\", "\\\\").replace('"', '\\"') + '"', readonly=True
            )
            if status != "OK":
                raise MailboxError("collection_unavailable")
            validity = (client.response("UIDVALIDITY")[1] or [b""])[0].decode()
            if not validity.isdigit():
                raise MailboxError("uidvalidity_unavailable")
            if cursor.get("validity") and cursor["validity"] != validity:
                raise MailboxError("uidvalidity_changed")
            known = set(cursor.get("known", []))
            uids = cursor.get("snapshot")
            if uids is None:
                if not cursor.get("since"):
                    raise MailboxError("backfill_window_required")
                criteria = ["SINCE", datetime.fromisoformat(cursor["since"]).strftime("%d-%b-%Y")]
                status, response = client.uid("SEARCH", None, *criteria)
                if status != "OK":
                    raise MailboxError("sync_failed")
                uids = sorted({int(value) for value in response[0].split()})
            if len(uids) > 10000 or len(known) > 10000:
                raise MailboxError("sync_scope_too_large")
            work = [(uid, False) for uid in uids] + [
                (uid, True) for uid in sorted(known - set(uids))
            ]
            offset = int(cursor.get("offset", 0))
            changes = []
            for uid, removed in work[offset : offset + 20]:
                identity = f"{collection}:{validity}:{uid}"
                if removed:
                    changes.append(MessageChange("remove_location", identity, identity))
                    continue
                if uid in known:
                    status, response = client.uid("FETCH", str(uid), "(UID FLAGS)")
                    if status != "OK":
                        raise MailboxError("sync_failed")
                    metadata = b" ".join(item for item in response if isinstance(item, bytes))
                    if re.search(rb"UID\s+" + str(uid).encode() + rb"\b", metadata):
                        changes.append(
                            MessageChange(
                                "flags", identity, identity, is_read=b"\\Seen" in metadata
                            )
                        )
                    else:
                        changes.append(MessageChange("remove_location", identity, identity))
                    continue
                status, fetched = client.uid(
                    "FETCH", str(uid), "(UID FLAGS RFC822.SIZE BODY.PEEK[]<0.20971521>)"
                )
                if status != "OK":
                    raise MailboxError("sync_failed")
                chunks = [item for item in fetched if isinstance(item, tuple)]
                if not chunks:
                    changes.append(MessageChange("remove_location", identity, identity))
                    continue
                metadata, raw = chunks[0]
                size = re.search(rb"RFC822.SIZE (\d+)", metadata)
                if (
                    not size
                    or int(size[1]) > self.capabilities.max_message_bytes
                    or len(raw) > self.capabilities.max_message_bytes
                ):
                    raise MailboxError("message_too_large")
                identity = f"{collection}:{validity}:{uid}"
                changes.append(
                    MessageChange("upsert", identity, identity, raw, b"\\Seen" in metadata)
                )
            complete = offset + 20 >= len(work)
            next_cursor = (
                {"validity": validity, "since": cursor["since"], "known": uids}
                if complete
                else {
                    "validity": validity,
                    "since": cursor["since"],
                    "known": sorted(known),
                    "snapshot": uids,
                    "offset": offset + 20,
                }
            )
            return SyncPage(
                collection,
                tuple(changes),
                continuation=None if complete else next_cursor,
                checkpoint=next_cursor if complete else None,
                complete=complete,
            )
