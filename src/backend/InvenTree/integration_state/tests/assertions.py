"""Exact test contracts that must not collapse unknown values into booleans."""


def assert_literal_bool(test_case, actual, expected):
    """Preserve boolean identity and one evaluation of the observed expression.

    Truthiness assertions accept None/0 as false and 1 as true. These source
    contracts require the literal singleton, including in refusal/readiness
    results. A named expected value keeps unittest modernization from replacing
    this identity check with a weaker truthiness check.
    """
    if type(expected) is not bool:
        raise TypeError('The expected value must be a literal boolean')
    test_case.assertIs(actual, expected)
