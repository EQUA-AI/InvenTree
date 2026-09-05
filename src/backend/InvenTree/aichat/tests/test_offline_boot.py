"""M1 no-egress boot proof (GR-26): the AI surface imports and checks offline.

Runs to completion only on the ``no-egress`` CI lane, where ``pytest-socket``
is installed and ``TIKTOKEN_CACHE_DIR`` points at a pre-warmed vocabulary.
Everywhere else the class skips visibly. Sockets are disabled in
``setUpClass`` (not via the pytest plugin) so the denial holds under the
Django test runner; unix sockets stay allowed because asyncio's self-pipe
needs a socketpair.
"""

from __future__ import annotations

import importlib
import unittest

from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase

try:  # pragma: no cover - lane-only dependency
    import pytest_socket
except ImportError:  # pragma: no cover
    pytest_socket = None


@unittest.skipIf(
    pytest_socket is None, 'pytest-socket is not installed (no-egress lane only)'
)
class OfflineBootTest(SimpleTestCase):
    """Nothing on the boot path may open a network socket."""

    @classmethod
    def setUpClass(cls):
        """Deny every non-unix socket for the duration of the class."""
        super().setUpClass()
        pytest_socket.disable_socket(allow_unix_socket=True)

    @classmethod
    def tearDownClass(cls):
        """Restore sockets for the rest of the run."""
        pytest_socket.enable_socket()
        super().tearDownClass()

    def test_ai_surface_imports_without_network(self):
        """Config, usage, the turn service and the boundary app import offline."""
        for name in (
            'ai.core.config',
            'ai.core.usage',
            'ai.core.turn_service',
            'ai.core.app',
        ):
            importlib.import_module(name)

    def test_token_estimator_serves_from_the_baked_cache(self):
        """With tiktoken present the baked vocabulary answers; absent -> None."""
        from ai.core.usage import _token_encoder, estimate_tokens

        _token_encoder.cache_clear()
        try:
            estimate = estimate_tokens('offline boot proof')
            if importlib.util.find_spec('tiktoken') is None:
                self.assertIsNone(estimate)
            else:
                # A network fetch would have raised inside _token_encoder and
                # degraded to None; an int proves the cache served the encoder.
                self.assertIsInstance(estimate, int)
                self.assertGreater(estimate, 0)
        finally:
            _token_encoder.cache_clear()

    @unittest.skipUnless(
        connection.vendor == 'sqlite',
        'the system check walks model choices that touch the database; a TCP '
        'database is a socket by definition (the lane runs SQLite)',
    )
    def test_manage_check_passes_offline(self):
        """The Django system check needs no egress on the SQLite lane."""
        call_command('check', verbosity=0)
