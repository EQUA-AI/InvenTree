"""S1: server-owned thread analysis scope — repository behavior.

Covers the durable storage, optimistic concurrency, owner-only writes,
generic authorization rejection, and the atomic per-turn snapshot. The
pure normalization/hash contract is pinned separately in
``ai/core/tests/test_analysis_scope.py``.
"""

from unittest import mock

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

FLEET_REQUEST = {'mode': scope_contract.MODE_ALL_AUTHORIZED}


class ThreadAnalysisScopeTests(TestCase):
    """Storage, versioning, and authorization behavior of the scope service."""

    def setUp(self) -> None:
        """One owner, one stranger, one thread."""
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='scope-owner')
        self.stranger = user_model.objects.create_user(username='scope-stranger')
        self.repository = ThreadRepository(self.owner.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create()

    # ---- storage and versions -------------------------------------------

    def test_new_thread_reads_legacy_unconfirmed(self) -> None:
        """Untyped threads are visibly unconfirmed, never silently converted."""
        payload = self.repository.get_scope(self.thread.pk)
        self.assertEqual(payload['scope']['mode'], scope_contract.MODE_LEGACY)
        self.assertEqual(payload['version'], 0)
        self.assertEqual(payload['hash'], '')
        self.assertEqual(payload['display_label'], 'Scope unconfirmed')
        self.assertTrue(payload['editable'])

    def test_set_scope_versions_hashes_and_round_trips(self) -> None:
        """A stored scope bumps the version, hashes, and reads back exactly."""
        result = self.repository.set_scope(
            self.thread.pk, FLEET_REQUEST, expected_version=0
        )
        self.assertEqual(result['version'], 1)
        self.assertEqual(len(result['hash']), 64)
        self.assertEqual(result['scope']['mode'], scope_contract.MODE_ALL_AUTHORIZED)
        self.assertEqual(result['display_label'], 'Authorized fleet')
        self.assertEqual(self.repository.get_scope(self.thread.pk), result)

    def test_stale_expected_version_conflicts_and_preserves_scope(self) -> None:
        """A stale expected_version conflicts before any write happens."""
        self.repository.set_scope(self.thread.pk, FLEET_REQUEST, expected_version=0)
        with self.assertRaises(ScopeVersionConflict):
            self.repository.set_scope(
                self.thread.pk, FLEET_REQUEST, expected_version=0
            )
        self.assertEqual(self.repository.get_scope(self.thread.pk)['version'], 1)

    def test_site_group_mode_is_rejected_typed(self) -> None:
        """The reserved multi-site mode fails closed until the upgrade."""
        with self.assertRaises(scope_contract.SiteGroupUnavailable):
            self.repository.set_scope(
                self.thread.pk,
                {'mode': scope_contract.MODE_SITE_GROUP},
                expected_version=0,
            )

    # ---- authorization ---------------------------------------------------

    def test_explicit_assets_reject_generically_on_any_unauthorized_id(self) -> None:
        """One bad id rejects the whole update; the message discloses nothing."""

        def half_authorized(user, machine_id):
            """Authorize machine 1 only; 2 is unknown-or-foreign."""
            return object() if int(machine_id) == 1 else None

        with mock.patch('assets.ai_read.authorized_machine', half_authorized):
            with self.assertRaises(ScopeUpdateRejected) as caught:
                self.repository.set_scope(
                    self.thread.pk,
                    {'mode': scope_contract.MODE_EXPLICIT, 'machine_ids': [1, 2]},
                    expected_version=0,
                )
            self.assertNotIn('2', str(caught.exception))
            self.assertEqual(self.repository.get_scope(self.thread.pk)['version'], 0)

            result = self.repository.set_scope(
                self.thread.pk,
                {'mode': scope_contract.MODE_EXPLICIT, 'machine_ids': [1]},
                expected_version=0,
            )
        self.assertEqual(result['scope']['machine_ids'], [1])
        self.assertEqual(result['version'], 1)

    def test_stranger_can_neither_read_nor_write_scope(self) -> None:
        """Another principal's repository cannot even see the thread."""
        other = ThreadRepository(self.stranger.pk, 'site:main')
        with self.assertRaises(ThreadNotFound):
            other.get_scope(self.thread.pk)
        with self.assertRaises(ThreadNotFound):
            other.set_scope(self.thread.pk, FLEET_REQUEST, expected_version=0)

    @override_settings(FEATURE_THREAD_SHARING=True)
    def test_shared_reader_sees_scope_read_only(self) -> None:
        """A read grant exposes the scope but never the update path."""
        self.repository.set_scope(self.thread.pk, FLEET_REQUEST, expected_version=0)
        self.repository.share(self.thread.pk, grantee_id=self.stranger.pk)
        grantee = ThreadRepository(self.stranger.pk, 'site:main')
        payload = grantee.get_scope(self.thread.pk)
        self.assertFalse(payload['editable'])
        self.assertEqual(payload['version'], 1)
        with self.assertRaises(ThreadNotFound):
            grantee.set_scope(self.thread.pk, FLEET_REQUEST, expected_version=1)

    def test_scope_payloads_match_the_wire_contract(self) -> None:
        """Live service payloads validate against the generated-wire mirrors."""
        from ai.core.analysis.wire import ActiveScopeSummary, ThreadScopePayload

        self.repository.set_scope(self.thread.pk, FLEET_REQUEST, expected_version=0)
        ThreadScopePayload.model_validate(self.repository.get_scope(self.thread.pk))
        self.thread.refresh_from_db()
        ActiveScopeSummary.model_validate(self.repository.scope_summary(self.thread))

    def test_client_minted_id_can_receive_scope_before_first_turn(self) -> None:
        """The PUT endpoint's get_or_create + set_scope composition works.

        The machine-page launch sets scope BEFORE the first send, on a
        thread id the client minted; the endpoint materializes the row
        first (the /upload precedent).
        """
        import uuid

        thread_id = f'thread_{uuid.uuid4().hex}'
        self.repository.get_or_create(thread_id)
        result = self.repository.set_scope(
            thread_id, FLEET_REQUEST, expected_version=0
        )
        self.assertEqual(result['version'], 1)

    # ---- per-turn snapshot ----------------------------------------------

    def _begin(self, key: str, content: str = 'Count the work orders'):
        fingerprint = canonical_request_fingerprint(
            content=content,
            modality='text',
            trusted_context={'policy_version': '1'},
            modality_metadata={},
        )
        return self.repository.begin_turn(
            self.thread.pk,
            content=content,
            modality='text',
            trusted_context={'policy_version': '1'},
            modality_metadata={},
            idempotency_key=key,
            request_fingerprint=fingerprint,
            correlation_id=f'corr-{key}',
        )

    def test_unscoped_turn_context_is_stored_verbatim(self) -> None:
        """Threads without typed scope keep the exact client trusted context."""
        begun = self._begin('turn-unscoped')
        self.assertIsNone(begun.scope_snapshot)
        self.assertEqual(begun.turn.trusted_context, {'policy_version': '1'})

    def test_begin_turn_binds_an_immutable_snapshot(self) -> None:
        """Each turn keeps the scope version it started under, forever."""
        self.repository.set_scope(self.thread.pk, FLEET_REQUEST, expected_version=0)
        first = self._begin('turn-one')
        self.assertEqual(first.scope_snapshot['version'], 1)
        self.assertEqual(
            first.turn.trusted_context[ANALYSIS_SCOPE_SNAPSHOT_KEY]['version'], 1
        )

        self.repository.set_scope(
            self.thread.pk,
            {'mode': scope_contract.MODE_ALL_AUTHORIZED, 'display_label': 'Renamed'},
            expected_version=1,
        )
        second = self._begin('turn-two')
        self.assertEqual(second.scope_snapshot['version'], 2)

        first.turn.refresh_from_db()
        self.assertEqual(
            first.turn.trusted_context[ANALYSIS_SCOPE_SNAPSHOT_KEY]['version'], 1
        )

        # Replay returns the ORIGINAL snapshot, not the current scope.
        replayed = self._begin('turn-one')
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.scope_snapshot['version'], 1)
