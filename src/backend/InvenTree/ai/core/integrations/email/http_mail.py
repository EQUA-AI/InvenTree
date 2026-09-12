"""Fixed-origin API transport with bounded responses and no implicit retries."""

import base64
import time
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import urlsplit

from .contracts import MailboxError, SendObservation


def api_mime(message):
    """Supply hidden envelope recipients to MIME APIs without storing a Bcc header."""
    parsed = BytesParser(policy=policy.default).parsebytes(message.raw, headersonly=True)
    visible = {
        address.lower()
        for _, address in getaddresses(parsed.get_all("To", []) + parsed.get_all("Cc", []))
    }
    hidden = [address for address in message.recipients if address.lower() not in visible]
    return (("Bcc: " + ", ".join(hidden) + "\r\n").encode("ascii") if hidden else b"") + message.raw


class APIMailProvider:
    """Shared HTTP safety; subclasses own endpoint and mailbox semantics."""

    def __init__(self, config):
        """Keep construction lazy and free of network traffic."""
        self.config = config

    def request(self, method, url, *, headers=None, content=None, json=None):
        """Reject cross-origin continuations before attaching account credentials."""
        import httpx

        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != self.host
            or parsed.fragment
            or not parsed.path.startswith(self.prefix)
        ):
            raise MailboxError("invalid_provider_url")
        token = (
            self.config.token_provider()
            if self.config.token_provider
            else self.config.credentials.get("access_token")
        )
        expiry = None if self.config.token_provider else self.config.credentials.get("expires_at")
        if not token or (expiry is not None and float(expiry) <= time.time() + 30):
            raise MailboxError("reauthorization_required")
        with (
            httpx.Client(
                timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False
            ) as client,
            client.stream(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
                content=content,
                json=json,
            ) as response,
        ):
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 30 * 1024 * 1024:
                    raise MailboxError("provider_response_too_large")
            return response.status_code, bytes(body)

    def get_json(self, url):
        """Return bounded JSON or a safe cursor/authentication error."""
        import json

        status, body = self.request("GET", url, headers=self.headers)
        if status in (404, 410):
            raise MailboxError("cursor_expired")
        if status in (401, 403):
            raise MailboxError("reauthorization_required")
        if status != 200:
            raise MailboxError("provider_unavailable")
        return json.loads(body)

    def reconcile(self, message):
        """A MIME match alone cannot prove acceptance of hidden recipients."""
        return SendObservation.unknown(len(message.recipients))

    def _send(self, message, url, *, google=False):
        if message.account_id != self.config.account_id:
            raise MailboxError("account_mismatch")
        try:
            raw = api_mime(message)
            if google:
                status, _ = self.request(
                    "POST", url, json={"raw": base64.urlsafe_b64encode(raw).decode()}
                )
            else:
                status, _ = self.request(
                    "POST",
                    url,
                    headers={**self.headers, "Content-Type": "text/plain"},
                    content=base64.b64encode(raw),
                )
        except MailboxError as exc:
            if exc.code in ("reauthorization_required", "invalid_provider_url"):
                return SendObservation(
                    "failed_before_effect", ("rejected",) * len(message.recipients), "pre_dispatch"
                )
            return SendObservation.unknown(len(message.recipients))
        except Exception:
            return SendObservation.unknown(len(message.recipients))
        if status == (200 if google else 202):
            return SendObservation(
                "succeeded", ("accepted",) * len(message.recipients), "transport_response"
            )
        if status in (400, 401, 403, 404, 413, 415, 422, 429):
            return SendObservation(
                "failed_before_effect",
                ("rejected",) * len(message.recipients),
                "transport_response",
            )
        return SendObservation.unknown(len(message.recipients))
