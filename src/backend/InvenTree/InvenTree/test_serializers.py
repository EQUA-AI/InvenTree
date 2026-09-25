"""Low level tests for serializers."""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings
from django.urls import path, reverse

from rest_framework.serializers import SerializerMethodField

import InvenTree.serializers
from InvenTree.mixins import ListCreateAPI, OutputOptionsMixin
from InvenTree.serializers import InvenTreeIsoDateTimeField, OptionalField
from InvenTree.unit_test import InvenTreeAPITestCase
from InvenTree.urls import backendpatterns
from part.models import Part


class SampleSerializer(
    InvenTree.serializers.FilterableSerializerMixin,
    InvenTree.serializers.InvenTreeModelSerializer,
):
    """Sample serializer for testing FilterableSerializerMixin."""

    class Meta:
        """Meta options."""

        model = User
        fields = [
            'field_a',
            'field_b',
            'field_c',
            'field_d',
            'field_e',
            'field_f',
            'id',
        ]

    field_a = SerializerMethodField(method_name='sample')
    field_b = OptionalField(
        serializer_class=SerializerMethodField,
        serializer_kwargs={'method_name': 'sample'},
    )
    field_c = OptionalField(
        serializer_class=SerializerMethodField,
        serializer_kwargs={'method_name': 'sample'},
        default_include=True,
        filter_name='crazy_name',
    )
    field_d = OptionalField(
        serializer_class=SerializerMethodField,
        serializer_kwargs={'method_name': 'sample'},
        default_include=True,
        filter_name='crazy_name',
    )
    field_e = OptionalField(
        serializer_class=SerializerMethodField,
        serializer_kwargs={'method_name': 'sample'},
        filter_name='field_e',
        filter_by_query=False,
    )

    # Field which embeds a model the requesting user may not have permission to view
    field_f = OptionalField(
        serializer_class=SerializerMethodField,
        serializer_kwargs={'method_name': 'sample'},
        default_include=True,
        filter_name='field_f',
        model=Part,
    )

    def sample(self, obj):
        """Sample method field."""
        return 'sample123'


class SampleList(OutputOptionsMixin, ListCreateAPI):
    """List endpoint sample."""

    serializer_class = SampleSerializer
    queryset = User.objects.all()
    permission_classes = []


urlpatterns = [
    path('', SampleList.as_view(), name='sample-list'),
    path('admin/', admin.site.urls, name='inventree-admin'),
]
urlpatterns += backendpatterns


class FilteredSerializers(InvenTreeAPITestCase):
    """Tests for functionality of FilteredSerializerMixin / adjacent functions."""

    def test_basic_setup(self):
        """Test simple sample setup."""
        with self.settings(
            ROOT_URLCONF=__name__,
            CSRF_TRUSTED_ORIGINS=['http://testserver'],
            SITE_URL='http://testserver',
        ):
            url = reverse('sample-list', urlconf=__name__)

            # Default request (no filters)
            response = self.client.get(url)
            self.assertContains(response, 'field_a')
            self.assertNotContains(response, 'field_b')
            self.assertContains(response, 'field_c')
            self.assertContains(response, 'field_d')

            # Request with filter for field_b
            response = self.client.get(url, {'field_b': True})
            self.assertContains(response, 'field_a')
            self.assertContains(response, 'field_b')
            self.assertContains(response, 'field_c')
            self.assertContains(response, 'field_d')

            self.assertEqual(response.data[0]['field_b'], 'sample123')

            # Disable field_c using custom filter name
            response = self.client.get(url, {'crazy_name': 'false'})
            self.assertContains(response, 'field_a')
            self.assertNotContains(response, 'field_b')
            self.assertNotContains(response, 'field_c')
            self.assertNotContains(response, 'field_d')

            # Query parameters being turned off means it should not be enable-able
            response = self.client.get(url, {'field_e': True})
            self.assertContains(response, 'field_a')
            self.assertNotContains(response, 'field_b')
            self.assertContains(response, 'field_c')
            self.assertContains(response, 'field_d')
            self.assertNotContains(response, 'field_e')

    def test_permission_gating(self):
        """An OptionalField which embeds a model should respect the model's permissions.

        'field_f' defaults to included, but declares 'model=Part' - it should only
        appear in the response if the requesting user actually has 'part.view'.
        """
        with self.settings(
            ROOT_URLCONF=__name__,
            CSRF_TRUSTED_ORIGINS=['http://testserver'],
            SITE_URL='http://testserver',
        ):
            url = reverse('sample-list', urlconf=__name__)

            # No 'part' role assigned - field should be hidden despite default_include=True
            response = self.client.get(url)
            self.assertContains(response, 'field_a')
            self.assertNotContains(response, 'field_f')

            # Assign the 'part.view' role - field should now appear
            self.assignRole('part.view')
            response = self.client.get(url)
            self.assertContains(response, 'field_f')
            self.assertEqual(response.data[0]['field_f'], 'sample123')


class IsoDateTimeFieldTests(SimpleTestCase):
    """Timestamps a client can place on a clock without guessing."""

    #: The project disables time zones under test (``USE_TZ = not TESTING``), and
    #: DRF strips the offset from an aware value when they are off. Production
    #: runs with them on, so that is the setting this contract is stated under.
    AWARE = datetime(2026, 9, 25, 21, 31, 5, tzinfo=dt_timezone.utc)

    @override_settings(USE_TZ=True)
    def test_an_instant_is_rendered_with_its_offset(self):
        """Without one, every browser outside UTC reads it as local time."""
        rendered = InvenTreeIsoDateTimeField().to_representation(self.AWARE)

        self.assertEqual(rendered, '2026-09-25T21:31:05Z')

    @override_settings(USE_TZ=True)
    def test_an_explicit_format_is_left_alone(self):
        """ISO-8601 is the default, not an override of the caller's choice."""
        field = InvenTreeIsoDateTimeField(format='%Y-%m-%d')

        self.assertEqual(field.to_representation(self.AWARE), '2026-09-25')
