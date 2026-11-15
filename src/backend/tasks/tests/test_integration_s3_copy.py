"""Pure contracts for independently labelled synthetic S3 copies."""

from integration_state.tests.assertions import assert_literal_bool

import unittest
from dataclasses import FrozenInstanceError
from importlib import import_module
from importlib.util import find_spec


class SourceCopyFixtureTests(unittest.TestCase):
    """Copy fixtures carry claims, never native discovery or source authority."""

    def copy_module(self):
        """Keep initial missing-entry RED distinct from behavioral coverage."""
        name = 'tasks.integration_s3_copy'
        self.assertIsNotNone(
            find_spec(name), 'S3 needs a bounded synthetic copy bridge'
        )
        return import_module(name)

    def test_original_bytes_and_unknown_copy_truth_are_explicit(self):
        """None, empty, exact bytes and indefinite retention are not inferred."""
        module = self.copy_module()
        content = b's3-private-copy-sentinel\x00\xff'
        fixture = module.FixtureSourceCopy(
            copy_key=' copy:007 ',
            copy_kind='backup',
            source_digest='a' * 64,
            content=content,
        )
        self.assertEqual(fixture.copy_key, ' copy:007 ')
        self.assertIs(fixture.content, content)
        self.assertIsNone(fixture.owners)
        self.assertEqual(fixture.attribution, 'unknown')
        self.assertEqual(fixture.retention, 'indefinite')
        assert_literal_bool(self, fixture.synthetic_only, True)
        self.assertNotIn('s3-private-copy-sentinel', repr(fixture))
        with self.assertRaises(FrozenInstanceError):
            fixture.retention = 'reviewable'

    def test_unsupported_fixture_fields_are_rejected_before_use(self):
        """Bounded exact bytes and literal independent claims cannot certify copies."""
        from assets.integration_contracts import ClientSourceIdentity

        module = self.copy_module()
        original = {
            'copy_key': ' copy:007 ',
            'copy_kind': 'backup',
            'source_digest': 'a' * 64,
        }
        for changes in (
            {'copy_key': ' '},
            {'copy_kind': 'native-delete'},
            {'source_digest': 'unknown'},
            {'content': bytearray(b'private-fixture')},
            {'content': 'private-fixture'},
            {'content': b'x' * (2 * 1024 * 1024 + 1)},
            {'owners': [ClientSourceIdentity('s3-test', 7)]},
            {'owners': (ClientSourceIdentity('s3-test', 7),) * 2},
            {'attribution': 'native-proved'},
            {'retention': 'delete-now'},
        ):
            with self.subTest(fields=tuple(changes)), self.assertRaises(ValueError):
                module.FixtureSourceCopy(**dict(original, **changes))

    def test_unknown_empty_bytes_and_literal_copy_keys_remain_distinct(self):
        """An explicit empty fixture is not an unknown copy or a normalized key."""
        from dataclasses import replace

        module = self.copy_module()
        missing = module.FixtureSourceCopy(
            copy_key=' copy:007 ', copy_kind='backup', source_digest='a' * 64
        )
        empty = replace(missing, content=b'')
        self.assertNotEqual(module._copy_key(missing), module._copy_key(empty))
        self.assertNotEqual(
            module._copy_key(empty),
            module._copy_key(replace(empty, copy_key='copy:007')),
        )
        object.__setattr__(empty, 'synthetic_only', False)
        with self.assertRaises(module.SourceCopyError):
            module._copy_key(empty)

    def test_foreign_carriers_are_refused_before_property_access(self):
        """Matching attribute names cannot confer source, owner or request authority."""
        module = self.copy_module()
        reads = []

        class Foreign:
            @property
            def environment(self):
                reads.append('environment')
                return 's3-test'

            @property
            def copy_key(self):
                reads.append('copy_key')
                return 'foreign'

        for check in (
            module._client_key,
            module._source_key,
            module._intent_key,
            module._impact_key,
            module._copy_key,
            module._coordinates,
            module._progress_fields,
        ):
            with self.subTest(check=check.__name__):
                with self.assertRaises(module.SourceCopyError):
                    check(Foreign())
        self.assertEqual(reads, [])

    def test_scalar_aliases_cannot_substitute_for_literal_fixture_claims(self):
        """Even an always-equal string subclass stays outside exact scalar admission."""
        module = self.copy_module()

        class Alias(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        original = {
            'copy_key': ' copy:007 ',
            'copy_kind': 'backup',
            'source_digest': 'a' * 64,
        }
        for changes in (
            {'copy_key': Alias('different')},
            {'copy_kind': Alias('native-delete')},
            {'source_digest': Alias('a' * 64)},
            {'attribution': Alias('native-proved')},
            {'retention': Alias('delete-now')},
        ):
            with self.subTest(fields=tuple(changes)), self.assertRaises(ValueError):
                module.FixtureSourceCopy(**dict(original, **changes))
