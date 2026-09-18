"""PostgreSQL-only SQL authorization and recheck qualification cases."""

import os
import uuid
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.core.analysis.scope import AnalysisScope, scope_to_payload
from ai.core.memory.recall_filter import recall_filter_for
from aichat.models import (
    ChatActionProposal,
    ChatThreadGrant,
    ClientAISettings,
    MemoryFact,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from aichat.services import memory_recall
from aichat.services.threads import ThreadRepository
from assets.models import Client, ClientScopeGrant


@skipUnless(connection.vendor == 'postgresql', 'Recall SQL requires PostgreSQL')
@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class MemoryRecallTests(TestCase):
    """Real SQL, synthetic consent and confirmation receipt; no provider call."""

    def setUp(self):
        """Prepare one client, private thread and confirmed owner preference."""
        self.owner = get_user_model().objects.create_superuser(
            username='recall-owner',
            email='fixture@example.test',
            password='unused-fixture',
        )
        self.other = get_user_model().objects.create_user(username='recall-other')
        self.client = Client.objects.create(
            name='Recall fixture', code='recall-fixture'
        )
        ClientScopeGrant.objects.create(user=self.owner, client=self.client)
        ClientAISettings.objects.create(
            client=self.client,
            memory_enabled=True,
            required_notice_version='memory-v1',
            enabled_at=timezone.now(),
        )
        MemoryNoticeAcknowledgement.objects.create(
            user=self.owner, notice_version='memory-v1'
        )
        self.repository = ThreadRepository(actor=self.owner, scope_key='site:main')
        self.thread, _ = self.repository.get_or_create()
        self.thread.analysis_scope = scope_to_payload(
            AnalysisScope(mode='all_authorized_assets')
        )
        self.thread.save(update_fields=['analysis_scope'])
        identity = uuid.uuid4()
        receipt = ChatActionProposal.objects.create(
            owner=self.owner,
            scope_key=f'user:{self.owner.pk}',
            scope_hash='a' * 64,
            action_type='memory.remember',
            target_memory_fact_id=identity,
            state='executed',
            receipt={'status': 'remembered'},
            policy_version='fixture',
            idempotency_key='fixture',
            expires_at=timezone.now(),
        )
        self.fact = MemoryFact.objects.create(
            id=identity,
            owner=self.owner,
            entity_kind='user',
            entity_id=str(self.owner.pk),
            slot_key='checklist',
            memory_type='user_preference',
            text='A maintenance checklist is preferred.',
            text_lang='en',
            origin='user_explicit',
            origin_modality='chat',
            visibility_scope='user_private',
            classification='preference',
            notice_version='memory-v1',
            claim_fingerprint='a' * 64,
            lifecycle_state='active',
            verification_class='user_confirmed',
            shield_state='clear',
            last_verified_at=timezone.now(),
            confirming_proposal=receipt,
        )
        config = SimpleNamespace(
            feature_semantic_memory_recall=True,
            aimms_memory_notice_version='memory-v1',
            memory_embedding_deployment='fixture',
            aimms_memory_topic_boost=0.0,
        )
        self.enterContext(
            mock.patch.object(memory_recall, 'get_settings', return_value=config)
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))

    def candidates(self):
        """Preferences do not require fabricating a query embedding."""
        return memory_recall.candidates(
            self.repository, self.thread.pk, recall_filter_for('general')
        )

    def test_candidate_and_recheck_are_one_statement_each(self):
        """The two fact-store hops leave one of the three slots for history."""
        with self.assertNumQueries(1):
            candidates = self.candidates()
        self.assertEqual([row['id'] for row in candidates], [self.fact.pk])
        with self.assertNumQueries(1):
            result = memory_recall.reauthorize(
                self.repository, self.thread.pk, candidates
            )
        self.assertEqual(result, candidates)

    def test_version_and_opt_out_changes_fail_closed_at_recheck(self):
        """A result cannot survive mutation or withdrawal between statements."""
        candidates = self.candidates()
        MemoryFact.objects.filter(pk=self.fact.pk).update(version=2)
        self.assertEqual(
            memory_recall.reauthorize(self.repository, self.thread.pk, candidates), []
        )
        candidates = self.candidates()
        UserMemorySettings.objects.create(user=self.owner, opted_out=True)
        self.assertEqual(
            memory_recall.reauthorize(self.repository, self.thread.pk, candidates), []
        )

    def test_shared_thread_and_revoked_client_are_empty(self):
        """Owner status does not override active sharing or a lost client boundary."""
        ChatThreadGrant.objects.create(thread=self.thread, grantee=self.other)
        self.assertEqual(self.candidates(), [])
        ChatThreadGrant.objects.all().delete()
        ClientScopeGrant.objects.filter(user=self.owner).delete()
        self.assertEqual(self.candidates(), [])

    def test_unknown_resolver_and_malformed_scope_do_not_widen(self):
        """Custom scope logic requires its own reviewed SQL projection."""
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER='custom.scope'):
            with self.assertRaises(ValueError):
                self.candidates()
        self.thread.analysis_scope['display_label'] = ['invalid']
        self.thread.save(update_fields=['analysis_scope'])
        self.assertEqual(self.candidates(), [])

    def test_wrong_vector_is_refused_before_any_statement(self):
        """Document-index dimensions cannot be substituted for memory vectors."""
        with self.assertNumQueries(0), self.assertRaises(ValueError):
            memory_recall.candidates(
                self.repository,
                self.thread.pk,
                recall_filter_for('general'),
                query_vector=[1.0] * 3072,
            )
