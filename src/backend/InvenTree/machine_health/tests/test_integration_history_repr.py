"""Representation controls for unwired bounded-history values."""

from unittest import TestCase

from machine_health.services.integration_history import (
    HistoryPage,
    HistoryResult,
    HistorySample,
)


class HistoryRepresentationTests(TestCase):
    """Ordinary representations should not log observation values."""

    def test_sample_and_container_repr_omit_observation_value(self):
        """Keep value access/equality intact while excluding it from repr."""
        sentinel = 'synthetic-private-observation-3ab9'
        sample = HistorySample('synthetic-id', 1, sentinel, 'good', 'unit')
        page = HistoryPage('synthetic-generation', (sample,), False)
        result = HistoryResult((sample,), ())
        for value in (sample, page, result, [sample]):
            with self.subTest(kind=type(value).__name__):
                self.assertNotIn(sentinel, repr(value))
                self.assertNotIn(sentinel, str(value))
        self.assertEqual(sample.value, sentinel)
        self.assertEqual(
            sample, HistorySample('synthetic-id', 1, sentinel, 'good', 'unit')
        )
        self.assertNotEqual(
            sample, HistorySample('synthetic-id', 1, 'different', 'good', 'unit')
        )
