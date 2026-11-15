"""Executable generated-SDK-shaped test double for the actual worker protocol."""

import json
import os
import sys
import time
from types import SimpleNamespace


class Config:
    """Require the documented finite-timeout and verified-TLS options."""

    def __init__(self, **kwargs):
        """Reject a permissive/implicit HTTP configuration in the product code."""
        if kwargs != {'timeout': 1, 'verify': True, 'scheme': 'https', 'proxies': {}}:
            raise ValueError('Unexpected SDK HTTP configuration')


class ConfidentialClientAuth:
    """Inspect invocation without an external token endpoint."""

    def __init__(self, **kwargs):
        """Never silently accept a different scope or background refresh mode."""
        if (
            kwargs['scopes'] != ['api:ontologies-read']
            or kwargs['should_refresh'] is not False
            or kwargs['hostname'] != 'synthetic.example.test'
        ):
            raise ValueError('Unexpected SDK authorization')
        if 'AIMMS_TEST_INHERITED_SECRET' in os.environ or 'HTTPS_PROXY' in os.environ:
            raise ValueError('Inherited secret or proxy')
        self.mode = kwargs['client_id']
        self.source = json.loads(kwargs['client_secret'])
        if self.mode == 'auth_slow':
            time.sleep(30)
        # Both Python and fd-level output must be absent from the wire result.
        print('synthetic secret in SDK stdout')
        print('synthetic secret in SDK stderr', file=sys.stderr)
        os.write(1, b'synthetic secret at fd 1\n')
        if self.mode == 'denied':
            raise RuntimeError(
                'https://synthetic.example.test/private?token=synthetic-private-value'
            )


class Collection:
    """Provide only .get; accidental paging/actions/lazy fields must fail."""

    def __init__(self, auth):
        """Retain the private synthetic fixture in this worker only."""
        self.auth = auth

    def get(self, pk, *, properties):
        """Return one selected object with controlled bad responses for tests."""
        if type(pk) is not (int if self.auth.mode == 'integer_key' else str):
            raise ValueError('Unexpected primary key type')
        if properties != [
            'native_pk',
            'client_pk',
            'client_birth',
            'incarnation',
            'namespace',
            'display_name',
        ]:
            raise ValueError('Unexpected property projection')
        mode = self.auth.mode
        if mode == 'slow':
            time.sleep(30)
        if mode == 'timeout':
            raise TimeoutError('synthetic secret in exception')
        source = self.auth.source
        return SimpleNamespace(
            native_pk=int(pk) + (1 if mode == 'wrong_pk' else 0),
            client_pk=str(source['client'])
            if mode == 'string_client'
            else source['client'],
            client_birth=source['client_incarnation'],
            incarnation=source['incarnation'],
            namespace=source['environment'],
            display_name='x' * 20000
            if mode == 'oversize'
            else 'Synthetic remote asset',
            unapproved='This field must never be projected',
        )


class FoundryClient:
    """Mimic the generated ontology collection names, not network behavior."""

    def __init__(self, *, auth, hostname, config):
        """Expose only the approved two synthetic object collections."""
        self.ontology = SimpleNamespace(
            objects=SimpleNamespace(Machine=Collection(auth), Location=Collection(auth))
        )
