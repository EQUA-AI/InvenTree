"""Independent PostgreSQL connections exercise native authority serialization."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from integration_state import native_authority as service
from integration_state.models import NativeAuthorityDecision, NativeAuthorityRequest
from integration_state.tests.test_native_authority import NativeAuthorityFixture


@skipUnless(connection.vendor == 'postgresql', 'Requires owned PostgreSQL connections')
class NativeAuthorityConcurrencyTests(NativeAuthorityFixture, TransactionTestCase):
    """Use actual separately committed service transactions; no lock mocks."""

    def parallel(self, actions):
        """Start two actual connection-local calls at the same boundary."""
        barrier = Barrier(2)

        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                try:
                    return action()
                except service.NativeAuthorityError:
                    return 'refused'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, actions))

    def test_same_key_commits_one_exact_request_under_concurrent_replay(self):
        """Concurrent exact retries cannot create competing immutable requests."""
        results = self.parallel([lambda: self.attest().pk, lambda: self.attest().pk])
        self.assertEqual(results[0], results[1])
        self.assertNotEqual(results[0], 'refused')
        self.assertEqual(NativeAuthorityRequest.objects.count(), 1)

    def test_conflicting_reviews_cannot_overwrite_the_committed_decision(self):
        """The losing exact-revision review observes an immutable conflict."""
        row = self.attest()
        view = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )

        def decide(value):
            result = service.decide_authority_request(
                self.request(self.reviewer),
                request_pk=row.pk,
                expected_request_digest=view['request_digest'],
                expected_impact_digest=view['impact_digest'],
                approved=value,
            )
            return result.approved

        results = self.parallel([lambda: decide(True), lambda: decide(False)])
        self.assertEqual(results.count('refused'), 1)
        self.assertEqual(NativeAuthorityDecision.objects.count(), 1)
        self.assertIn(NativeAuthorityDecision.objects.get().approved, results)
