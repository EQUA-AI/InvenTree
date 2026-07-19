"""Unit tests for the RPF schema and normalization layer.

Covers canonical JSON serialization, domain-separated hashing, the closed
operator/kind legality matrix, identifier and physical value normalization,
deterministic value comparison, and policy definition validation.
"""

import copy
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from part.verification.compatibility import compare_values
from part.verification.normalization import (
    NormalizationError,
    canonical_boolean,
    canonical_decimal,
    canonical_range,
    normalize_identifier,
)
from part.verification.policy import PolicyError, validate_definition
from part.verification.schema import (
    OPERATORS,
    BlockerCodes,
    CanonicalizationError,
    canonical_json,
    hash_canonical,
    validate_operator,
)


class CanonicalJsonTests(SimpleTestCase):
    """Canonical JSON serialization rules (spec section 8.4)."""

    def test_sorted_keys(self):
        """Object keys are emitted in sorted order regardless of insertion."""
        self.assertEqual(canonical_json({'b': 1, 'a': 2}), '{"a":2,"b":1}')

    def test_insertion_order_irrelevant(self):
        """Two dicts with different insertion orders serialize identically."""
        first = {'alpha': 1, 'beta': 2, 'gamma': 3}
        second = {'gamma': 3, 'alpha': 1, 'beta': 2}
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_nfc_normalization_of_values(self):
        """Composed and decomposed Unicode strings canonicalize identically."""
        composed = 'Café'
        decomposed = 'Café'
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(canonical_json(composed), canonical_json(decomposed))
        self.assertEqual(canonical_json(decomposed), '"Café"')

    def test_nfc_normalization_of_keys(self):
        """Dict keys are NFC-normalized before serialization."""
        self.assertEqual(canonical_json({'é': 1}), canonical_json({'é': 1}))

    def test_decimal_rendered_as_string(self):
        """Decimal values render as exact strings, preserving precision."""
        self.assertEqual(canonical_json(Decimal('1.50')), '"1.50"')
        self.assertEqual(canonical_json({'v': Decimal('230')}), '{"v":"230"}')

    def test_float_raises(self):
        """Binary floats are prohibited at the top level."""
        with self.assertRaises(CanonicalizationError):
            canonical_json(1.5)

    def test_nested_float_raises(self):
        """Binary floats are prohibited anywhere inside nested structures."""
        with self.assertRaises(CanonicalizationError):
            canonical_json({'a': [1.5]})
        with self.assertRaises(CanonicalizationError):
            canonical_json({'a': {'b': 0.1}})

    def test_nested_structures(self):
        """Nested dicts, lists, and tuples canonicalize recursively."""
        value = {'outer': [{'z': Decimal('1.5'), 'a': ('x', 'y')}], 'n': None}
        self.assertEqual(
            canonical_json(value), '{"n":null,"outer":[{"a":["x","y"],"z":"1.5"}]}'
        )

    def test_set_sorted(self):
        """Sets serialize as sorted lists."""
        self.assertEqual(canonical_json({'k': {3, 1, 2}}), '{"k":[1,2,3]}')
        self.assertEqual(canonical_json({'b', 'a', 'c'}), '["a","b","c"]')

    def test_deterministic_across_calls(self):
        """Repeated serialization of equivalent inputs is byte-identical."""
        value = {'set': {'b', 'a'}, 'num': Decimal('2.5'), 'list': [1, 2]}
        rebuilt = {'list': [1, 2], 'num': Decimal('2.5'), 'set': {'a', 'b'}}
        self.assertEqual(canonical_json(value), canonical_json(value))
        self.assertEqual(canonical_json(value), canonical_json(rebuilt))

    def test_non_string_key_raises(self):
        """Non-string object keys cannot be canonicalized."""
        with self.assertRaises(CanonicalizationError):
            canonical_json({1: 'a'})

    def test_unsupported_type_raises(self):
        """Arbitrary objects cannot be canonicalized."""
        with self.assertRaises(CanonicalizationError):
            canonical_json(object())


class HashCanonicalTests(SimpleTestCase):
    """Domain-separated canonical hashing."""

    def test_distinct_domains_distinct_hashes(self):
        """Identical payloads hash differently under distinct domains."""
        payload = {'key': 'value', 'n': 1}
        self.assertNotEqual(
            hash_canonical('rpf.requirements', payload),
            hash_canonical('rpf.evidence', payload),
        )

    def test_prefix_and_length(self):
        """Hashes carry the sha256: prefix and are 71 characters total."""
        digest = hash_canonical('rpf.decision', {'a': 1})
        self.assertTrue(digest.startswith('sha256:'))
        self.assertEqual(len(digest), 71)
        hex_part = digest[len('sha256:') :]
        self.assertTrue(all(c in '0123456789abcdef' for c in hex_part))

    def test_stable_across_calls(self):
        """The same domain and payload always produce the same hash."""
        payload = {'b': [1, 2], 'a': Decimal('3.5')}
        reordered = {'a': Decimal('3.5'), 'b': [1, 2]}
        self.assertEqual(
            hash_canonical('rpf.evaluation', payload),
            hash_canonical('rpf.evaluation', reordered),
        )

    def test_distinct_payloads_distinct_hashes(self):
        """Different payloads under the same domain hash differently."""
        self.assertNotEqual(
            hash_canonical('rpf.source', {'a': 1}),
            hash_canonical('rpf.source', {'a': 2}),
        )


class ValidateOperatorTests(SimpleTestCase):
    """Closed operator/value-kind legality matrix."""

    # (operator, legal kind, illegal kind)
    MATRIX = [
        ('eq', 'text', 'set'),
        ('eq', 'decimal', 'range'),
        ('in', 'set', 'text'),
        ('contains', 'set', 'decimal'),
        ('range_contains', 'range', 'decimal'),
        ('range_within', 'range', 'set'),
        ('gte', 'decimal', 'range'),
        ('lte', 'decimal', 'text'),
        ('present', 'certification', 'decimal'),
        ('present', 'boolean', 'range'),
        ('compatible_revision', 'revision', 'text'),
    ]

    def test_legality_matrix(self):
        """Each operator accepts a legal kind and rejects an illegal one."""
        for operator, legal, illegal in self.MATRIX:
            with self.subTest(operator=operator):
                self.assertTrue(validate_operator(legal, operator))
                self.assertFalse(validate_operator(illegal, operator))

    def test_every_operator_covered(self):
        """The matrix exercises every operator in the closed vocabulary."""
        self.assertEqual({row[0] for row in self.MATRIX}, set(OPERATORS.keys()))

    def test_unknown_operator_illegal(self):
        """Unknown operators are never legal for any kind."""
        self.assertFalse(validate_operator('text', 'matches'))


class NormalizeIdentifierTests(SimpleTestCase):
    """Namespace-aware identifier normalization (spec section 9.2)."""

    def test_trims_and_preserves_raw(self):
        """Surrounding whitespace is trimmed; the raw value is preserved."""
        result = normalize_identifier('ipn', '  ABC-1 ')
        self.assertEqual(result['raw'], '  ABC-1 ')
        self.assertEqual(result['normalized'], 'abc-1')
        self.assertEqual(result['namespace'], 'ipn')

    def test_case_insensitive_namespaces(self):
        """ipn/mpn/sku namespaces compare case-insensitively."""
        for namespace in ('ipn', 'mpn', 'sku'):
            with self.subTest(namespace=namespace):
                self.assertEqual(
                    normalize_identifier(namespace, '6205-2RS')['normalized'],
                    normalize_identifier(namespace, '6205-2rs')['normalized'],
                )

    def test_namespace_name_case_insensitive(self):
        """The namespace name itself is lowered before the case rule lookup."""
        result = normalize_identifier('IPN', 'AbC')
        self.assertEqual(result['namespace'], 'ipn')
        self.assertEqual(result['normalized'], 'abc')

    def test_other_namespaces_preserve_case(self):
        """Namespaces outside the casefold set keep case significant."""
        self.assertEqual(
            normalize_identifier('serial', 'AbC-01')['normalized'], 'AbC-01'
        )

    def test_punctuation_preserved(self):
        """Punctuation characters remain significant."""
        result = normalize_identifier('mpn', 'MTR/230-4.5_A')
        self.assertEqual(result['normalized'], 'mtr/230-4.5_a')

    def test_leading_zeros_preserved(self):
        """Leading zeros never collapse: '007' and '7' stay distinct."""
        self.assertNotEqual(
            normalize_identifier('ipn', '007')['normalized'],
            normalize_identifier('ipn', '7')['normalized'],
        )
        self.assertEqual(normalize_identifier('ipn', '007')['normalized'], '007')

    def test_revision_suffix_preserved(self):
        """A revision-significant suffix is never stripped."""
        with_rev = normalize_identifier('ipn', 'PMP-100-B')['normalized']
        without_rev = normalize_identifier('ipn', 'PMP-100')['normalized']
        self.assertEqual(with_rev, 'pmp-100-b')
        self.assertNotEqual(with_rev, without_rev)

    def test_blank_raises(self):
        """Blank or missing identifier values fail normalization."""
        for value in ('', '   ', None):
            with self.subTest(value=value), self.assertRaises(NormalizationError):
                normalize_identifier('ipn', value)


class CanonicalDecimalTests(TestCase):
    """Physical value canonicalization through the repository unit registry."""

    def test_unit_conversion_equivalence(self):
        """230 V and 230000 mV canonicalize to the same decimal string."""
        volts = canonical_decimal('230', unit='V', target_unit='V')
        millivolts = canonical_decimal('230000', unit='mV', target_unit='V')
        self.assertEqual(volts, millivolts)
        self.assertEqual(volts, '230.000000')

    def test_dimension_mismatch_raises(self):
        """A pressure value can never convert to a voltage target."""
        with self.assertRaises(NormalizationError) as ctx:
            canonical_decimal('5', unit='bar', target_unit='V')
        self.assertEqual(ctx.exception.code, BlockerCodes.UNIT_DIMENSION_MISMATCH)

    def test_unsupported_unit_raises(self):
        """An unknown target unit fails with UNIT_UNSUPPORTED."""
        with self.assertRaises(NormalizationError) as ctx:
            canonical_decimal('5', unit='V', target_unit='thisisnotaunit')
        self.assertEqual(ctx.exception.code, BlockerCodes.UNIT_UNSUPPORTED)

    def test_quantization_at_decimal_places(self):
        """Values are quantized at the declared precision."""
        self.assertEqual(canonical_decimal('1.23456789', decimal_places=4), '1.2346')
        self.assertEqual(canonical_decimal('42', decimal_places=2), '42.00')

    def test_bankers_rounding(self):
        """Quantization uses ROUND_HALF_EVEN at the boundary digit."""
        self.assertEqual(canonical_decimal('2.5', decimal_places=0), '2')
        self.assertEqual(canonical_decimal('3.5', decimal_places=0), '4')
        self.assertEqual(canonical_decimal('1.2345675', decimal_places=6), '1.234568')
        self.assertEqual(canonical_decimal('1.2345665', decimal_places=6), '1.234566')

    def test_plain_numbers_without_units(self):
        """Values with no target unit are quantized directly."""
        self.assertEqual(canonical_decimal('42'), '42.000000')
        self.assertEqual(canonical_decimal(7), '7.000000')
        self.assertEqual(canonical_decimal(Decimal('1.5')), '1.500000')

    def test_invalid_number_raises(self):
        """Non-numeric text fails normalization."""
        with self.assertRaises(NormalizationError):
            canonical_decimal('not-a-number')

    def test_blank_raises(self):
        """Blank and missing values fail normalization."""
        for value in ('', '   ', None):
            with self.subTest(value=value), self.assertRaises(NormalizationError):
                canonical_decimal(value)


class CanonicalRangeTests(SimpleTestCase):
    """Range canonicalization with explicit bounds."""

    def test_scalar_bounds(self):
        """Present bounds canonicalize like decimals."""
        result = canonical_range({'min': '1', 'max': '5'})
        self.assertEqual(result, {'min': '1.000000', 'max': '5.000000'})

    def test_equal_bounds_valid(self):
        """A degenerate range with equal bounds is valid."""
        result = canonical_range({'min': '5', 'max': '5'})
        self.assertEqual(result, {'min': '5.000000', 'max': '5.000000'})

    def test_inverted_range_raises(self):
        """A range whose minimum exceeds its maximum fails."""
        with self.assertRaises(NormalizationError):
            canonical_range({'min': '5', 'max': '1'})

    def test_open_bounds_stay_none(self):
        """Absent bounds stay explicitly None."""
        self.assertEqual(
            canonical_range({'min': None, 'max': '10'}),
            {'min': None, 'max': '10.000000'},
        )
        self.assertEqual(
            canonical_range({'min': '10'}), {'min': '10.000000', 'max': None}
        )
        self.assertEqual(canonical_range({}), {'min': None, 'max': None})

    def test_non_dict_raises(self):
        """A range value must be an object with min/max bounds."""
        with self.assertRaises(NormalizationError):
            canonical_range('100-200')


class CanonicalBooleanTests(SimpleTestCase):
    """Strict boolean canonicalization."""

    def test_accepted_values(self):
        """JSON booleans and exact true/false strings are accepted."""
        self.assertTrue(canonical_boolean(True))
        self.assertFalse(canonical_boolean(False))
        self.assertTrue(canonical_boolean('true'))
        self.assertFalse(canonical_boolean('false'))
        self.assertTrue(canonical_boolean('  TRUE '))

    def test_rejected_values(self):
        """'yes', numeric, and blank forms never convert implicitly."""
        for value in ('yes', 'no', 1, 0, '', '1', None):
            with self.subTest(value=value), self.assertRaises(NormalizationError):
                canonical_boolean(value)


class CompareValuesTests(SimpleTestCase):
    """Deterministic canonical value comparison per operator."""

    def test_eq_absolute_tolerance(self):
        """Absolute tolerance: exact boundary passes, one quantum outside fails."""
        tolerance = {'kind': 'absolute', 'value': '0.5'}
        self.assertTrue(
            compare_values('eq', 'decimal', '10.000000', '10.500000', tolerance)
        )
        self.assertTrue(
            compare_values('eq', 'decimal', '10.000000', '9.500000', tolerance)
        )
        self.assertFalse(
            compare_values('eq', 'decimal', '10.000000', '10.500001', tolerance)
        )
        self.assertFalse(
            compare_values('eq', 'decimal', '10.000000', '9.499999', tolerance)
        )

    def test_eq_percent_tolerance(self):
        """Percent tolerance: exact boundary passes, one quantum outside fails."""
        tolerance = {'kind': 'percent', 'value': '10'}
        self.assertTrue(
            compare_values('eq', 'decimal', '200.000000', '220.000000', tolerance)
        )
        self.assertTrue(
            compare_values('eq', 'decimal', '200.000000', '180.000000', tolerance)
        )
        self.assertFalse(
            compare_values('eq', 'decimal', '200.000000', '220.000001', tolerance)
        )
        self.assertFalse(
            compare_values('eq', 'decimal', '200.000000', '179.999999', tolerance)
        )

    def test_eq_no_tolerance_exact(self):
        """Without tolerance, decimal equality is exact."""
        self.assertTrue(compare_values('eq', 'decimal', '10.000000', '10.000000', None))
        self.assertFalse(
            compare_values('eq', 'decimal', '10.000000', '10.000001', None)
        )

    def test_gte(self):
        """Gte compares the candidate rating against the required floor."""
        self.assertTrue(
            compare_values('gte', 'decimal', '10.000000', '10.000000', None)
        )
        self.assertTrue(
            compare_values('gte', 'decimal', '10.000000', '10.000001', None)
        )
        self.assertFalse(
            compare_values('gte', 'decimal', '10.000000', '9.999999', None)
        )

    def test_lte(self):
        """Lte compares the candidate rating against the required ceiling."""
        self.assertTrue(
            compare_values('lte', 'decimal', '10.000000', '10.000000', None)
        )
        self.assertTrue(compare_values('lte', 'decimal', '10.000000', '9.999999', None))
        self.assertFalse(
            compare_values('lte', 'decimal', '10.000000', '10.000001', None)
        )

    def test_range_within_scalar_candidate(self):
        """range_within: scalar candidate inside/outside the envelope."""
        envelope = {'min': '100.000000', 'max': '200.000000'}
        self.assertTrue(
            compare_values('range_within', 'range', envelope, '150.000000', None)
        )
        self.assertFalse(
            compare_values('range_within', 'range', envelope, '250.000000', None)
        )
        self.assertFalse(
            compare_values('range_within', 'range', envelope, '99.999999', None)
        )

    def test_range_within_open_bound(self):
        """range_within: an open requirement bound does not constrain."""
        envelope = {'min': None, 'max': '200.000000'}
        self.assertTrue(
            compare_values('range_within', 'range', envelope, '50.000000', None)
        )
        self.assertFalse(
            compare_values('range_within', 'range', envelope, '250.000000', None)
        )

    def test_range_contains(self):
        """range_contains: candidate rating must cover the envelope."""
        envelope = {'min': '110.000000', 'max': '120.000000'}
        covering = {'min': '100.000000', 'max': '230.000000'}
        not_covering = {'min': '115.000000', 'max': '230.000000'}
        self.assertTrue(
            compare_values('range_contains', 'range', envelope, covering, None)
        )
        self.assertFalse(
            compare_values('range_contains', 'range', envelope, not_covering, None)
        )

    def test_in(self):
        """in: candidate must be a member of the required set."""
        self.assertTrue(compare_values('in', 'set', ['a', 'b'], 'a', None))
        self.assertFalse(compare_values('in', 'set', ['a', 'b'], 'c', None))

    def test_contains(self):
        """contains: candidate set must hold every required member."""
        self.assertTrue(
            compare_values('contains', 'set', ['a', 'b'], ['a', 'b', 'c'], None)
        )
        self.assertFalse(compare_values('contains', 'set', ['a', 'b'], ['a'], None))

    def test_compatible_revision_allowed_list(self):
        """compatible_revision: candidate must be in the allowed list."""
        requirement = {'allowed': ['B', 'C']}
        self.assertTrue(
            compare_values('compatible_revision', 'revision', requirement, 'B', None)
        )
        self.assertFalse(
            compare_values('compatible_revision', 'revision', requirement, 'A', None)
        )

    def test_compatible_revision_scalar(self):
        """compatible_revision: scalar requirement compares exactly."""
        self.assertTrue(
            compare_values('compatible_revision', 'revision', 'B', 'B', None)
        )
        self.assertFalse(
            compare_values('compatible_revision', 'revision', 'B', 'A', None)
        )


class PolicyValidateDefinitionTests(SimpleTestCase):
    """Closed-vocabulary policy definition validation."""

    def base_definition(self) -> dict:
        """Return a fresh valid policy definition document."""
        return copy.deepcopy({
            'schema_version': 1,
            'description': 'test policy',
            'requirements': [
                {
                    'key': 'electrical.voltage',
                    'category': 'electrical',
                    'value_kind': 'range',
                    'operator': 'range_within',
                    'unit': 'V',
                    'hard': True,
                    'sources': [
                        {'kind': 'observation'},
                        {'kind': 'parameter', 'template': 'Voltage'},
                    ],
                    'missing_blocker': 'NAMEPLATE_REQUIRED',
                },
                {
                    'key': 'electrical.phase',
                    'value_kind': 'decimal',
                    'operator': 'eq',
                    'hard': True,
                    'sources': [{'kind': 'parameter', 'template': 'Phase'}],
                    'candidate_missing': 'exclude',
                    'conflict_code': 'PHASE_CONFLICT',
                    'tolerance': {'kind': 'absolute', 'value': '0'},
                },
            ],
            'retrieval': {'max_candidates': 50, 'tier_cap': 25},
            'rank_factors': [
                {'id': 'exact_requested_identity', 'max': 25},
                {'id': 'evidence_coverage', 'max': 15},
            ],
            'revalidation': {
                'non_material_paths': ['requested_part.description'],
                'expiry_hours': 24,
            },
        })

    def test_valid_definition_passes(self):
        """A valid document validates silently."""
        self.assertIsNone(validate_definition(self.base_definition()))

    def test_unknown_top_key_raises(self):
        """Unknown top-level keys are rejected."""
        definition = self.base_definition()
        definition['surprise'] = {}
        with self.assertRaises(PolicyError):
            validate_definition(definition)

    def test_unknown_rank_factor_raises(self):
        """Rank factor ids outside the closed set are rejected."""
        definition = self.base_definition()
        definition['rank_factors'] = [{'id': 'vibes', 'max': 10}]
        with self.assertRaises(PolicyError):
            validate_definition(definition)

    def test_duplicate_requirement_key_raises(self):
        """Duplicate requirement keys are rejected."""
        definition = self.base_definition()
        duplicate = copy.deepcopy(definition['requirements'][0])
        definition['requirements'].append(duplicate)
        with self.assertRaises(PolicyError):
            validate_definition(definition)

    def test_illegal_operator_kind_combo_raises(self):
        """An operator that is illegal for the declared kind is rejected."""
        definition = self.base_definition()
        definition['requirements'][1]['operator'] = 'range_within'
        with self.assertRaises(PolicyError):
            validate_definition(definition)

    def test_float_tolerance_value_raises(self):
        """Binary float tolerance values are prohibited in policy content."""
        definition = self.base_definition()
        definition['requirements'][1]['tolerance'] = {'kind': 'absolute', 'value': 0.5}
        with self.assertRaises(PolicyError):
            validate_definition(definition)

    def test_bad_candidate_missing_raises(self):
        """candidate_missing outside exclude/indeterminate is rejected."""
        definition = self.base_definition()
        definition['requirements'][1]['candidate_missing'] = 'wildcard'
        with self.assertRaises(PolicyError):
            validate_definition(definition)
