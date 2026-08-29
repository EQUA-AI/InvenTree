"""R3 media retrieval scope: denial is indistinguishable from nonexistence.

The filter names only the actor's clients, never anyone else's.

Runs ``search_corpus_media`` against the shared RAG fixtures with the
production scope path live (resolver seam -> ``scope_for_actor`` ->
``client_codes_for_actor``) and only the network clients faked. The machine
and work-order resolvers are the real ``assets.ai_read.machines_in_scope``
and ``tasks.ai_read`` helpers under the acting user, so an out-of-scope
work-order photo and a nonexistent one take the same path — an off-scope
client's evidence media is invisible because the filter carries only the
actor's own codes.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings

from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

from ai.core.integrations.media_corpus import MediaRetrievalError, search_corpus_media

from .test_attachment_rag_ingestion import RagFixtureTestCase, _ai_settings
from .test_attachment_rag_media_ingestion import FakeGeminiClient, _media_settings

_GRANTS: dict[str, set[MaintenanceScope]] = {}

#: The non-negotiable prefix of every evidence-media filter for an acme-only
#: actor: site pin, currency, the evidence trust tier, the owner allow-list,
#: and the actor's own client codes — clause order is part of the contract.
_BASE_FILTER = (
    "scope_key eq 'site-a' and is_current eq true and "
    "access_class eq 'evidence_recording' and "
    "search.in(model_type, 'workorder,workorderstepexecution,assetmachine', ',') "
    "and client_codes/any(c: search.in(c, 'acme', ','))"
)


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
        """Initialize recorders and the scripted result set."""
        self.filters: list[str] = []
        self.calls = 0
        self._rows = rows or []

    def search(self, **kwargs):
        """Record the built filter and answer with the scripted rows."""
        self.calls += 1
        self.filters.append(kwargs['filter'])
        return list(self._rows)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver',
    # The real machine/work-order resolvers have their own kill-switches;
    # narrowing needs them lit, while scope authority stays the resolver above.
    AIMMS_MACHINE_AI_READ_ENABLED=True,
    AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
)
class MediaRetrievalScopeTests(RagFixtureTestCase):
    """Cross-client scoping through the real resolver and code projection."""

    @classmethod
    def setUpTestData(cls):
        """Fixtures plus an acme-scoped actor and one WO per client."""
        super().setUpTestData()
        cls.actor = get_user_model().objects.create_superuser(
            username='acme-tech', password='x'
        )
        cls.wo_acme = WorkOrder.objects.create(
            title='Press 1 bearing swap',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine,
        )
        cls.wo_zeta = WorkOrder.objects.create(
            title='Press 2 seal service',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=cls.machine_zeta,
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
        """Run one media search with faked network clients only."""
        search_client = FakeSearchClient(rows=rows)
        ai_settings = _media_settings(FEATURE_MEDIA_RAG_RETRIEVAL=True)
        with mock.patch('ai.core.config.get_settings', return_value=ai_settings):
            result = search_corpus_media(
                user=self.actor,
                query=kwargs.pop('query', 'what does the nameplate read'),
                search_client=search_client,
                embedding_client=FakeGeminiClient(),
                **kwargs,
            )
        return search_client, result

    def test_filter_pins_tier_owners_and_only_the_granted_client(self):
        """The unnarrowed filter is exactly the pinned clause sequence."""
        search_client, result = self._search()
        self.assertEqual(len(search_client.filters), 1)
        self.assertEqual(search_client.filters[0], _BASE_FILTER)
        self.assertNotIn('zeta', search_client.filters[0])
        self.assertEqual(result['work_order_filter'], 'not_requested')
        self.assertEqual(result['machine_filter'], 'not_requested')

    def test_in_scope_machine_narrows_by_serial(self):
        """A granted machine narrows on its indexed serial."""
        search_client, result = self._search(machine='Press 1')
        self.assertEqual(result['machine_filter'], 'applied')
        self.assertIn("asset_id eq 'SN-100'", search_client.filters[0])

    def test_work_order_and_machine_narrowings_combine(self):
        """WO and machine narrowing AND together (inverted from the doc tool)."""
        search_client, result = self._search(
            work_order=self.wo_acme.reference, machine='Press 1'
        )
        self.assertEqual(result['work_order_filter'], 'applied')
        self.assertEqual(result['machine_filter'], 'applied')
        self.assertEqual(
            search_client.filters[0],
            _BASE_FILTER
            + f' and work_order_id eq {self.wo_acme.pk}'
            + " and asset_id eq 'SN-100'",
        )

    def test_foreign_and_missing_machines_are_indistinguishable(self):
        """Denial == nonexistence for machine narrowing.

        Zeta's machine and a fabricated name must produce byte-identical
        response shapes (both degrade site-wide, both still carry the
        acme-only client filter).
        """
        foreign_client, foreign = self._search(machine='Press 2')
        missing_client, missing = self._search(machine='No Such Machine 999')

        self.assertEqual(_indistinguishable(foreign), _indistinguishable(missing))
        self.assertEqual(foreign['machine_filter'], 'not_applied')
        self.assertEqual(foreign_client.filters, missing_client.filters)
        for built in foreign_client.filters + missing_client.filters:
            self.assertNotIn('SN-200', built)
            self.assertNotIn('zeta', built)

    def test_foreign_and_missing_work_orders_are_indistinguishable(self):
        """A cross-client WO's photos are as invisible as a nonexistent WO's.

        Neither narrows, neither leaks an id, and the client clause stays
        acme-only either way.
        """
        foreign_client, foreign = self._search(work_order=self.wo_zeta.reference)
        missing_client, missing = self._search(work_order='WO-NOPE-999')

        self.assertEqual(_indistinguishable(foreign), _indistinguishable(missing))
        self.assertEqual(foreign['work_order_filter'], 'not_applied')
        self.assertEqual(foreign_client.filters, missing_client.filters)
        for built in foreign_client.filters + missing_client.filters:
            self.assertEqual(built, _BASE_FILTER)
            self.assertNotIn(str(self.wo_zeta.pk), built.split("'acme'")[-1])
            self.assertNotIn('zeta', built)

    def test_ungranted_actor_is_refused_not_widened(self):
        """An actor with no grants gets a refusal, never an unscoped query."""
        _GRANTS.clear()
        with self.assertRaises(MediaRetrievalError) as caught:
            self._search()
        self.assertEqual(caught.exception.code, 'MEDIA_SCOPE_UNRESOLVED')

    def test_clientless_machine_never_appears_in_a_filter(self):
        """The orphan press resolves to no scope.

        Narrowing degrades and the client clause still names acme alone.
        """
        search_client, result = self._search(machine='Orphan Press')
        self.assertEqual(result['machine_filter'], 'not_applied')
        self.assertNotIn('Orphan', search_client.filters[0])

    def test_disabled_flag_refuses_before_any_network_call(self):
        """Retrieval dark means refusal before embedding or searching."""
        search_client = FakeSearchClient()
        embedder = FakeGeminiClient()
        with (
            mock.patch(
                'ai.core.config.get_settings', return_value=_ai_settings()
            ),
            self.assertRaises(MediaRetrievalError) as caught,
        ):
            search_corpus_media(
                user=self.actor,
                query='nameplate',
                search_client=search_client,
                embedding_client=embedder,
            )
        self.assertEqual(caught.exception.code, 'MEDIA_RETRIEVAL_DISABLED')
        self.assertEqual(search_client.calls, 0)
        self.assertEqual(embedder.query_calls, 0)

    def test_blank_site_scope_refuses_before_any_network_call(self):
        """An empty policy key refuses instead of widening site-wide."""
        search_client = FakeSearchClient()
        embedder = FakeGeminiClient()
        blank = _media_settings(
            FEATURE_MEDIA_RAG_RETRIEVAL=True, single_site_policy_key=''
        )
        with (
            mock.patch('ai.core.config.get_settings', return_value=blank),
            self.assertRaises(MediaRetrievalError) as caught,
        ):
            search_corpus_media(
                user=self.actor,
                query='nameplate',
                search_client=search_client,
                embedding_client=embedder,
            )
        self.assertEqual(caught.exception.code, 'MEDIA_SCOPE_UNCONFIGURED')
        self.assertEqual(search_client.calls, 0)
        self.assertEqual(embedder.query_calls, 0)
