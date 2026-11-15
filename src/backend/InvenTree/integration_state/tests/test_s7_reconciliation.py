"""Bounded scan decision controls; no source completeness or deletion proof."""

from integration_state.tests.assertions import assert_literal_bool

import unittest
from dataclasses import replace
from uuid import UUID


class ReconciliationTests(unittest.TestCase):
    """Independent page truth exercises the pure continuity state machine."""

    @staticmethod
    def binding(**changes):
        """Label synthetic source/actor/filter truth without asserting authority."""
        from integration_state.s7_reconciliation import ScanBinding

        return ScanBinding(
            environment='synthetic-s7',
            epoch=UUID(int=1),
            entity_kind='machine',
            aspect='placement',
            actor_key='synthetic-actor',
            client_id=11,
            filter_key='all-assigned',
            generation='fixture-g1',
            **changes,
        )

    @staticmethod
    def identity(number):
        """Use actual typed source coordinates, not guessed list indexes."""
        from integration_state.s7_contracts import SourceAspectIdentity

        return SourceAspectIdentity(
            environment='synthetic-s7',
            epoch=UUID(int=1),
            entity_kind='machine',
            aspect='placement',
            source_id=number,
        )

    def page(
        self,
        binding,
        *,
        offset=0,
        total: int | None = 0,
        numbers=(),
        next_offset=None,
        state='available',
    ):
        """Build source-shaped independent page metadata and identities."""
        from integration_state.s7_reconciliation import ScanPage

        return ScanPage(
            binding=binding,
            offset=offset,
            total=total,
            items=tuple(self.identity(n) for n in numbers),
            next_offset=next_offset,
            state=state,
        )

    def advance(self, progress, page):
        """Reserve the bounded attempt before simulating a source response."""
        from integration_state.s7_reconciliation import accept_page, reserve_page

        reserved = reserve_page(progress)
        return accept_page(
            reserved, page, attempt=reserved.pending_attempt, authorize=lambda _: True
        )

    def test_complete_empty_and_server_capped_nonempty_remain_observations(self):
        """Transport continuity does not prove population or source removal."""
        from integration_state.s7_reconciliation import begin_scan

        binding = self.binding()
        empty = self.advance(
            begin_scan(binding, max_pages=4, max_records=10), self.page(binding)
        )
        self.assertEqual(empty.state, 'transport_complete')
        self.assertEqual(empty.seen, frozenset())
        progress = begin_scan(binding, max_pages=4, max_records=10)
        for offset in range(3):
            progress = self.advance(
                progress,
                self.page(
                    binding,
                    offset=offset,
                    total=3,
                    numbers=(offset + 1,),
                    next_offset=offset + 1 if offset < 2 else None,
                ),
            )
        self.assertEqual(progress.state, 'transport_complete')
        self.assertEqual(len(progress.seen), 3)
        self.assertEqual(progress.attempts_used, 3)
        for result in (empty, progress):
            self.assertFalse(result.population_complete)
            self.assertFalse(result.removal_complete)
            self.assertFalse(result.deletion_applied)
            self.assertEqual(result.tombstones, ())

    def test_malformed_missing_duplicate_and_mixed_pages_never_complete(self):
        """Count/offset/continuation and identity must all agree independently."""
        from integration_state.s7_reconciliation import begin_scan

        binding = self.binding()
        initial = begin_scan(binding, max_pages=5, max_records=10)
        first = self.advance(
            initial, self.page(binding, total=3, numbers=(1,), next_offset=1)
        )
        cases = (
            self.page(binding, offset=2, total=3, numbers=(2,), next_offset=3),
            self.page(binding, offset=1, total=3, numbers=(2,), next_offset=None),
            self.page(binding, offset=1, total=4, numbers=(2,), next_offset=2),
            self.page(binding, offset=1, total=3, numbers=(), next_offset=1),
            self.page(binding, offset=1, total=3, numbers=(1, 2), next_offset=None),
            self.page(binding, offset=1, total=3, numbers=(2, 2), next_offset=None),
            self.page(
                replace(binding, generation='fixture-g2'),
                offset=1,
                total=3,
                numbers=(2, 3),
            ),
            self.page(
                replace(binding, actor_key='other-actor'),
                offset=1,
                total=3,
                numbers=(2, 3),
            ),
            self.page(
                replace(binding, filter_key='different-filter'),
                offset=1,
                total=3,
                numbers=(2, 3),
            ),
        )
        for index, page in enumerate(cases):
            with self.subTest(case=index):
                result = self.advance(first, page)
                self.assertEqual(result.state, 'incomplete')
                self.assertEqual(result.seen, first.seen)
                self.assertEqual(result.offset, 1)
                self.assertFalse(result.population_complete)

    def test_source_fault_retry_and_budget_are_not_zero_or_reset(self):
        """Retry carries the consumed reservation and partial progress forward."""
        from integration_state.s7_reconciliation import (
            begin_scan,
            reserve_page,
            retry_scan,
        )

        binding = self.binding()
        initial = begin_scan(binding, max_pages=2, max_records=10)
        for state in ('unavailable', 'denied', 'incomplete'):
            with self.subTest(state=state):
                failed = self.advance(
                    initial, self.page(binding, total=None, state=state)
                )
                self.assertEqual(failed.state, state)
                self.assertEqual(failed.attempts_used, 1)
                self.assertEqual(failed.seen, frozenset())
                retry = retry_scan(failed, authorize=lambda _: True)
                self.assertEqual(retry.attempts_used, 1)
                partial = self.advance(
                    retry, self.page(binding, total=2, numbers=(1,), next_offset=1)
                )
                exhausted = reserve_page(partial)
                self.assertEqual(exhausted.state, 'budget_exhausted')
                self.assertEqual(exhausted.attempts_used, 2)
                self.assertEqual(exhausted.seen, partial.seen)
                with self.assertRaises(ValueError):
                    retry_scan(exhausted, authorize=lambda _: True)

    def test_attempt_replay_and_conflicting_response_do_not_advance(self):
        """A reservation is not a source event or a high-watermark shortcut."""
        from integration_state.s7_reconciliation import (
            accept_page,
            begin_scan,
            reserve_page,
        )

        binding = self.binding()
        reserved = reserve_page(begin_scan(binding, max_pages=3, max_records=10))
        page = self.page(binding, total=2, numbers=(1,), next_offset=1)
        result = accept_page(
            reserved, page, attempt=reserved.pending_attempt, authorize=lambda _: True
        )
        replay = accept_page(
            result, page, attempt=reserved.pending_attempt, authorize=lambda _: True
        )
        self.assertEqual(result, replay)
        with self.assertRaises(ValueError):
            accept_page(
                result,
                replace(page, total=3),
                attempt=reserved.pending_attempt,
                authorize=lambda _: True,
            )
        with self.assertRaises(ValueError):
            accept_page(reserved, page, attempt=UUID(int=99), authorize=lambda _: True)
        with self.assertRaises(ValueError):
            reserve_page(reserved)

    def test_authority_and_scalar_aliases_are_checked_before_return(self):
        """Scripted rechecks test a seam, not real native roles or grants."""
        from integration_state.s7_reconciliation import (
            accept_page,
            begin_scan,
            reserve_page,
        )

        binding = self.binding()
        reserved = reserve_page(begin_scan(binding, max_pages=2, max_records=10))
        page = self.page(binding)
        with self.assertRaises(PermissionError):
            accept_page(
                reserved,
                page,
                attempt=reserved.pending_attempt,
                authorize=lambda _: False,
            )
        checks = iter((True, False))
        with self.assertRaises(PermissionError):
            accept_page(
                reserved,
                page,
                attempt=reserved.pending_attempt,
                authorize=lambda _: next(checks),
            )
        for field, value in (('total', True), ('offset', False), ('next_offset', True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(page, **{field: value})
        for field, value in (('max_pages', True), ('max_records', 0)):
            values = {'max_pages': 3, 'max_records': 10, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                begin_scan(binding, **values)
        too_large = self.advance(
            begin_scan(binding, max_pages=3, max_records=1),
            self.page(binding, total=2, numbers=(1,), next_offset=1),
        )
        self.assertEqual(too_large.state, 'budget_exhausted')
        self.assertEqual(too_large.seen, frozenset())

    def test_actual_s6_comparison_stays_non_actionable_through_s7(self):
        """The real producer may report missing IDs but supplies no removal proof."""
        from integration_state.s7_reconciliation import notice_from_fixture_change
        from machine_health.services.integration_s6_adapter import (
            compare_fixture_history,
        )
        from machine_health.tests.test_integration_s6_adapter import context, fixture

        previous = fixture()
        current = fixture(records=())

        def capture(adapter):
            return adapter.read_history(
                context(adapter), adapter.coverage, authorize=lambda *_: True
            )

        source = compare_fixture_history(
            previous,
            capture(previous),
            current,
            capture(current),
            authorize=lambda *_: True,
        )
        notice = notice_from_fixture_change(source, authorize=lambda: True)
        self.assertEqual(notice.missing, source.missing)
        self.assertEqual(notice.source_schema, source.schema_version)
        self.assertEqual(notice.state, 'observed')
        self.assertTrue(notice.reconciliation_required)
        self.assertEqual(notice.tombstones, ())
        self.assertFalse(notice.removal_complete)
        self.assertFalse(notice.deletion_applied)
        self.assertFalse(hasattr(notice, 'epoch'))
        with self.assertRaises(PermissionError):
            notice_from_fixture_change(source, authorize=lambda: False)
        checks = iter((True, False))
        with self.assertRaises(PermissionError):
            notice_from_fixture_change(source, authorize=lambda: next(checks))
        object.__setattr__(source, 'removal_complete', True)
        with self.assertRaises(ValueError):
            notice_from_fixture_change(source, authorize=lambda: True)

    def test_transport_completion_requires_an_observed_reserved_terminal_page(self):
        """A constructor cannot turn a never-read initial checkpoint into empty success."""
        from integration_state.s7_reconciliation import begin_scan

        binding = self.binding()
        initial = begin_scan(binding, max_pages=2, max_records=10)
        with self.assertRaises(ValueError):
            replace(initial, state='transport_complete', expected_total=0)
        actual_empty = self.advance(initial, self.page(binding))
        self.assertEqual(actual_empty.state, 'transport_complete')
        self.assertEqual(actual_empty.attempts_used, 1)


class FixtureNoticeIntegrityTests(unittest.TestCase):
    """Actual S6 differences remain bound through the private S7 constructor."""

    @staticmethod
    def source():
        """Produce all three groups from independent synthetic source records."""
        from machine_health.services.integration_s6_adapter import (
            compare_fixture_history,
        )
        from machine_health.tests.test_integration_s6_adapter import context, fixture
        from machine_health.tests.test_integration_s6_wire import observation

        previous = fixture(
            records=(
                observation(value=1, source_observation_id='changed-event:07'),
                observation(value=2, source_observation_id='missing-event:09'),
            )
        )
        current = fixture(
            records=(
                observation(value=3, source_observation_id='changed-event:07'),
                observation(value=4, source_observation_id='  added-event:02  '),
            )
        )

        def capture(adapter):
            return adapter.read_history(
                context(adapter), adapter.coverage, authorize=lambda *_: True
            )

        return compare_fixture_history(
            previous,
            capture(previous),
            current,
            capture(current),
            authorize=lambda *_: True,
        )

    def test_schema_valid_notice_for_other_intake_is_refused(self):
        """A useful producer-derived positive cannot certify substituted values."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import notice_from_fixture_change

        source = self.source()
        notice = notice_from_fixture_change(source, authorize=lambda: True)
        self.assertEqual(notice.state, 'observed')
        self.assertEqual(notice.source_schema, 'aimms.s6.fixture-change.v1')
        self.assertEqual(notice.added, ('  added-event:02  ',))
        self.assertEqual(notice.changed, ('changed-event:07',))
        self.assertEqual(notice.missing, ('missing-event:09',))
        self.assertEqual(notice.tombstones, ())
        assert_literal_bool(self, notice.removal_complete, False)
        assert_literal_bool(self, notice.deletion_applied, False)
        forged = replace(notice, added=('foreign-event:02',))
        with patch(
            'integration_state.s7_reconciliation.FixtureReconciliationNotice',
            return_value=forged,
        ) as constructor:
            try:
                with self.assertRaises(ValueError):
                    notice_from_fixture_change(source, authorize=lambda: True)
            finally:
                constructor.assert_called_once()

    def test_source_change_during_notice_construction_is_refused(self):
        """A still-valid changed source cannot make an old output current."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            FixtureReconciliationNotice,
            notice_from_fixture_change,
        )

        source = self.source()
        calls = []

        def construct(**values):
            result = FixtureReconciliationNotice(**values)
            calls.append(result)
            object.__setattr__(source, 'changed', ('rebound-event:07',))
            return result

        with patch(
            'integration_state.s7_reconciliation.FixtureReconciliationNotice',
            side_effect=construct,
        ):
            try:
                with self.assertRaises(ValueError):
                    notice_from_fixture_change(source, authorize=lambda: True)
            finally:
                self.assertEqual(len(calls), 1)
                self.assertEqual(source.changed, ('rebound-event:07',))

    def test_final_authorizer_cannot_rebind_source_or_returned_notice(self):
        """Final authorization is not evidence that previously checked values match."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            FixtureReconciliationNotice,
            notice_from_fixture_change,
        )

        for target in ('source', 'notice', 'both'):
            with self.subTest(target=target):
                source = self.source()
                seen = {'checks': 0, 'activated': False}

                def construct(seen=seen, **values):
                    seen['notice'] = FixtureReconciliationNotice(**values)
                    return seen['notice']

                def authorize(seen=seen, source=source, target=target):
                    seen['checks'] += 1
                    if seen['checks'] == 2:
                        seen['activated'] = True
                        for name, value in (
                            ('source', source),
                            ('notice', seen['notice']),
                        ):
                            if target in (name, 'both'):
                                object.__setattr__(
                                    value, 'added', ('foreign-event:02',)
                                )
                    return True

                with patch(
                    'integration_state.s7_reconciliation.FixtureReconciliationNotice',
                    side_effect=construct,
                ) as constructor:
                    try:
                        with self.assertRaises(ValueError):
                            notice_from_fixture_change(source, authorize=authorize)
                    finally:
                        assert_literal_bool(self, seen['activated'], True)
                        constructor.assert_called_once()

    def test_foreign_carriers_are_refused_before_member_access(self):
        """Neither input nor output shape is permission to execute a foreign member."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import notice_from_fixture_change

        reads = []

        class Foreign:
            @property
            def __post_init__(self):
                reads.append('foreign-validator')
                return lambda: None

        with self.assertRaises(ValueError):
            notice_from_fixture_change(Foreign(), authorize=lambda: True)
        self.assertEqual(reads, [])
        source = self.source()
        with patch(
            'integration_state.s7_reconciliation.FixtureReconciliationNotice',
            return_value=Foreign(),
        ) as constructor:
            with self.assertRaises(ValueError):
                notice_from_fixture_change(source, authorize=lambda: True)
            constructor.assert_called_once()
        self.assertEqual(reads, [])

    def test_equal_detached_notice_preserves_supported_states_and_literal_ids(self):
        """Binding is primitive value equality, not Python data-carrier identity."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            FixtureReconciliationNotice,
            notice_from_fixture_change,
        )

        for state in ('unchanged', 'observed', 'incomplete', 'incomparable'):
            with self.subTest(state=state):
                source = self.source()
                if state != 'observed':
                    source = replace(
                        source, state=state, added=(), changed=(), missing=()
                    )
                produced = []

                def construct(produced=produced, **values):
                    first = FixtureReconciliationNotice(**values)
                    detached = replace(first)
                    self.assertIsNot(first, detached)
                    produced.append(detached)
                    return detached

                with patch(
                    'integration_state.s7_reconciliation.FixtureReconciliationNotice',
                    side_effect=construct,
                ):
                    result = notice_from_fixture_change(source, authorize=lambda: True)
                self.assertIs(result, produced[0])
                self.assertEqual(result.state, state)
                self.assertEqual(result.added, source.added)
                self.assertEqual(result.changed, source.changed)
                self.assertEqual(result.missing, source.missing)
                self.assertEqual(result.tombstones, ())
                assert_literal_bool(self, result.removal_complete, False)
                assert_literal_bool(self, result.deletion_applied, False)

    def test_lost_authority_withholds_success_and_constructor_fault(self):
        """Revocation at the real construction seam takes precedence over an error."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            FixtureReconciliationNotice,
            notice_from_fixture_change,
        )

        for failure in (False, True):
            with self.subTest(failure=failure):
                source = self.source()
                allowed = {'value': True, 'activated': False}

                def construct(allowed=allowed, failure=failure, **values):
                    result = FixtureReconciliationNotice(**values)
                    allowed.update(value=False, activated=True)
                    if failure:
                        raise ValueError('synthetic constructor fault')
                    return result

                with patch(
                    'integration_state.s7_reconciliation.FixtureReconciliationNotice',
                    side_effect=construct,
                ) as constructor:
                    with self.assertRaises(PermissionError):
                        notice_from_fixture_change(
                            source, authorize=lambda allowed=allowed: allowed['value']
                        )
                    constructor.assert_called_once()
                assert_literal_bool(self, allowed['activated'], True)

    def test_memory_and_process_control_faults_preserve_the_actual_exception(self):
        """No constructor fault becomes a success-shaped empty observation."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import notice_from_fixture_change

        for fault in (
            ValueError('synthetic'),
            MemoryError(),
            KeyboardInterrupt(),
            SystemExit(9),
        ):
            with self.subTest(fault=type(fault).__name__):
                source = self.source()
                with patch(
                    'integration_state.s7_reconciliation.FixtureReconciliationNotice',
                    side_effect=fault,
                ) as constructor:
                    with self.assertRaises(type(fault)) as raised:
                        notice_from_fixture_change(source, authorize=lambda: True)
                    self.assertIs(raised.exception, fault)
                    constructor.assert_called_once()

    def test_late_source_validation_cannot_outlive_current_authority(self):
        """Any source-validator work after the last check must not leak a result."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import notice_from_fixture_change

        source = self.source()
        validate = type(source).__post_init__
        seen = {'checks': 0, 'allowed': True}

        def authorize():
            seen['checks'] += 1
            return seen['allowed']

        def validate_source(value):
            validate(value)
            if seen['checks'] >= 2:
                seen['allowed'] = False

        result = None
        with patch.object(type(source), '__post_init__', new=validate_source):
            try:
                result = notice_from_fixture_change(source, authorize=authorize)
            except PermissionError:
                assert_literal_bool(self, seen['allowed'], False)
        if result is not None:
            assert_literal_bool(self, seen['allowed'], True)
            self.assertEqual(result.added, ('  added-event:02  ',))

    def test_final_scalar_aliases_and_deletion_flags_are_refused(self):
        """Last-step equality cannot accept aliases or manufacture removal proof."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            FixtureReconciliationNotice,
            notice_from_fixture_change,
        )

        class EqualString(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        corruptions = (
            ('state', EqualString('incomplete')),
            ('schema', EqualString('unadmitted.v9')),
            ('added', (EqualString('foreign-event'),)),
            ('changed', ['foreign-event']),
            ('tombstones', ['foreign-event']),
            ('removal_complete', True),
            ('deletion_applied', True),
        )
        for target in ('source', 'notice'):
            for field, replacement in corruptions:
                with self.subTest(target=target, field=field):
                    source = self.source()
                    seen = {'checks': 0, 'activated': False}

                    def construct(seen=seen, **values):
                        seen['notice'] = FixtureReconciliationNotice(**values)
                        return seen['notice']

                    def authorize(
                        seen=seen,
                        source=source,
                        target=target,
                        field=field,
                        replacement=replacement,
                    ):
                        seen['checks'] += 1
                        if seen['checks'] == 2:
                            value = source if target == 'source' else seen['notice']
                            name = field
                            if field == 'schema':
                                name = (
                                    'schema_version'
                                    if target == 'source'
                                    else 'source_schema'
                                )
                            object.__setattr__(value, name, replacement)
                            seen['activated'] = True
                        return True

                    with patch(
                        'integration_state.s7_reconciliation.FixtureReconciliationNotice',
                        side_effect=construct,
                    ) as constructor:
                        with self.assertRaises(ValueError):
                            notice_from_fixture_change(source, authorize=authorize)
                        constructor.assert_called_once()
                    assert_literal_bool(self, seen['activated'], True)


class CoverageRegisterTests(unittest.TestCase):
    """Private limitation metadata cannot certify source or writer coverage."""

    @staticmethod
    def progress():
        """Complete an independently known empty transport, not a source population."""
        from integration_state.s7_reconciliation import (
            ScanPage,
            accept_page,
            begin_scan,
            reserve_page,
        )

        binding = ReconciliationTests.binding()
        reserved = reserve_page(begin_scan(binding, max_pages=2, max_records=10))
        return accept_page(
            reserved,
            ScanPage(binding=binding, offset=0, total=0, items=(), next_offset=None),
            attempt=reserved.pending_attempt,
            authorize=lambda _: True,
        )

    def test_limitation_register_preserves_cells_without_qualifying_empty_or_transport(
        self,
    ):
        """An absent gap or successful page transport is never supported capture."""
        from integration_state import s7_reconciliation as peer

        build = getattr(peer, 'coverage_register', None)
        gap_type = getattr(peer, 'CoverageGap', None)
        self.assertTrue(
            callable(build), 'The allocated private coverage register is absent'
        )
        self.assertTrue(
            callable(gap_type), 'The explicit coverage-gap carrier is absent'
        )
        progress = self.progress()
        gaps = tuple(
            gap_type(
                target_kind=kind,
                target_key='  private-source-or-writer  ',
                capability='capture',
                reason='unsupported',
                evidence_key='opaque-contract-reference',
            )
            for kind in ('source', 'writer')
        )
        for items in ((), gaps):
            with self.subTest(populated=bool(items)):
                result = build(progress, items, authorize=lambda _: True)
                self.assertEqual(result.binding, progress.binding)
                self.assertEqual(result.transport_state, 'transport_complete')
                self.assertEqual(result.gaps, items)
                self.assertEqual(result.status, 'unqualified')
                assert_literal_bool(self, result.coverage_complete, False)
                assert_literal_bool(self, result.population_complete, False)
                assert_literal_bool(self, result.native_authorized, False)
                assert_literal_bool(self, result.removal_complete, False)
                assert_literal_bool(self, result.deletion_applied, False)
                self.assertEqual(result.tombstones, ())
                self.assertIsNot(result.binding, progress.binding)
                for original, detached in zip(items, result.gaps, strict=True):
                    self.assertIsNot(original, detached)
                    self.assertEqual(
                        detached.target_key, '  private-source-or-writer  '
                    )

    @staticmethod
    def gap(**changes):
        """Declare limitation truth independently of the register under test."""
        from integration_state.s7_reconciliation import CoverageGap

        values = {
            'target_kind': 'writer',
            'target_key': 'synthetic-uncovered-writer',
            'capability': 'capture',
            'reason': 'unknown',
            'evidence_key': 'opaque-private-evidence',
        }
        values.update(changes)
        return CoverageGap(**values)

    def test_returned_register_must_match_original_scope_state_and_limitations(self):
        """Valid foreign output or corrupt proof flags cannot replace the intake."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import coverage_register

        progress, gaps = self.progress(), (self.gap(),)
        good = coverage_register(progress, gaps, authorize=lambda _: True)
        outputs = [
            replace(good, binding=replace(good.binding, client_id=99)),
            replace(good, transport_state='incomplete'),
            replace(good, gaps=()),
            replace(good, gaps=(replace(gaps[0], evidence_key='foreign-evidence'),)),
        ]
        for flag in (
            'coverage_complete',
            'population_complete',
            'native_authorized',
            'removal_complete',
            'deletion_applied',
            'tombstones',
        ):
            changed = replace(good)
            object.__setattr__(
                changed, flag, ('invented-removal',) if flag == 'tombstones' else True
            )
            outputs.append(changed)
        for number, output in enumerate(outputs):
            with self.subTest(output=number):
                with patch(
                    'integration_state.s7_reconciliation.ScanCoverageRegister',
                    return_value=output,
                ) as constructor:
                    try:
                        with self.assertRaises(ValueError):
                            coverage_register(progress, gaps, authorize=lambda _: True)
                    finally:
                        constructor.assert_called_once()

    def test_final_check_refuses_changed_progress_gap_or_coherent_output(self):
        """Current authority and mutually equal forged carriers are not intake truth."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            ScanCoverageRegister,
            coverage_register,
        )

        for target in ('progress', 'gap', 'output', 'coherent'):
            with self.subTest(target=target):
                progress, gaps = self.progress(), (self.gap(),)
                seen = {'checks': 0, 'activated': False}

                def construct(seen=seen, **values):
                    seen['result'] = ScanCoverageRegister(**values)
                    return seen['result']

                def authorize(
                    _, seen=seen, progress=progress, gaps=gaps, target=target
                ):
                    seen['checks'] += 1
                    if seen['checks'] == 2:
                        seen['activated'] = True
                        if target in ('progress', 'coherent'):
                            object.__setattr__(progress, 'state', 'incomplete')
                        if target in ('gap', 'coherent'):
                            object.__setattr__(gaps[0], 'reason', 'unsupported')
                        if target == 'output':
                            object.__setattr__(seen['result'], 'gaps', ())
                        if target == 'coherent':
                            object.__setattr__(
                                seen['result'], 'transport_state', 'incomplete'
                            )
                            object.__setattr__(
                                seen['result'], 'gaps', (replace(gaps[0]),)
                            )
                    return True

                with patch(
                    'integration_state.s7_reconciliation.ScanCoverageRegister',
                    side_effect=construct,
                ) as constructor:
                    try:
                        with self.assertRaises(ValueError):
                            coverage_register(progress, gaps, authorize=authorize)
                    finally:
                        assert_literal_bool(self, seen['activated'], True)
                        constructor.assert_called_once()

    def test_duplicate_conflict_and_budget_do_not_silently_drop_limitations(self):
        """Repeated cells are not extra coverage; the exact bound stays useful."""
        from integration_state.s7_reconciliation import (
            MAX_COVERAGE_GAPS,
            coverage_register,
        )

        progress, gap = self.progress(), self.gap()
        for items in ((gap, gap), (gap, replace(gap, reason='unsupported')), [gap]):
            with self.subTest(items=type(items).__name__):
                with self.assertRaises(ValueError):
                    coverage_register(progress, items, authorize=lambda _: True)
        rows = tuple(
            self.gap(target_key=f'writer-{n}') for n in range(MAX_COVERAGE_GAPS + 1)
        )
        result = coverage_register(progress, rows[:-1], authorize=lambda _: True)
        self.assertEqual(len(result.gaps), MAX_COVERAGE_GAPS)
        self.assertEqual(
            tuple(x.target_key for x in result.gaps),
            tuple(f'writer-{n}' for n in range(MAX_COVERAGE_GAPS)),
        )
        assert_literal_bool(self, result.coverage_complete, False)
        with self.assertRaises(ValueError):
            coverage_register(progress, rows, authorize=lambda _: True)

    def test_actual_transport_states_remain_visible_and_replay_is_immutable(self):
        """Faults and retry exhaustion cannot qualify complete-empty coverage."""
        from dataclasses import FrozenInstanceError

        from integration_state.s7_reconciliation import (
            ScanPage,
            accept_page,
            begin_scan,
            coverage_register,
            reserve_page,
            retry_scan,
        )

        binding = ReconciliationTests.binding()
        states = [begin_scan(binding, max_pages=1, max_records=1), self.progress()]
        for state in ('unavailable', 'denied', 'incomplete'):
            reserved = reserve_page(begin_scan(binding, max_pages=1, max_records=1))
            fault = accept_page(
                reserved,
                ScanPage(
                    binding=binding,
                    offset=0,
                    total=None,
                    items=(),
                    next_offset=None,
                    state=state,
                ),
                attempt=reserved.pending_attempt,
                authorize=lambda _: True,
            )
            states.append(fault)
            if state == 'unavailable':
                states.append(retry_scan(fault, authorize=lambda _: True))
        self.assertEqual(
            {x.state for x in states},
            {
                'running',
                'transport_complete',
                'unavailable',
                'denied',
                'incomplete',
                'budget_exhausted',
            },
        )
        for progress in states:
            with self.subTest(state=progress.state):
                original = replace(progress)
                first = coverage_register(
                    progress, (self.gap(),), authorize=lambda _: True
                )
                second = coverage_register(
                    progress, (self.gap(),), authorize=lambda _: True
                )
                self.assertEqual(first, second)
                self.assertIsNot(first, second)
                self.assertEqual(first.transport_state, progress.state)
                self.assertEqual(first.status, 'unqualified')
                assert_literal_bool(self, first.population_complete, False)
                self.assertEqual(progress, original)
                with self.assertRaises(FrozenInstanceError):
                    first.gaps = ()

    def test_each_binding_dimension_and_current_authority_are_rechecked(self):
        """Scripted authority exercises scope, not an installed native grant."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            ScanCoverageRegister,
            coverage_register,
        )

        for answer in (False, 1, None):
            with self.subTest(answer=answer), self.assertRaises(PermissionError):
                coverage_register(
                    self.progress(), authorize=lambda _, answer=answer: answer
                )
        answers = iter((True, False))
        with self.assertRaises(PermissionError):
            coverage_register(self.progress(), authorize=lambda _: next(answers))
        changes = {
            'environment': 'other-source',
            'epoch': UUID(int=2),
            'entity_kind': 'work',
            'aspect': 'other-aspect',
            'actor_key': 'other-actor',
            'client_id': 99,
            'filter_key': 'other-filter',
            'generation': 'other-generation',
        }
        for name, value in changes.items():
            with self.subTest(field=name):
                progress = self.progress()
                original = getattr(progress.binding, name)

                def construct(progress=progress, name=name, value=value, **values):
                    result = ScanCoverageRegister(**values)
                    object.__setattr__(progress.binding, name, value)
                    return result

                try:
                    with patch(
                        'integration_state.s7_reconciliation.ScanCoverageRegister',
                        side_effect=construct,
                    ):
                        with self.assertRaises(PermissionError):
                            coverage_register(progress, authorize=lambda _: True)
                finally:
                    object.__setattr__(progress.binding, name, original)
                self.assertEqual(
                    coverage_register(progress, authorize=lambda _: True).status,
                    'unqualified',
                )

    def test_foreign_input_entries_and_outputs_refuse_before_properties(self):
        """No foreign-shaped progress, entry or result is a data capability."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import coverage_register

        touched = []

        class Foreign:
            @property
            def binding(self):
                touched.append('binding')
                return None

            @property
            def target_kind(self):
                touched.append('target_kind')
                return 'writer'

        with self.assertRaises(ValueError):
            coverage_register(Foreign(), authorize=lambda _: True)
        with self.assertRaises(ValueError):
            coverage_register(self.progress(), (Foreign(),), authorize=lambda _: True)
        with patch(
            'integration_state.s7_reconciliation.ScanCoverageRegister',
            return_value=Foreign(),
        ) as constructor:
            with self.assertRaises(ValueError):
                coverage_register(self.progress(), authorize=lambda _: True)
            constructor.assert_called_once()
        self.assertEqual(touched, [])

    def test_scalar_aliases_and_positive_capability_claims_are_refused(self):
        """Admit every compared member before custom equality could run."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import (
            ScanCoverageRegister,
            coverage_register,
        )

        class EqualString(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        for capability in (
            'capture',
            'reconciliation',
            'revision',
            'incarnation',
            'ordering',
            'history',
            'population',
            'retained_copies',
            'removal',
        ):
            for reason in (
                'unsupported',
                'unknown',
                'incomplete',
                'unavailable',
                'denied',
            ):
                result = coverage_register(
                    self.progress(),
                    (self.gap(capability=capability, reason=reason),),
                    authorize=lambda _: True,
                )
                assert_literal_bool(self, result.coverage_complete, False)
        for field, value in (
            ('target_kind', EqualString('source')),
            ('target_key', EqualString('foreign')),
            ('capability', EqualString('capture')),
            ('reason', EqualString('unknown')),
            ('evidence_key', EqualString('foreign')),
            ('reason', 'supported'),
            ('target_key', True),
            ('evidence_key', ''),
            ('target_key', 'x' * 257),
        ):
            with self.subTest(field=field, kind=type(value).__name__):
                with self.assertRaises(ValueError):
                    self.gap(**{field: value})
                progress, gap = self.progress(), self.gap()
                seen = {'checks': 0}

                def construct(**values):
                    return ScanCoverageRegister(**values)

                def authorize(_, seen=seen, gap=gap, field=field, value=value):
                    seen['checks'] += 1
                    if seen['checks'] == 2:
                        object.__setattr__(gap, field, value)
                    return True

                with patch(
                    'integration_state.s7_reconciliation.ScanCoverageRegister',
                    side_effect=construct,
                ):
                    with self.assertRaises(ValueError):
                        coverage_register(progress, (gap,), authorize=authorize)

    def test_private_representations_omit_scope_target_and_evidence_labels(self):
        """Private field access stays useful; repr is not a serializer policy."""
        from integration_state.s7_reconciliation import begin_scan, coverage_register

        progress = begin_scan(
            replace(
                self.progress().binding,
                actor_key='private-actor-sentinel',
                filter_key='private-filter-sentinel',
            ),
            max_pages=1,
            max_records=1,
        )
        gap = self.gap(
            target_key='private-target-sentinel',
            evidence_key='private-evidence-sentinel',
        )
        result = coverage_register(progress, (gap,), authorize=lambda _: True)
        text = repr((result, gap, [result])) + str(result)
        for value in (
            'private-actor-sentinel',
            'private-filter-sentinel',
            'private-target-sentinel',
            'private-evidence-sentinel',
        ):
            self.assertNotIn(value, text)
        self.assertEqual(result.gaps[0].target_key, 'private-target-sentinel')
        self.assertEqual(result.gaps[0].evidence_key, 'private-evidence-sentinel')

    def test_fault_paths_recheck_authority_and_preserve_process_control(self):
        """Errors stay errors or denials, never empty successful registers."""
        from unittest.mock import patch

        from integration_state.s7_reconciliation import coverage_register

        for revoked in (False, True):
            with self.subTest(revoked=revoked):
                allowed = {'value': True}
                fault = ValueError('synthetic-construction-fault')

                def construct(allowed=allowed, revoked=revoked, fault=fault, **_):
                    allowed['value'] = not revoked
                    raise fault

                with patch(
                    'integration_state.s7_reconciliation.ScanCoverageRegister',
                    side_effect=construct,
                ):
                    with self.assertRaises(
                        PermissionError if revoked else ValueError
                    ) as raised:
                        coverage_register(
                            self.progress(),
                            authorize=lambda _, allowed=allowed: allowed['value'],
                        )
                    if not revoked:
                        self.assertIs(raised.exception, fault)
        for fault in (MemoryError(), KeyboardInterrupt(), SystemExit(4)):
            with self.subTest(fault=type(fault).__name__):
                with patch(
                    'integration_state.s7_reconciliation.ScanCoverageRegister',
                    side_effect=fault,
                ):
                    with self.assertRaises(type(fault)) as raised:
                        coverage_register(self.progress(), authorize=lambda _: True)
                    self.assertIs(raised.exception, fault)
