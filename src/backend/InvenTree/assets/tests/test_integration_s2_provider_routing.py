"""Pure controls for native-first S2 routing; no ORM or external transport."""

import json
import unittest
from dataclasses import replace


def fixture_binding(**changes):
    """Build literal fake authority; this does not authenticate a real user."""
    from assets.integration_s2_provider import ReadBinding, ReadShape

    shape = ReadShape(
        domain='machines',
        filters=(('active', 'true'),),
        ordering='test-codepoint-name-pk.v1',
        fields=('name', 'pk'),
        limit=2,
        offset=0,
    )
    values = {
        'shape': shape,
        'environment': 'synthetic',
        'actor_pk': 1,
        'client_ids': (11, 12),
        'authority': 'synthetic-authority-v1',
        'request_scope': 'synthetic-request-v1',
    }
    values.update(changes)
    return ReadBinding(**values)


class FixtureSnapshot:
    """Separate immutable fixture oracle, fake transport and proof checker."""

    def __init__(self, binding, rows=None):
        """Seed expected source bytes before constructing any response."""
        self.binding = binding
        self.generation = 'fixture-v1'
        self.oracle = json.dumps(
            rows if rows is not None else [{'pk': 1, 'name': 'A'}], sort_keys=True
        )
        self.calls = []
        self.transform = lambda page: page

    def fetch(self, *, binding, generation, cursor, position, sequence):
        """Serve a bounded slice; tests can corrupt transport independently."""
        from assets.integration_s2_provider import ProviderPage

        self.calls.append((position, sequence, cursor, generation))
        source = json.loads(self.oracle)
        rows = tuple(source[position : position + binding.shape.limit])
        end = position + len(rows)
        page = ProviderPage(
            binding=binding,
            generation=generation,
            position=position,
            sequence=sequence,
            rows=rows,
            total=len(source),
            next_cursor=f'issued:{end}' if end < len(source) else None,
            coverage_complete=True,
        )
        return self.transform(page)

    def verify(self, page):
        """Compare a page with fixture truth, not echoed provider metadata."""
        source = json.loads(self.oracle)
        expected = tuple(
            source[page.position : page.position + self.binding.shape.limit]
        )
        end = page.position + len(expected)
        return (
            page.rows == expected
            and page.total == len(source)
            and page.next_cursor == (f'issued:{end}' if end < len(source) else None)
        )

    def plan(self, binding=None):
        """A synthetic plan is not a real-provider capability certification."""
        from assets.integration_s2_provider import ReadCapability, ReadPlan

        return ReadPlan(
            capability=ReadCapability(
                binding=binding or self.binding,
                generation=self.generation,
                offsets=True,
            ),
            fetch=self.fetch,
            verify=self.verify,
            row_key=lambda row: (row['name'], row['pk']),
        )


class NativeFirstRoutingTests(unittest.TestCase):
    """Default reads do not need an integration actor, provider or credential."""

    def test_default_executes_native_without_resolving_optional_context(self):
        """The disabled seam returns the exact native result once."""
        from assets.integration_s2_provider import route_native_read

        calls = []
        expected = object()

        def native():
            calls.append('native')
            return expected

        def binding():
            self.fail('Default-native routing must not resolve integration authority')

        self.assertIs(
            route_native_read(native=native, binding_factory=binding), expected
        )
        self.assertEqual(calls, ['native'])

    def test_unsupported_request_is_selected_native_before_data_execution(self):
        """A side-effect-free capability refusal precedes native execution."""
        from assets.integration_s2_provider import route_native_read

        calls = []
        context = fixture_binding()

        def binding():
            calls.append('binding')
            return context

        def factory(request):
            self.assertIs(request, context)
            calls.append('capability-unsupported')
            return None

        def native():
            calls.append('native')
            return 'native-page'

        self.assertEqual(
            route_native_read(
                native=native, binding_factory=binding, provider_factory=factory
            ),
            'native-page',
        )
        self.assertEqual(calls, ['binding', 'capability-unsupported', 'native'])

    def test_supported_fake_executes_once_and_returns_useful_bound_rows(self):
        """A supported request does not silently remain native-only."""
        from assets.integration_s2_provider import route_native_read

        binding = fixture_binding()
        source = FixtureSnapshot(binding)
        calls = []
        page = route_native_read(
            native=lambda: calls.append('native'),
            binding_factory=lambda: binding,
            provider_factory=source.plan,
        )
        self.assertEqual(page.rows, [{'pk': 1, 'name': 'A'}])
        self.assertEqual(page.total, 1)
        self.assertTrue(page.snapshot_complete)
        self.assertEqual(page.generation, 'fixture-v1')
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(calls, [])

    def test_capability_mismatch_selects_native_without_provider_fetch(self):
        """Every bound coordinate and the full request shape participate."""
        from assets.integration_s2_provider import route_native_read

        binding = fixture_binding()
        changed = [
            replace(binding, environment='different-source'),
            replace(binding, actor_pk=2),
            replace(binding, client_ids=(11,)),
            replace(binding, authority='changed-authority'),
            replace(binding, request_scope='narrower-token'),
        ]
        for field, value in (
            ('domain', 'locations'),
            ('filters', (('active', 'false'),)),
            ('ordering', 'another-collation.v1'),
            ('fields', ('name', 'pk', 'profile')),
            ('limit', 1),
            ('offset', 1),
        ):
            changed.append(
                replace(binding, shape=replace(binding.shape, **{field: value}))
            )
        for other in changed:
            source = FixtureSnapshot(binding)
            expected = object()
            with self.subTest(coordinate=repr(other)):
                self.assertIs(
                    route_native_read(
                        native=lambda expected=expected: expected,
                        binding_factory=lambda: binding,
                        provider_factory=lambda request, source=source, other=other: (
                            source.plan(other)
                        ),
                    ),
                    expected,
                )
                self.assertEqual(source.calls, [])

    def test_unqualified_generation_and_offset_support_select_native(self):
        """A cursor-only capability cannot emulate random offsets by scanning."""
        from assets.integration_s2_provider import route_native_read

        binding = fixture_binding()
        binding = replace(binding, shape=replace(binding.shape, offset=1))
        source = FixtureSnapshot(binding)
        for capability in (
            replace(source.plan().capability, generation=''),
            replace(source.plan().capability, offsets=False),
        ):
            expected = object()
            plan = replace(source.plan(), capability=capability)
            with self.subTest(capability=repr(capability)):
                self.assertIs(
                    route_native_read(
                        native=lambda expected=expected: expected,
                        binding_factory=lambda: binding,
                        provider_factory=lambda request, plan=plan: plan,
                    ),
                    expected,
                )
        self.assertEqual(source.calls, [])
