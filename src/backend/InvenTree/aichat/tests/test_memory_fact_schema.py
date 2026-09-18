"""Authored PostgreSQL authority, vocabulary and deletion-contract regressions."""

from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from ai.core.memory.vocabulary import (
    FactLifecycle,
    FactOrigin,
    FactVerification,
    MemoryType,
    Topic,
)
from aichat import memory_choices as choices
from aichat.models import ChatThread, MemoryFact, MemoryFactClaim
from aichat.services.memory_retention import purge_thread_memory, thread_memory_residual


class MemoryVocabularyTests(SimpleTestCase):
    """Durable enums share the plan's vocabulary without changing M1 item labels."""

    def test_durable_choices_match_existing_binding_sets(self):
        """Resolved remains valid only for summary open-question records."""
        for actual, expected in (
            (choices.MemoryVerification, FactVerification),
            (choices.MemoryOrigin, FactOrigin),
            (choices.DurableMemoryType, MemoryType),
            (choices.MemoryTopic, Topic),
        ):
            self.assertEqual(set(actual.values), {member.value for member in expected})
        self.assertEqual(
            set(choices.MemoryLifecycle.values),
            {member.value for member in FactLifecycle} - {'resolved'},
        )


class MemoryFactSchemaTests(TestCase):
    """Exercise database constraints with direct writes that bypass service validation."""

    def setUp(self):
        """Create one private, unconfirmed preference and its transcript source."""
        self.owner = get_user_model().objects.create_user(username='fact-schema-owner')
        self.thread = ChatThread.objects.create(
            owner=self.owner, scope_key='fixture', scope_hash='fixture'
        )

    def fact(self, **changes):
        """A safe proposal fixture; this helper never grants confirmed authority."""
        fields = {
            'owner': self.owner,
            'source_thread': self.thread,
            'entity_kind': 'user',
            'entity_id': str(self.owner.pk),
            'slot_key': 'answer_style',
            'memory_type': 'user_preference',
            'text': 'Explain one step at a time.',
            'text_lang': 'en',
            'origin': 'user_explicit',
            'origin_modality': 'chat',
            'visibility_scope': 'user_private',
            'classification': 'preference',
            'notice_version': 'memory-v1',
            'claim_fingerprint': 'a' * 64,
        }
        fields.update(changes)
        return MemoryFact.objects.create(**fields)

    @skipUnless(
        connection.vendor == 'postgresql',
        'Array/vector checks require PostgreSQL + pgvector',
    )
    def test_direct_sql_cannot_elevate_inferred_or_cross_scope_preferences(self):
        """Choices alone are insufficient; database checks reject invalid writes."""
        for change in (
            {'lifecycle_state': 'active'},
            {'lifecycle_state': 'resolved'},
            {'client_code': 'foreign-client'},
            {'verification_class': 'invented'},
            {'topics': ['invented']},
            {'topics': ['parts', 'safety', 'planning', 'controls']},
            {'notice_version': ''},
            {'prohibited': True},
            {'lifecycle_state': 'forgotten'},
            {'embedding': [0.0] * 3},
        ):
            with self.subTest(change=change):
                with self.assertRaises((IntegrityError, ValueError)):
                    with transaction.atomic():
                        self.fact(**change)

    def test_thread_purge_deletes_proposals_and_severs_retained_facts(self):
        """Confirmed history can retain a fact only after source pointers are removed."""
        proposed = self.fact()
        retained = self.fact(lifecycle_state='superseded')
        claim = MemoryFactClaim.objects.create(
            fact=retained,
            source_thread=self.thread,
            source_class='user_utterance',
            content_trust='untrusted_fenced',
            source_fingerprint='b' * 64,
        )
        purge_thread_memory(self.thread.pk)
        self.assertFalse(MemoryFact.objects.filter(pk=proposed.pk).exists())
        retained.refresh_from_db()
        claim.refresh_from_db()
        self.assertIsNone(retained.source_thread_id)
        self.assertIsNone(claim.source_thread_id)
        self.assertIsNotNone(claim.severed_at)
        self.assertEqual(thread_memory_residual(self.thread.pk), 0)
        purge_thread_memory(self.thread.pk)
        self.assertEqual(thread_memory_residual(self.thread.pk), 0)
