"""VOICE-H04: a canonical no-op scope PUT must not churn the stored scope.

Re-sending the same canonical analysis scope with the current expected
version returns the unchanged canonical payload without advancing the
version, hash, timestamps or turn idempotency. Every existing safeguard
still runs first and is preserved: explicit ids are re-authorized against
real ORM actor permissions (``ClientScopeGrant`` rows) before anything is
stored, a stale ``expected_version`` still conflicts even when the payload
is equal, unknown or revoked ids are still denied generically even when the
request matches the stored set, a shared reader still cannot write, and a
real change still advances the version exactly once.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from ai.core.analysis import scope as scope_contract
from aichat.services.threads import (
    ANALYSIS_SCOPE_SNAPSHOT_KEY,
    ScopeUpdateRejected,
    ScopeVersionConflict,
    ThreadNotFound,
    ThreadRepository,
    canonical_request_fingerprint,
)
from aichat.tests.memory_scope_fixtures import fixture_client_resolver
from assets.models import AssetMachine, Client, ClientScopeGrant


@override_settings(
    AIMMS_MACHINE_AI_READ_ENABLED=True,
    FEATURE_THREAD_SHARING=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=fixture_client_resolver,
)
class ThreadScopeCanonicalNoopTests(TestCase):
    """No-op equality is decided only after lock, version and re-authorization."""

    def setUp(self) -> None:
        """Real client/machines and real per-user grant rows, one thread."""
        suffix = uuid.uuid4().hex[:6]
        self.client_a = Client.objects.create(
            name=f'Noop client {suffix}', code=f'noop-{suffix}'
        )
        self.machine_a = AssetMachine.objects.create(
            name=f'Noop A {suffix}', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name=f'Noop B {suffix}', client=self.client_a
        )
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username=f'noop-owner-{suffix}')
        self.stranger = user_model.objects.create_user(
            username=f'noop-stranger-{suffix}'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.owner, client=self.client_a
        )
        self.repository = ThreadRepository(self.owner.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create()

    def _explicit(self, *machines) -> dict:
        """One explicit request over the given machine rows (any order)."""
        return {
            'mode': scope_contract.MODE_EXPLICIT,
            'machine_ids': [machine.pk for machine in machines],
        }

    def _begin(self, key: str):
        """One real turn through the idempotent intake path."""
        fingerprint = canonical_request_fingerprint(
            content='Count the work orders',
            modality='text',
            trusted_context={'policy_version': '1'},
            modality_metadata={},
        )
        return self.repository.begin_turn(
            self.thread.pk,
            content='Count the work orders',
            modality='text',
            trusted_context={'policy_version': '1'},
            modality_metadata={},
            idempotency_key=key,
            request_fingerprint=fingerprint,
            correlation_id=f'corr-{key}',
        )

    def _stored_state(self) -> dict:
        """The durable scope state of the thread row."""
        self.thread.refresh_from_db()
        return {
            'version': self.thread.analysis_scope_version,
            'hash': self.thread.analysis_scope_hash,
            'scope': self.thread.analysis_scope,
            'updated_at': self.thread.updated_at,
        }

    # ---- the no-op path --------------------------------------------------

    def test_equal_canonical_scope_returns_unchanged_payload_without_churn(
        self,
    ) -> None:
        """Duplicate/order-normalized equality is a pure no-op.

        The request repeats the stored set with duplicates and reversed
        order; canonical normalization collapses it onto the stored scope.
        """
        first = self.repository.set_scope(
            self.thread.pk,
            {
                'mode': scope_contract.MODE_EXPLICIT,
                'machine_ids': [self.machine_b.pk, self.machine_a.pk],
            },
            expected_version=0,
        )
        self.assertEqual(first['version'], 1)
        begun = self._begin('noop-turn')
        self.assertEqual(begun.scope_snapshot['version'], 1)
        before = self._stored_state()

        again = self.repository.set_scope(
            self.thread.pk,
            {
                'mode': scope_contract.MODE_EXPLICIT,
                'machine_ids': [
                    self.machine_b.pk,
                    self.machine_a.pk,
                    self.machine_b.pk,
                ],
            },
            expected_version=1,
        )

        self.assertEqual(again, first)
        self.assertEqual(again, self.repository.get_scope(self.thread.pk))
        self.assertEqual(self._stored_state(), before)

        # Turn idempotency is untouched: the replay still returns the
        # original snapshot under the original key.
        replayed = self._begin('noop-turn')
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.scope_snapshot['version'], 1)
        self.assertEqual(
            replayed.turn.trusted_context[ANALYSIS_SCOPE_SNAPSHOT_KEY]['version'], 1
        )

    def test_stale_expected_version_conflicts_even_for_an_equal_payload(self) -> None:
        """Optimistic concurrency wins over no-op equality."""
        self.repository.set_scope(
            self.thread.pk, self._explicit(self.machine_a), expected_version=0
        )
        before = self._stored_state()

        with self.assertRaises(ScopeVersionConflict):
            self.repository.set_scope(
                self.thread.pk, self._explicit(self.machine_a), expected_version=0
            )

        self.assertEqual(self._stored_state(), before)

    # ---- authorization still gates the no-op path ------------------------

    def test_revoked_grant_is_denied_generically_on_the_same_stored_set(self) -> None:
        """Revoking the real grant row rejects the identical stored set."""
        self.repository.set_scope(
            self.thread.pk, self._explicit(self.machine_a), expected_version=0
        )
        before = self._stored_state()

        ClientScopeGrant.objects.filter(pk=self.grant.pk).delete()

        with self.assertRaises(ScopeUpdateRejected) as caught:
            self.repository.set_scope(
                self.thread.pk, self._explicit(self.machine_a), expected_version=1
            )

        self.assertNotIn(str(self.machine_a.pk), str(caught.exception))
        self.assertEqual(str(caught.exception), 'Scope update rejected')
        self.assertEqual(self._stored_state(), before)

    def test_unknown_id_is_denied_generically_on_the_same_stored_set(self) -> None:
        """A machine that vanished later is now unknown: generic denial."""
        self.repository.set_scope(
            self.thread.pk,
            self._explicit(self.machine_a, self.machine_b),
            expected_version=0,
        )
        before = self._stored_state()
        missing_pk = self.machine_b.pk

        AssetMachine.objects.filter(pk=missing_pk).delete()

        with self.assertRaises(ScopeUpdateRejected) as caught:
            self.repository.set_scope(
                self.thread.pk,
                self._explicit(self.machine_a, self.machine_b),
                expected_version=1,
            )

        self.assertNotIn(str(missing_pk), str(caught.exception))
        self.assertEqual(self._stored_state(), before)

    def test_shared_reader_cannot_write_even_an_equal_payload(self) -> None:
        """A shared read grant never reaches the update path, no-op or not."""
        self.repository.set_scope(
            self.thread.pk, self._explicit(self.machine_a), expected_version=0
        )
        # A share needs a grantee provisioned the way production provision
        # them: a real client-grant row (as the transcript-sharing fixture
        # helper does) — this does not grant any thread write.
        ClientScopeGrant.objects.create(user=self.stranger, client=self.client_a)
        self.repository.share(self.thread.pk, grantee_id=self.stranger.pk)
        before = self._stored_state()

        grantee = ThreadRepository(self.stranger.pk, 'site:main')
        with self.assertRaises(ThreadNotFound):
            grantee.set_scope(
                self.thread.pk, self._explicit(self.machine_a), expected_version=1
            )

        self.assertEqual(self._stored_state(), before)

    # ---- real changes still advance exactly once -------------------------

    def test_changed_scope_advances_the_version_exactly_once(self) -> None:
        """A genuine change bumps the version by one and refreshes the hash."""
        first = self.repository.set_scope(
            self.thread.pk, self._explicit(self.machine_a), expected_version=0
        )
        second = self.repository.set_scope(
            self.thread.pk,
            self._explicit(self.machine_a, self.machine_b),
            expected_version=1,
        )

        self.assertEqual(second['version'], first['version'] + 1)
        self.assertNotEqual(second['hash'], first['hash'])
        self.assertEqual(
            second['scope']['machine_ids'],
            sorted([self.machine_a.pk, self.machine_b.pk]),
        )
        self.assertEqual(self.repository.get_scope(self.thread.pk), second)
