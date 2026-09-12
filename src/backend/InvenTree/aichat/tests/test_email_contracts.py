"""Provider contracts exercised without a database or live mail service."""

import os
import subprocess
import sys
from unittest import TestCase

from ai.core.integrations.email.contracts import (
    AccountConfig,
    MailboxError,
    PreparedEmail,
    SendObservation,
    SyncPage,
)
from ai.core.integrations.email.factory import get_provider


class MailboxContractTests(TestCase):
    """Shared result semantics and recording-provider isolation."""

    def setUp(self):
        """Create a deterministic, network-free submission."""
        self.config = AccountConfig('account-a', 'recording', 'sender@example.test')
        self.message = PreparedEmail(
            'account-a',
            'operation-a',
            self.config.address,
            ('to@example.test', 'bcc@example.test'),
            '<test@example.test>',
            b'body',
            'fingerprint',
        )
        self.provider = get_provider(self.config, allow_recording=True)

    def test_success_without_provider_ids(self):
        """An observed whole-envelope acceptance does not require remote IDs."""
        observation = self.provider.submit(self.message).validate(2)
        self.assertEqual(observation.outcome, 'succeeded')
        self.assertIsNone(observation.provider_message_id)

    def test_recipient_evidence_controls_classification(self):
        """Reject contradictory or incomplete outcomes rather than invent proof."""
        cases = [
            SendObservation(
                'succeeded', ('accepted', 'rejected'), 'transport_response'
            ),
            SendObservation('partial', ('unknown', 'rejected'), 'transport_response'),
            SendObservation(
                'failed_before_effect', ('unknown', 'unknown'), 'inconclusive'
            ),
            SendObservation('succeeded', ('accepted', 'accepted'), 'pre_dispatch'),
            SendObservation('succeeded', ('accepted',), 'transport_response'),
        ]
        for observation in cases:
            with self.subTest(observation=observation), self.assertRaises(MailboxError):
                observation.validate(2)
        SendObservation(
            'partial', ('accepted', 'rejected'), 'transport_response'
        ).validate(2)
        SendObservation.unknown(2).validate(2)

    def test_factory_never_defaults_to_recording_or_notification(self):
        """Recording requires explicit authorization and accounts are mandatory."""
        with self.assertRaises(MailboxError):
            get_provider(self.config)
        with self.assertRaises(MailboxError):
            get_provider(AccountConfig('', 'recording', ''), allow_recording=True)

    def test_account_isolation_and_duplicate_visibility(self):
        """Separate instances cannot share mailbox state or hide duplicate calls."""
        other = get_provider(
            AccountConfig('account-b', 'recording', 'other@example.test'),
            allow_recording=True,
        )
        with self.assertRaises(MailboxError):
            other.submit(self.message)
        self.provider.submit(self.message)
        self.provider.submit(self.message)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(other.calls, [])

    def test_unknown_reconciliation_does_not_dispatch(self):
        """A missing receipt remains unknown and reconciliation never submits."""
        self.provider.observations.append(TimeoutError())
        with self.assertRaises(TimeoutError):
            self.provider.submit(self.message)
        self.assertEqual(self.provider.reconcile(self.message).outcome, 'unknown')
        self.assertEqual(len(self.provider.calls), 1)

    def test_sync_page_scope_and_checkpoint(self):
        """Empty intermediate pages are valid; premature checkpoints are not."""
        page = SyncPage('Inbox', continuation={'page': 1}, complete=False)
        self.provider.pages[('Inbox', 0)] = page
        self.assertEqual(self.provider.sync('Inbox', {}).validate('Inbox', 100), page)
        self.assertEqual(self.provider.sync('Inbox', {}), page)
        for invalid in (
            SyncPage('Sent', checkpoint={}),
            SyncPage('Inbox'),
            SyncPage('Inbox', continuation={}, checkpoint={}),
        ):
            with self.subTest(page=invalid), self.assertRaises(MailboxError):
                invalid.validate('Inbox', 100)

    def test_import_without_optional_packages(self):
        """A clean interpreter with no site packages can load mailbox contracts."""
        result = subprocess.run(
            [
                sys.executable,
                '-S',
                '-c',
                'from ai.core.integrations.email.provider import MailboxProvider; from ai.core.integrations.email.factory import get_provider',
            ],
            env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
