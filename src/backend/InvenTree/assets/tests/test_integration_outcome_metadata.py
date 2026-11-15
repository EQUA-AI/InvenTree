"""Direct observations cannot impersonate pages, revisions or serving proof."""

from copy import deepcopy
from unittest import TestCase

from tasks.integration_s3_reader import S3ReadOutcome

from assets.integration_contracts import SourceReadOutcome


class OutcomeMetadataTests(TestCase):
    """Check independent contract versions and non-completeness guarantees."""

    def test_revalidated_outcomes_reject_claimed_or_coerced_completeness(self):
        """Mutation/reconstruction of an envelope must not upgrade its proof."""
        for original in (
            SourceReadOutcome('unavailable', None),
            S3ReadOutcome('unavailable', None),
        ):
            for name in ('population_complete', 'revision_complete'):
                for value in (True, 1, 0, None, 'false'):
                    altered = deepcopy(original)
                    object.__setattr__(altered, name, value)
                    with (
                        self.subTest(kind=type(original), field=name, value=value),
                        self.assertRaises(ValueError),
                    ):
                        type(altered).__post_init__(altered)

    def test_revalidated_outcomes_reject_an_unknown_or_coercing_version(self):
        """Even an unavailable envelope has a strict, independent schema."""

        class EqualVersion(str):
            def __eq__(self, other):
                return True

            __hash__ = None

        for original in (
            SourceReadOutcome('unavailable', None),
            S3ReadOutcome('unavailable', None),
        ):
            for version in (
                'aimms.page.v1',
                None,
                EqualVersion(original.schema_version),
            ):
                altered = deepcopy(original)
                object.__setattr__(altered, 'schema_version', version)
                with (
                    self.subTest(kind=type(original), version=version),
                    self.assertRaises(ValueError),
                ):
                    type(altered).__post_init__(altered)
