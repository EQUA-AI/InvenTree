"""Network-free OAuth double for the isolated Palantir worker."""

import json
import os
import sys
import time
from urllib.parse import unquote


class BasicAuth:
    """Capture only synthetic client credentials."""

    def __init__(self, client_id, client_secret):
        """Decode OAuth basic-auth credentials."""
        self.client_id = unquote(client_id)
        self.client_secret = unquote(client_secret)


class Response:
    """Provide one bounded synthetic token response."""

    def __init__(self, auth):
        """Retain the synthetic authorization."""
        self.auth = auth

    def __enter__(self):
        """Enter the response context."""
        return self

    def __exit__(self, *args):
        """Exit the response context."""
        return False

    def raise_for_status(self):
        """Simulate a denied client."""
        if self.auth.client_id == 'denied':
            raise RuntimeError('synthetic private authentication failure')

    def iter_bytes(self):
        """Return a synthetic token with the selected scope."""
        if self.auth.client_id == 'oversize_token':
            return (b'x' * 16385,)
        scope = 'api:use-ontologies-read'
        if self.auth.client_id == 'extra_scope':
            scope += ' api:use-ontologies-write'
        if self.auth.client_id == 'missing_token':
            return (b'{"token_type":"Bearer"}',)
        return (
            json.dumps({
                'access_token': json.dumps([
                    self.auth.client_id,
                    self.auth.client_secret,
                ]),
                'token_type': 'Bearer',
                'scope': scope,
            }).encode(),
        )


class Client:
    """Reject unexpected networking, credential or scope settings."""

    def __init__(self, **kwargs):
        """Check the worker's HTTP settings."""
        if kwargs != {
            'timeout': 1,
            'verify': True,
            'trust_env': False,
            'follow_redirects': False,
        }:
            raise ValueError('Unexpected OAuth HTTP configuration')

    def __enter__(self):
        """Enter the client context."""
        return self

    def __exit__(self, *args):
        """Exit the client context."""
        return False

    def stream(self, method, url, *, data, auth, headers):
        """Check the OAuth request before returning a response."""
        if (
            method != 'POST'
            or url != 'https://synthetic.example.test/multipass/api/oauth2/token'
            or data
            != {'grant_type': 'client_credentials', 'scope': 'api:use-ontologies-read'}
            or headers != {'Accept': 'application/json'}
            or any(
                name in os.environ
                for name in (
                    'AIMMS_TEST_INHERITED_SECRET',
                    'HTTPS_PROXY',
                    'AIMMS_PALANTIR_CLIENT_ID',
                    'AIMMS_PALANTIR_CLIENT_SECRET',
                )
            )
        ):
            raise ValueError('Unexpected OAuth request')
        if auth.client_id == 'auth_slow':
            time.sleep(30)
        print('synthetic secret in OAuth stdout')
        print('synthetic secret in OAuth stderr', file=sys.stderr)
        os.write(1, b'synthetic secret at fd 1\n')
        return Response(auth)
