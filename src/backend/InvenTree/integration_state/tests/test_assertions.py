"""Unknown, numeric and truthy/falsey aliases cannot satisfy exact booleans."""

from django.test import SimpleTestCase

from integration_state.tests.assertions import assert_literal_bool


class LiteralBooleanAssertionTests(SimpleTestCase):
    """Keep the source tests' strict boolean identity through all style hooks."""

    def test_singletons_pass_and_unknown_or_numeric_aliases_fail(self):
        """None/0/1 and other truthy values never masquerade as approved booleans."""
        assert_literal_bool(self, True, True)
        assert_literal_bool(self, False, False)
        for actual, expected in [(None, False), (0, False), (1, True), ('yes', True), ([], False), (False, True), (True, False)]:
            with self.subTest(actual=actual, expected=expected), self.assertRaises(AssertionError):
                assert_literal_bool(self, actual, expected)

    def test_the_expected_contract_itself_requires_a_boolean(self):
        """A malformed expected value cannot turn identity into a passing oracle."""
        for expected in [None, 0, 1, 'false']:
            with self.subTest(expected=expected), self.assertRaises(TypeError):
                assert_literal_bool(self, expected, expected)

    def test_the_observed_expression_is_evaluated_once(self):
        """The strict assertion never re-reads a source expression or its property."""
        observed = iter([True, False])
        assert_literal_bool(self, next(observed), True)
        assert_literal_bool(self, next(observed), False)
        with self.assertRaises(StopIteration):
            next(observed)
