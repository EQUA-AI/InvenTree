"""Work surfaces preserve instants across browser time zones."""

from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from rest_framework import serializers

from tasks.serializers import (
    WorkOrderBoardSerializer,
    WorkOrderOverviewSerializer,
    WorkOrderTableSerializer,
)


@override_settings(USE_TZ=True, REST_FRAMEWORK={'DATETIME_FORMAT': '%Y-%m-%d %H:%M'})
class WorkOrderTimestampSerializationTests(SimpleTestCase):
    """The board's datetime contract is inherited by table and overview."""

    instant = datetime(2026, 10, 28, 7, 43, 21, 861857, tzinfo=dt_timezone.utc)
    serializer_types = (
        WorkOrderBoardSerializer,
        WorkOrderTableSerializer,
        WorkOrderOverviewSerializer,
    )
    datetime_fields = (
        'created_at',
        'updated_at',
        'scheduled_start',
        'scheduled_end',
        'actual_started_at',
        'actual_completed_at',
    )

    def test_utc_and_riyadh_retain_the_same_instant_and_precision(self):
        """A 07:43 UTC completion must render as 10:43 at UTC+03:00."""
        for zone, expected in (
            ('UTC', '2026-10-28T07:43:21.861857Z'),
            ('Asia/Riyadh', '2026-10-28T10:43:21.861857+03:00'),
        ):
            with timezone.override(ZoneInfo(zone)):
                for serializer_type in self.serializer_types:
                    fields = serializer_type().fields
                    for name in self.datetime_fields:
                        with self.subTest(
                            zone=zone, serializer=serializer_type, field=name
                        ):
                            rendered = fields[name].to_representation(self.instant)
                            self.assertEqual(rendered, expected)
                            self.assertEqual(
                                datetime.fromisoformat(rendered), self.instant
                            )

    def test_field_read_write_and_date_contracts_are_preserved(self):
        """Execution timestamps remain read-only; schedule fields remain writable."""
        for serializer_type in self.serializer_types:
            fields = serializer_type().fields
            with self.subTest(serializer=serializer_type):
                self.assertIsInstance(fields['due_date'], serializers.DateField)
                self.assertNotIsInstance(fields['due_date'], serializers.DateTimeField)
                for name in (
                    'created_at',
                    'updated_at',
                    'actual_started_at',
                    'actual_completed_at',
                ):
                    self.assertTrue(fields[name].read_only)
                for name in ('scheduled_start', 'scheduled_end'):
                    self.assertEqual(
                        fields[name].read_only,
                        serializer_type is WorkOrderOverviewSerializer,
                    )
                    self.assertTrue(fields[name].allow_null)
        with timezone.override(ZoneInfo('UTC')):
            field = WorkOrderBoardSerializer().fields['scheduled_start']
            self.assertEqual(
                field.run_validation('2026-10-28T10:43:21.861857+03:00'), self.instant
            )
