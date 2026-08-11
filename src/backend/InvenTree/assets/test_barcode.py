"""S32a: machines are scannable through the standard barcode rail."""

from django.urls import reverse

from assets.models import AssetMachine, Client
from InvenTree.unit_test import InvenTreeAPITestCase


class AssetMachineBarcodeTests(InvenTreeAPITestCase):
    """Scan, assign, dedupe and unassign for machine barcodes."""

    roles = ['work_order.view', 'work_order.change']

    @classmethod
    def setUpTestData(cls):
        """Create two machines on one client tenant."""
        super().setUpTestData()
        cls.client_tenant = Client.objects.create(name='Barcode Plant', code='bc-a')
        cls.machine = AssetMachine.objects.create(
            name='Scan Press 1', client=cls.client_tenant
        )
        cls.other = AssetMachine.objects.create(
            name='Scan Press 2', client=cls.client_tenant
        )

    def setUp(self):
        """Ensure plugins are loaded for the barcode rail."""
        super().setUp()
        self.ensurePluginsLoaded()

    def scan(self, barcode, expected_code=200):
        """POST one barcode to the scan endpoint."""
        return self.post(
            reverse('api-barcode-scan'),
            data={'barcode': barcode},
            expected_code=expected_code,
        )

    def test_type_code_is_registered_and_unique(self):
        """The AM short code resolves to AssetMachine in the registry."""
        from plugin.base.barcodes.helper import get_supported_barcode_model_codes_map

        mapping = get_supported_barcode_model_codes_map()
        self.assertIs(mapping.get('AM'), AssetMachine)

    def test_scan_internal_short_code(self):
        """Scanning INV-AM<pk> resolves the machine with URLs."""
        response = self.scan(f'INV-AM{self.machine.pk}')
        self.assertIn('assetmachine', response.data)
        payload = response.data['assetmachine']
        self.assertEqual(payload['pk'], self.machine.pk)
        self.assertEqual(payload['api_url'], f'/api/assets/machines/{self.machine.pk}/')
        self.assertEqual(
            payload['web_url'], f'/web/machines/machine/{self.machine.pk}/'
        )

    def test_scan_missing_machine_is_an_error(self):
        """A short code for a nonexistent machine does not resolve."""
        response = self.scan('INV-AM999999', expected_code=400)
        self.assertIn('error', response.data)

    def test_assign_scan_and_unassign_third_party_barcode(self):
        """A custom tag links, scans back, dedupes, and unlinks."""
        barcode = 'MACHINE-TAG-0001'
        response = self.post(
            reverse('api-barcode-link'),
            data={'barcode': barcode, 'assetmachine': self.machine.pk},
            expected_code=200,
        )
        self.assertIn('success', response.data)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.barcode_data, barcode)
        self.assertTrue(self.machine.barcode_hash)

        scanned = self.scan(barcode)
        self.assertEqual(scanned.data['assetmachine']['pk'], self.machine.pk)

        # The same tag cannot be assigned to a second machine.
        duplicate = self.post(
            reverse('api-barcode-link'),
            data={'barcode': barcode, 'assetmachine': self.other.pk},
            expected_code=400,
        )
        self.assertIn('error', duplicate.data)

        self.post(
            reverse('api-barcode-unlink'),
            data={'assetmachine': self.machine.pk},
            expected_code=200,
        )
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.barcode_data, '')
        self.assertEqual(self.machine.barcode_hash, '')
