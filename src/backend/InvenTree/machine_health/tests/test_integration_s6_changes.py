"""S6 source-change observations never apply deletion, replay or tombstones."""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import replace
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from machine_health.services.integration_history import HistoryAuthorizationError
from machine_health.tests.test_integration_s6_adapter import context, fixture
from machine_health.tests.test_integration_s6_wire import observation


def captured(adapter, **limits):
    """Use explicit fake authority only for pure source-contract controls."""
    return adapter.read_history(
        context(adapter), adapter.coverage, authorize=lambda c, w: True, **limits
    )


class FixtureHistoryChangeTests(TestCase):
    """Qualified fixture differences are observations, not source write commands."""

    def test_duplicate_and_replayed_reads_never_create_a_mutation(self):
        """Same-key comparisons are stable and carry no deletion authority."""
        from machine_health.services.integration_s6_adapter import (
            compare_fixture_history,
        )

        adapter = fixture()
        value = captured(adapter)
        result = compare_fixture_history(
            adapter, value, adapter, value, authorize=lambda c, w: True
        )
        self.assertEqual(result.state, 'unchanged')
        self.assertEqual(result.added, ())
        self.assertEqual(result.changed, ())
        self.assertEqual(result.missing, ())
        self.assertEqual(result.tombstones, ())
        assert_literal_bool(self, result.removal_complete, False)
        assert_literal_bool(self, result.deletion_applied, False)

    def test_late_and_corrected_observations_are_not_source_time_watermarks(self):
        """An old-timestamp new identity is retained and provenance changes survive."""
        from machine_health.services.integration_s6_adapter import (
            compare_fixture_history,
        )

        previous = fixture(records=(observation(value=1),))
        latest = fixture(
            records=(
                observation(value=2),
                observation(
                    value=3,
                    source_observation_id='late-event',
                    observed_at=datetime(
                        1969, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc
                    ),
                ),
            )
        )
        result = compare_fixture_history(
            previous,
            captured(previous),
            latest,
            captured(latest),
            authorize=lambda c, w: True,
        )
        self.assertEqual(result.state, 'observed')
        self.assertEqual(result.added, ('late-event',))
        self.assertEqual(result.changed, ('literal-event:0',))
        self.assertTrue(result.reconciliation_required)
        self.assertFalse(result.deletion_applied)

    def test_partial_or_reversed_comparison_cannot_turn_missing_into_deletion(self):
        """Unknown source ordering and an incomplete scan do not certify removal."""
        from machine_health.services.integration_s6_adapter import (
            compare_fixture_history,
        )

        full = fixture()
        empty = fixture(records=())
        result = compare_fixture_history(
            full, captured(full), empty, captured(empty), authorize=lambda c, w: True
        )
        self.assertEqual(result.state, 'observed')
        self.assertEqual(len(result.missing), 2)
        self.assertEqual(result.tombstones, ())
        self.assertFalse(result.removal_complete)
        partial = captured(full, max_samples_per_read=1, max_reads=1)
        result = compare_fixture_history(
            empty, captured(empty), full, partial, authorize=lambda c, w: True
        )
        self.assertEqual(result.state, 'incomplete')
        self.assertEqual(result.missing, ())
        self.assertEqual(result.tombstones, ())
        self.assertFalse(result.deletion_applied)

    def test_changed_mapping_or_lost_current_authority_is_not_comparable(self):
        """A current mapping edit cannot retroactively certify old association."""
        from machine_health.services.integration_s6_adapter import (
            compare_fixture_history,
        )

        previous = fixture()
        current = replace(
            previous,
            mapping=replace(previous.mapping, dictionary_hash='new-reviewed-hash'),
        )
        result = compare_fixture_history(
            previous,
            captured(previous),
            current,
            captured(current),
            authorize=lambda c, w: True,
        )
        self.assertEqual(result.state, 'incomparable')
        self.assertEqual(result.tombstones, ())
        with self.assertRaises(HistoryAuthorizationError):
            compare_fixture_history(
                previous,
                captured(previous),
                current,
                captured(current),
                authorize=lambda c, w: False,
            )

    def test_revocation_during_final_notice_validation_cannot_emit_changes(self):
        """A successful constructor is followed by separate final validation work."""
        from machine_health.services import integration_s6_adapter as module

        adapter = fixture()
        value = captured(adapter)
        original = module.FixtureHistoryChange.__post_init__
        allowed = True
        calls = 0

        def revoke(result):
            nonlocal allowed, calls
            original(result)
            calls += 1
            if calls == 2:
                allowed = False

        with patch.object(module.FixtureHistoryChange, '__post_init__', new=revoke):
            with self.assertRaises(HistoryAuthorizationError):
                module.compare_fixture_history(
                    adapter, value, adapter, value, authorize=lambda c, w: allowed
                )

    def test_revocation_inside_last_source_validation_withholds_notice(self):
        """Fresh authority must follow source validation, not only precede it."""
        from machine_health.services.integration_s6_adapter import (
            FixtureHistoryAdapter,
            compare_fixture_history,
        )

        adapter = fixture()
        value = captured(adapter)
        original = FixtureHistoryAdapter.validate_read
        healthy = []

        def trace(*args, **kwargs):
            healthy.append(1)
            return original(*args, **kwargs)

        with patch.object(FixtureHistoryAdapter, 'validate_read', new=trace):
            result = compare_fixture_history(
                adapter, value, adapter, value, authorize=lambda c, w: True
            )
        self.assertEqual(result.state, 'unchanged')
        calls = []
        allowed = True

        def revoke(*args, **kwargs):
            nonlocal allowed
            result = original(*args, **kwargs)
            calls.append(1)
            if len(calls) == len(healthy):
                allowed = False
            return result

        with patch.object(FixtureHistoryAdapter, 'validate_read', new=revoke):
            with self.assertRaises(HistoryAuthorizationError):
                compare_fixture_history(
                    adapter, value, adapter, value, authorize=lambda c, w: allowed
                )
