"""Authored restore non-revival cases; no real restore or providers are used."""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.utils import timezone

from pydantic import SecretStr

from aichat.models import MemoryFact, MemoryFactTombstone, UserMemorySettings
from aichat.services import memory_lifecycle
from aichat.services.retention_journal import (
    SALT,
    JournalError,
    export_journal,
    read_journal,
    replay_journal,
)


class MemoryJournalTests(TestCase):
    """Isolated row rollback emulates old backups without executing management tools."""

    def setUp(self):
        """Bind a disposable actor and HMAC key to the isolated journal source."""
        self.owner = get_user_model().objects.create_user(
            username='memory-journal-owner'
        )
        self.enterContext(
            mock.patch.dict(
                os.environ,
                {
                    'INVENTREE_RETENTION_JOURNAL_SOURCE': 'memory-fixture',
                    'INVENTREE_RESTORE_HOLD': '1',
                },
            )
        )
        self.config = SimpleNamespace(
            aimms_memory_fingerprint_key=SecretStr('fixture-key-' * 4)
        )
        self.enterContext(
            mock.patch.object(
                memory_lifecycle, 'get_settings', return_value=self.config
            )
        )
        self.since = (timezone.now() - timedelta(hours=1)).isoformat()
        self.fact = MemoryFact.objects.create(
            owner=self.owner,
            entity_kind='user',
            entity_id=str(self.owner.pk),
            slot_key='checklist',
            memory_type='user_preference',
            text='Private fixture preference for checklists.',
            text_lang='en',
            origin='compaction',
            origin_modality='chat',
            visibility_scope='user_private',
            classification='preference',
            notice_version='memory-v1',
            claim_fingerprint=memory_lifecycle.claim_fingerprint(
                'Private fixture preference for checklists.'
            ),
        )
        self.snapshot = MemoryFact.objects.filter(pk=self.fact.pk).values().get()

    def source_forget(self):
        """Internal canonical deletion is legal under the isolated test transaction."""
        memory_lifecycle._forget_locked(
            self.fact, actor_id=self.owner.pk, reason='forget'
        )
        return export_journal(since=self.since)

    def restore_fact(self):
        """Roll back the fact and deletion proof, preserving its original birth time."""
        MemoryFact.objects.filter(pk=self.fact.pk).delete()
        MemoryFactTombstone.objects.all().delete()
        values = dict(self.snapshot)
        MemoryFact.objects.create(**values)
        MemoryFact.objects.filter(pk=self.fact.pk).update(
            created_at=values['created_at']
        )

    def test_replay_removes_resurrected_fact_and_preserves_deletion_clock(self):
        """The signed artifact carries only hashes and identity, never claim text."""
        token = self.source_forget()
        payload = read_journal(token, since=self.since)
        stone = payload['memories']['tombstones'][0]
        self.assertNotIn(self.snapshot['text'], str(payload))
        self.restore_fact()
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replayed')
        self.assertFalse(result['hold_released'])
        fact = MemoryFact.objects.get(pk=self.fact.pk)
        self.assertEqual(fact.text, '')
        self.assertEqual(fact.lifecycle_state, 'withdrawn')
        self.assertEqual(
            MemoryFactTombstone.objects.get(pk=stone['id']).deleted_at.isoformat(),
            stone['deleted_at'],
        )
        again = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(again['status'], 'replayed')
        self.assertEqual(MemoryFact.objects.get(pk=fact.pk).version, fact.version)

    def test_key_mismatch_and_reused_account_id_refuse_before_writes(self):
        """A valid signature does not override fingerprint or actor identity drift."""
        token = self.source_forget()
        self.restore_fact()
        self.config.aimms_memory_fingerprint_key = SecretStr(
            'different-fixture-key-' * 3
        )
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replay_incomplete')
        self.assertEqual(
            MemoryFact.objects.get(pk=self.fact.pk).text, self.snapshot['text']
        )
        self.config.aimms_memory_fingerprint_key = SecretStr('fixture-key-' * 4)
        get_user_model().objects.filter(pk=self.owner.pk).update(
            date_joined=timezone.now()
        )
        self.assertGreater(
            replay_journal(token, since=self.since)['memory_conflicts'], 0
        )

    def test_missing_owner_incarnation_cannot_be_exported_as_proof(self):
        """No historical identity backfill is inferred from today's account row."""
        self.source_forget()
        MemoryFactTombstone.objects.update(owner_joined_at=None)
        with self.assertRaises(JournalError):
            export_journal(since=self.since)

    def test_version_two_journal_is_readable_but_does_not_qualify_memory(self):
        """Legacy thread/account evidence is explicitly incomplete for new stores."""
        payload = read_journal(export_journal(since=self.since), since=self.since)
        payload.pop('memories')
        for row in payload['threads']:
            row.pop('forget_confirmed_memories')
        payload.update(
            schema_version=2, scope='retained_threads_and_local_account_intents'
        )
        token = signing.dumps(payload, salt=SALT)
        self.assertEqual(read_journal(token, since=self.since)['schema_version'], 2)
        result = replay_journal(token, since=self.since, execute=True)
        self.assertTrue(result['memory_journal_missing'])
        self.assertEqual(result['status'], 'replay_incomplete')

    def test_opt_out_stop_bit_survives_restore(self):
        """An empty source fact store does not erase the owner's withdrawal intent."""
        UserMemorySettings.objects.create(user=self.owner, opted_out=True)
        token = export_journal(since=self.since)
        UserMemorySettings.objects.all().delete()
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replayed')
        self.assertTrue(UserMemorySettings.objects.get(user=self.owner).opted_out)
        self.assertEqual(MemoryFact.objects.get(pk=self.fact.pk).text, '')

    def test_later_deletion_of_new_row_preserves_earlier_claim_proof(self):
        """Explicit revival and later deletion cannot overwrite the original target."""
        self.source_forget()
        original = MemoryFactTombstone.objects.get(fact_id=self.fact.pk)
        values = dict(self.snapshot)
        values.pop('id')
        replacement = MemoryFact.objects.create(**values)
        memory_lifecycle._forget_locked(
            replacement, actor_id=self.owner.pk, reason='forget'
        )
        original.refresh_from_db()
        self.assertEqual(original.fact_id, self.fact.pk)
        self.assertEqual(MemoryFactTombstone.objects.count(), 2)
        payload = read_journal(export_journal(since=self.since), since=self.since)
        self.assertEqual(len(payload['memories']['tombstones']), 2)
