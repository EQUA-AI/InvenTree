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

from tasks.scope import MaintenanceScope

from ai.core.integrations.attachment_corpus import search_corpus_attachments

from .test_attachment_rag_ingestion import (
    FakeEmbeddingClient,
    RagFixtureTestCase,
    _ai_settings,
)

_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())



def _indistinguishable(result):
    """Normalize a corpus result for denial==nonexistence comparison.

    The S5 retrieval envelope mints a fresh random ``retrieval_id`` per call
    — deliberately signal-free (every response gets one), so the
    indistinguishability contract compares everything else byte-identically.
    """
    normalized = dict(result)
    retrieval = dict(normalized.pop('retrieval', {}) or {})
    retrieval.pop('retrieval_id', None)
    normalized['retrieval'] = retrieval
    return normalized

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
        still carry the acme-only client filter).
        """
        foreign_client, foreign = self._search(machine='Press 2')
        missing_client, missing = self._search(machine='No Such Machine 999')

        self.assertEqual(_indistinguishable(foreign), _indistinguishable(missing))
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
        client clause still names acme alone.
        """
        search_client, result = self._search(machine='Orphan Press')
        self.assertEqual(result['machine_filter'], 'not_applied')
        self.assertNotIn('Orphan', search_client.filters[0])


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='acme',
    AIMMS_MACHINE_AI_READ_ENABLED=True,
)
class EvalFixtureIsolationTests(RagFixtureTestCase):
    """S6 (WP-A5): eval-fixtures isolation through the REAL grant resolver.

    No resolver seam here — the production ``granted_client_scope_resolver``
    reads real ``ClientScopeGrant`` rows, so these tests pin the actual
    control: an ordinary user's filter can never name ``eval-fixtures``, the
    designated evaluation user's filter names it alongside the site tenant,
    and ``eval-offlimits`` stays granted to nobody.
    """

    @classmethod
    def setUpTestData(cls):
        """The shared RAG world plus the eval client, machine, and users."""
        super().setUpTestData()
        from assets.models import AssetMachine, Client, ClientScopeGrant

        cls.eval_client = Client.objects.create(
            name='RAG Evaluation Fixtures', code='eval-fixtures'
        )
        cls.eval_machine = AssetMachine.objects.create(
            name='RAG Eval HX-200 Heat Exchanger',
            client=cls.eval_client,
            serial='EVAL-HX200',
        )
        cls.ordinary = get_user_model().objects.create_superuser(
            username='ordinary-solar', password='x'
        )
        cls.evaluator = get_user_model().objects.create_superuser(
            username='solar-evaluation', password='x'
        )
        ClientScopeGrant.objects.create(user=cls.evaluator, client=cls.client_acme)
        ClientScopeGrant.objects.create(user=cls.evaluator, client=cls.eval_client)

    def _search_as(self, user, **kwargs):
        search_client = FakeSearchClient(rows=kwargs.pop('rows', None))
        ai_settings = _ai_settings(FEATURE_ATTACHMENT_RAG_RETRIEVAL=True)
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            result = search_corpus_attachments(
                user=user,
                query=kwargs.pop('query', 'heat exchanger seal'),
                search_client=search_client,
                embedding_client=FakeEmbeddingClient(),
                **kwargs,
            )
        return search_client, result

    def test_ordinary_user_filter_never_names_eval_fixtures(self):
        """The leak S6 removes: broad queries cannot reach the fixtures."""
        search_client, _result = self._search_as(self.ordinary)
        built = search_client.filters[0]
        self.assertIn("client_codes/any(c: search.in(c, 'acme', ','))", built)
        self.assertNotIn('eval-fixtures', built)
        self.assertNotIn('eval-offlimits', built)

    def test_ordinary_user_cannot_narrow_to_the_eval_machine(self):
        """The eval machine is invisible to an ungranted user.

        Resolution fails exactly like a machine that does not exist.
        """
        eval_client_calls, eval_result = self._search_as(
            self.ordinary, machine='RAG Eval HX-200 Heat Exchanger'
        )
        missing_calls, missing_result = self._search_as(
            self.ordinary, machine='No Such Machine 999'
        )
        self.assertEqual(
            _indistinguishable(eval_result), _indistinguishable(missing_result)
        )
        for built in eval_client_calls.filters + missing_calls.filters:
            self.assertNotIn('EVAL-HX200', built)

    def test_evaluation_user_reaches_both_clients(self):
        """The eval user holds acme AND eval-fixtures.

        The golden set stays whole while isolation binds everyone else.
        """
        search_client, _result = self._search_as(self.evaluator)
        self.assertIn(
            "client_codes/any(c: search.in(c, 'acme,eval-fixtures', ','))",
            search_client.filters[0],
        )

    def test_evaluation_user_narrows_to_the_eval_machine(self):
        """Granted scope makes the fixture machine's serial filter work."""
        search_client, result = self._search_as(
            self.evaluator, machine='RAG Eval HX-200 Heat Exchanger'
        )
        self.assertEqual(result['machine_filter'], 'applied')
        self.assertIn("asset_id eq 'EVAL-HX200'", search_client.filters[0])
