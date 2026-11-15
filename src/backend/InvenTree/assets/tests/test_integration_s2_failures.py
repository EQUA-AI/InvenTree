"""Provider faults must not become successful empty or partial native reads."""

import unittest
from dataclasses import replace

from assets.tests.test_integration_s2_provider_routing import (
    FixtureSnapshot,
    fixture_binding,
)


class ReadFailureTests(unittest.TestCase):
    """Faults use synthetic sentinels; no actual source or service is called."""

    def setUp(self):
        """One useful nonempty snapshot and a recording native fallback."""
        self.binding = fixture_binding()
        self.source = FixtureSnapshot(self.binding)
        self.native_calls = []

    def read(self, **changes):
        """Reach the same public router used by the native adapter."""
        from assets.integration_s2_provider import route_native_read

        args = {
            'native': lambda: self.native_calls.append('native'),
            'binding_factory': lambda: self.binding,
            'provider_factory': lambda request: self.source.plan(),
        }
        args.update(changes)
        return route_native_read(**args)

    def test_mixed_page_context_and_generation_fail_without_native_fallback(self):
        """Returned metadata must describe the request that actually executed."""
        for mutation in (
            lambda page: replace(page, binding=replace(page.binding, actor_pk=2)),
            lambda page: replace(page, generation='another-generation'),
            lambda page: replace(page, position=1),
            lambda page: replace(page, sequence=1),
        ):
            self.source.transform = mutation
            with (
                self.subTest(mutation=mutation.__name__),
                self.assertRaises(ValueError),
            ):
                self.read()
        self.assertEqual(self.native_calls, [])

    def test_false_empty_short_and_unproved_coverage_fail(self):
        """The oracle and count/termination rules reject premature success."""
        for mutation in (
            lambda page: replace(page, rows=(), total=0),
            lambda page: replace(page, total=2),
            lambda page: replace(page, coverage_complete=False),
            lambda page: replace(page, next_cursor='unissued:skip'),
            lambda page: replace(page, rows=({'pk': 9, 'name': 'Substitute'},)),
        ):
            self.source.transform = mutation
            with (
                self.subTest(mutation=mutation.__name__),
                self.assertRaises(ValueError),
            ):
                self.read()
        self.assertEqual(self.native_calls, [])

    def test_row_identity_required_fields_and_payload_bounds_fail(self):
        """Do not validate a detached key list while accepting malformed rows."""
        for rows in (
            ({'pk': True, 'name': 'A'},),
            ({'pk': 0, 'name': 'A'},),
            ({'pk': 1},),
            ({'pk': 1, 'name': 'A', 'secret-extra': 'private'},),
            ({'pk': 1, 'name': 'X' * 256},),
            ({'pk': 1, 'name': 'A', 'loop': object()},),
        ):
            self.source.transform = lambda page, rows=rows: replace(page, rows=rows)
            with (
                self.subTest(case=type(rows[0]['pk']).__name__),
                self.assertRaises(ValueError),
            ):
                self.read()
        self.assertEqual(self.native_calls, [])

    def test_accepted_metadata_is_detached_before_independent_proof(self):
        """A callback cannot swap source metadata after its structural checks."""
        seen = []

        def capture(page):
            seen.append(page)
            return page

        self.source.transform = capture
        plan = self.source.plan()

        def prove(detached):
            self.assertTrue(plan.verify(detached))
            object.__setattr__(seen[0], 'total', 1000)
            object.__setattr__(seen[0], 'generation', 'unverified-generation')
            object.__setattr__(seen[0], 'next_cursor', 'unissued-cursor')
            return True

        page = self.read(provider_factory=lambda binding: replace(plan, verify=prove))
        self.assertEqual(page.total, 1)
        self.assertEqual(page.generation, 'fixture-v1')
        self.assertIsNone(page.next_cursor)
        self.assertTrue(page.snapshot_complete)
        self.assertEqual(page.rows, [{'pk': 1, 'name': 'A'}])
        self.assertEqual(self.native_calls, [])

    def test_proof_cannot_mutate_the_snapshot_it_claims_to_verify(self):
        """A true verdict applies to the same rows, keys and typed metadata."""
        from assets.integration_s2_provider import ReadError

        mutations = (
            lambda page: page.rows[0].update(name='Different proof payload'),
            lambda page: object.__setattr__(page, 'total', True),
            lambda page: object.__setattr__(page, 'generation', 'different-proof'),
            lambda page: object.__setattr__(page, 'next_cursor', 'unissued-proof'),
            lambda page: object.__setattr__(page, 'position', False),
            lambda page: object.__setattr__(page, 'sequence', True),
            lambda page: object.__setattr__(page, 'coverage_complete', 1),
        )
        for mutate in mutations:
            source = FixtureSnapshot(self.binding)
            plan = source.plan()

            def prove(page, mutate=mutate, plan=plan):
                self.assertTrue(plan.verify(page))
                mutate(page)
                return True

            with self.subTest(mutate=mutate), self.assertRaises(ReadError):
                self.read(
                    provider_factory=lambda binding, plan=plan: replace(
                        plan, verify=prove
                    )
                )
        self.assertEqual(self.native_calls, [])
        self.assertEqual(self.read().total, 1)

    def test_timeout_and_ordinary_fault_are_sanitized_without_native_fallback(self):
        """A failed selected execution cannot restart as an empty/native success."""
        for fault, expected in (
            (TimeoutError('private-timeout'), 'timeout'),
            (RuntimeError('private-provider-detail'), 'unavailable'),
        ):

            def fetch(*, fault=fault, **kwargs):
                raise fault

            plan = replace(self.source.plan(), fetch=fetch)
            with self.subTest(code=expected), self.assertRaises(ValueError) as raised:
                self.read(provider_factory=lambda request, plan=plan: plan)
            self.assertEqual(raised.exception.code, expected)
            self.assertNotIn('private', str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(self.native_calls, [])

    def test_initial_authority_and_capability_faults_are_sanitized(self):
        """Metadata discovery faults are not raw exceptions or native success."""
        for stage in ('binding_factory', 'provider_factory'):
            for fault, code in (
                (TimeoutError('private-metadata'), 'timeout'),
                (RuntimeError('private-metadata'), 'unavailable'),
            ):

                def fail(*args, fault=fault):
                    raise fault

                with self.subTest(stage=stage, code=code):
                    with self.assertRaises(ValueError) as raised:
                        self.read(**{stage: fail})
                    self.assertEqual(raised.exception.code, code)
                    self.assertNotIn('private', str(raised.exception))
        self.assertEqual(self.native_calls, [])
        self.assertEqual(self.source.calls, [])

    def test_recheck_dependency_faults_preserve_timeout_and_unavailable(self):
        """A failed authority lookup is not an affirmative authority denial."""
        from assets.integration_s2_provider import (
            ReadError,
            ReadTimeoutError,
            ReadUnavailableError,
        )

        for occurrence in (2, 3, 4):
            for fault, code in (
                (TimeoutError('private-recheck'), 'timeout'),
                (ReadTimeoutError(), 'timeout'),
                (ReadUnavailableError(), 'unavailable'),
                (RuntimeError('private-recheck'), 'unavailable'),
            ):
                calls = []

                def binding(*, fault=fault, occurrence=occurrence, calls=calls):
                    calls.append('lookup')
                    if len(calls) == occurrence:
                        raise fault
                    return self.binding

                with self.subTest(occurrence=occurrence, code=code):
                    with self.assertRaises(ReadError) as raised:
                        self.read(binding_factory=binding)
                    self.assertEqual(raised.exception.code, code)
                    self.assertNotIn('private-recheck', str(raised.exception))
        self.assertEqual(self.native_calls, [])
        self.assertEqual(self.read().total, 1)

    def test_authority_is_rechecked_after_provider_work(self):
        """A stale initially allowed binding cannot authorize final emission."""
        revoked = False

        def binding():
            return replace(self.binding, client_ids=(11,)) if revoked else self.binding

        def after_read(page):
            nonlocal revoked
            revoked = True
            return page

        self.source.transform = after_read

        with self.assertRaises(ValueError) as raised:
            self.read(binding_factory=binding)
        self.assertEqual(raised.exception.code, 'denied')
        self.assertEqual(len(self.source.calls), 1)
        self.assertEqual(self.native_calls, [])

    def test_verified_empty_is_not_rejected_as_a_provider_fault(self):
        """A complete authorized empty set is distinct from unknown/denied data."""
        self.source = FixtureSnapshot(self.binding, rows=[])
        page = self.read()
        self.assertEqual(page.rows, [])
        self.assertEqual(page.total, 0)
        self.assertTrue(page.snapshot_complete)

    def test_supported_late_offset_empty_page_is_not_snapshot_completeness(self):
        """Native random-page semantics permit an offset beyond the true total."""
        self.binding = replace(
            self.binding, shape=replace(self.binding.shape, offset=1000)
        )
        self.source = FixtureSnapshot(self.binding)
        page = self.read()
        self.assertEqual(page.rows, [])
        self.assertEqual(page.total, 1)
        self.assertFalse(page.snapshot_complete)
        self.assertEqual(self.source.calls, [(1000, 0, None, 'fixture-v1')])

    def test_oracle_cannot_waive_duplicate_identity_or_row_order(self):
        """A source proof is necessary, not a substitute for row invariants."""
        for rows in (
            [{'pk': 1, 'name': 'A'}, {'pk': 1, 'name': 'B'}],
            [{'pk': 2, 'name': 'B'}, {'pk': 1, 'name': 'A'}],
        ):
            self.source = FixtureSnapshot(self.binding, rows=rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.read()

    def test_payload_tree_and_page_byte_budgets_are_enforced_before_emission(self):
        """Nested, cyclic, oversized and non-JSON data cannot become empty success."""
        from assets.integration_s2_provider import (
            MAX_JSON_ITEMS,
            MAX_PAGE_BYTES,
            ReadError,
        )

        self.binding = replace(
            self.binding,
            shape=replace(self.binding.shape, fields=('name', 'pk', 'payload')),
        )
        cycle = []
        cycle.append(cycle)
        for value in (
            cycle,
            [None] * (MAX_JSON_ITEMS + 1),
            'X' * (MAX_PAGE_BYTES + 1),
            1 << 100,
            float('nan'),
            {True: 'not-a-string-key'},
            object(),
        ):
            with self.subTest(kind=type(value).__name__):
                self.source = FixtureSnapshot(
                    self.binding, rows=[{'pk': 1, 'name': 'A', 'payload': None}]
                )
                self.source.transform = lambda page, value=value: replace(
                    page, rows=({'pk': 1, 'name': 'A', 'payload': value},)
                )
                with self.assertRaises(ReadError):
                    self.read()
        self.assertEqual(self.native_calls, [])

    def test_blank_unicode_names_and_valid_json_payloads_remain_supported(self):
        """Bounds do not discard meaningful null/false/blank values or names."""
        self.binding = replace(
            self.binding,
            shape=replace(self.binding.shape, fields=('name', 'pk', 'payload')),
        )
        for value in (None, False, 0, [], {}, 'synthetic' * 100):
            self.source = FixtureSnapshot(
                self.binding,
                rows=[
                    {'pk': 1, 'name': '', 'payload': value},
                    {'pk': 2, 'name': 'Ä', 'payload': value},
                ],
            )
            self.assertEqual(self.read().rows[0]['payload'], value)
        self.assertEqual(self.native_calls, [])

    def test_coordinate_and_cursor_1024_boundaries_preserve_literal_values(self):
        """Exactly bounded fake-issued tokens work; oversized ones are refused."""
        from assets.integration_s2_provider import ReadError

        original = self.binding
        self.binding = replace(
            original,
            environment='e' * 1024,
            authority='a' * 1024,
            request_scope='s' * 1024,
        )
        self.source = FixtureSnapshot(self.binding)
        self.assertEqual(self.read().total, 1)
        for coordinate in ('environment', 'authority', 'request_scope'):
            self.binding = replace(original, **{coordinate: 'X' * 1025})
            self.source = FixtureSnapshot(self.binding)
            with self.subTest(coordinate=coordinate), self.assertRaises(ReadError):
                self.read()
            self.assertEqual(self.source.calls, [])
        self.binding = replace(original, shape=replace(original.shape, limit=1))
        for size in (1024, 1025):
            token = 'C' * size
            self.source = FixtureSnapshot(
                self.binding, rows=[{'pk': 1, 'name': 'A'}, {'pk': 2, 'name': 'B'}]
            )
            self.source.transform = lambda page, token=token: replace(
                page, next_cursor=token
            )
            plan = replace(
                self.source.plan(),
                verify=lambda page, token=token: (
                    page.rows == ({'pk': 1, 'name': 'A'},)
                    and page.total == 2
                    and page.next_cursor == token
                ),
            )
            if size == 1024:
                self.assertEqual(
                    self.read(
                        provider_factory=lambda binding, plan=plan: plan
                    ).next_cursor,
                    token,
                )
            else:
                with self.assertRaises(ReadError):
                    self.read(provider_factory=lambda binding, plan=plan: plan)

    def test_exact_scalar_and_container_types_resist_equality_spoofing(self):
        """User/provider equality overloads are not literal context coordinates."""
        from assets.integration_s2_provider import ReadError

        class EqualString(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        for binding in (
            replace(self.binding, actor_pk=True),
            replace(self.binding, actor_pk=0),
            replace(self.binding, actor_pk=-1),
            replace(self.binding, client_ids=[11, 12]),
            replace(self.binding, environment=EqualString('forged')),
            replace(
                self.binding,
                shape=replace(self.binding.shape, domain=EqualString('forged')),
            ),
        ):
            with self.subTest(binding=repr(binding)), self.assertRaises(ReadError):
                self.read(binding_factory=lambda binding=binding: binding)
        for mutation in (
            lambda page: replace(page, generation=EqualString('different')),
            lambda page: replace(page, total=True),
            lambda page: replace(page, position=True),
            lambda page: replace(page, sequence=True),
            lambda page: replace(page, rows=list(page.rows)),
            lambda page: replace(page, next_cursor=EqualString('forged')),
        ):
            self.source.transform = mutation
            with self.subTest(mutation=mutation.__name__), self.assertRaises(ReadError):
                self.read()
        self.assertEqual(self.native_calls, [])

    def test_private_carrier_repr_does_not_include_synthetic_payload(self):
        """Explicit row access remains available, not arbitrary serialization redaction."""
        marker = 'synthetic-sensitive-row'
        self.source = FixtureSnapshot(self.binding, rows=[{'pk': 1, 'name': marker}])
        page = self.read()
        self.assertNotIn(marker, repr(page))
        self.assertNotIn(marker, repr((page, self.source.plan())))
        self.assertEqual(page.rows[0]['name'], marker)
