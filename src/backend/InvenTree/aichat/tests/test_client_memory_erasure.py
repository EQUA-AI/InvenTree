"""Authored client-memory offboarding isolation and restore-proof cases."""

import os
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from pydantic import SecretStr

from aichat.models import ClientMemoryErasure, MemoryFact
from aichat.services import client_memory_erasure as service
from aichat.services import memory_lifecycle
from assets.models import Client


class ClientMemoryErasureTests(TestCase):
    """The shared owner is never itself an erasure selector for another client."""

    def setUp(self):
        """One operator-controlled target and one multi-client owner."""
        self.owner = get_user_model().objects.create_user(username='client-erase-owner')
        self.client = Client.objects.create(
            name='Client erasure fixture', code='erase-fixture'
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.enterContext(
            mock.patch.object(
                memory_lifecycle,
                'get_settings',
                return_value=SimpleNamespace(
                    aimms_memory_fingerprint_key=SecretStr('fixture-key-' * 4)
                ),
            )
        )

    def fact(self, client):
        """Unconfirmed customer-specific prose is never authority."""
        return MemoryFact.objects.create(
            owner=self.owner,
            client_code=client,
            entity_kind='machine',
            entity_id='1',
            slot_key='fixture',
            memory_type='equipment_fact',
            text='Synthetic equipment fact',
            text_lang='en',
            origin='compaction',
            origin_modality='chat',
            visibility_scope='client_shared',
            classification='operational',
            notice_version='memory-v1',
            claim_fingerprint=memory_lifecycle.claim_fingerprint(
                'Synthetic equipment fact'
            ),
        )

    def test_preview_does_not_write_and_execution_preserves_other_client(self):
        """No owner-wide delete or full-client-success receipt is substituted."""
        target, other = self.fact(self.client.code), self.fact('other-client')
        self.assertEqual(service.purge_client(self.client.pk)['status'], 'dry_run')
        self.assertFalse(ClientMemoryErasure.objects.exists())
        report = service.purge_client(self.client.pk, dry_run=False)
        self.assertFalse(report['client_erasure_complete'])
        self.assertEqual(report['memory_status'], 'purged')
        target.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(target.text, '')
        self.assertEqual(other.text, 'Synthetic equipment fact')
        self.assertTrue(
            ClientMemoryErasure.objects.filter(client_id=self.client.pk).exists()
        )

    def test_client_identity_conflict_refuses_restore_replay(self):
        """A restored primary key cannot be repurposed into a grant to erase."""
        from django.utils.dateparse import parse_datetime

        service.purge_client(self.client.pk, dry_run=False)
        proof = service.export_proof()[0]
        Client.objects.filter(pk=self.client.pk).update(code='different-client')
        self.assertTrue(service.conflicts([proof], parse_time=parse_datetime))
        with (
            mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '1'}),
            self.assertRaises(ValueError),
        ):
            service.replay(proof, parse_time=parse_datetime)
