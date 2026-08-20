"""R2 retrieval scope: the filter names only the actor's clients, and
denial is indistinguishable from nonexistence.

Runs ``search_corpus_attachments`` against the shared RAG fixtures with the
production scope path live (resolver seam -> ``scope_for_actor`` ->
``client_codes_for_actor``) and only the network clients faked. The machine
resolver is the real ``assets.ai_read.machines_in_scope`` under the acting
user, so an out-of-scope machine and a nonexistent one take the same path.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings

from ai.core.integrations.attachment_corpus import search_corpus_attachments
from tasks.scope import MaintenanceScope

from .test_attachment_rag_ingestion import (
    FakeEmbeddingClient,
    RagFixtureTestCase,
    _ai_settings,
)

_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


class FakeSearchClient:
    """Records every filter; returns scripted rows."""

    def __init__(self, rows=None):
        self.filters: list[str] = []
        self.calls = 0
        self._rows = rows or []

    def search(self, **kwargs):
        self.calls += 1
        self.filters.append(kwargs['filter'])
        return list(self._rows)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver',
    # machines_in_scope (the real machine resolver) has its own kill-switch;
    # narrowing needs it lit, while scope authority stays the resolver above.
    AIMMS_MACHINE_AI_READ_ENABLED=True,
)
class AttachmentRetrievalScopeTests(RagFixtureTestCase):
    """Cross-client scoping through the real resolver and code projection."""

    @classmethod
    def setUpTestData(cls):
        """Fixtures plus an acme-scoped superuser actor."""
        super().setUpTestData()
        cls.actor = get_user_model().objects.create_superuser(
            username='acme-tech', password='x'
        )

    def setUp(self):
        """Grant the actor acme only; zeta stays foreign."""
        _GRANTS.clear()
        _GRANTS[self.actor.get_username()] = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_acme.pk
            )
        }

    def _search(self, *, rows=None, **kwargs):
        search_client = FakeSearchClient(rows=rows)
        ai_settings = _ai_settings(FEATURE_ATTACHMENT_RAG_RETRIEVAL=True)
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            result = search_corpus_attachments(
                user=self.actor,
                query=kwargs.pop('query', 'commissioning steps'),
                search_client=search_client,
                embedding_client=FakeEmbeddingClient(),
                **kwargs,
            )
        return search_client, result

    def test_filter_names_only_the_granted_client(self):
        search_client, _result = self._search()
        self.assertEqual(len(search_client.filters), 1)
        built = search_client.filters[0]
        self.assertIn("client_codes/any(c: search.in(c, 'acme', ','))", built)
        self.assertNotIn('zeta', built)
        self.assertIn("scope_key eq 'site-a'", built)
        self.assertIn("access_class eq 'attachment_uploaded'", built)

    def test_in_scope_machine_narrows_by_serial(self):
        search_client, result = self._search(machine='Press 1')
        self.assertEqual(result['machine_filter'], 'applied')
        self.assertIn("asset_id eq 'SN-100'", search_client.filters[0])

    def test_foreign_and_missing_machines_are_indistinguishable(self):
        """Denial == nonexistence: zeta's machine and a fabricated name must
        produce byte-identical response shapes (both degrade site-wide, both
        still carry the acme-only client filter)."""
        foreign_client, foreign = self._search(machine='Press 2')
        missing_client, missing = self._search(machine='No Such Machine 999')

        self.assertEqual(foreign, missing)
        self.assertEqual(foreign['machine_filter'], 'not_applied')
        self.assertEqual(foreign_client.filters, missing_client.filters)
        for built in foreign_client.filters + missing_client.filters:
            self.assertNotIn('SN-200', built)
            self.assertNotIn('zeta', built)

    def test_ungranted_actor_is_refused_not_widened(self):
        """An actor with no grants gets a refusal, never an unscoped query."""
        from ai.core.integrations.attachment_corpus import AttachmentRetrievalError

        _GRANTS.clear()
        with self.assertRaises(AttachmentRetrievalError) as caught:
            self._search()
        self.assertEqual(caught.exception.code, 'ATTACHMENT_SCOPE_UNRESOLVED')

    def test_clientless_machine_never_appears_in_a_filter(self):
        """The orphan press resolves to no scope; narrowing degrades and the
        client clause still names acme alone."""
        search_client, result = self._search(machine='Orphan Press')
        self.assertEqual(result['machine_filter'], 'not_applied')
        self.assertNotIn('Orphan', search_client.filters[0])
