"""Bounded whole-fixture native/optional HTTP parity beyond a single page.

Native users, roles, scope, grants, source rows and actual URL dispatch are real.
The separately captured fixture snapshot supplies local transport/generation
and field/count-policy doubles, never real-provider or semantic approval.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

from assets.health_models import MachineAnomaly
from assets.location_api import LocationList, LocationMachines
from assets.models import AssetMachine, ClientScopeGrant
from assets.tests.test_integration_s2_native_parity_db import BASE, S2SourceFixture


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_ASSET_READ_ADAPTER=None,
)
class NativePopulationHTTPTests(S2SourceFixture, TestCase):
    """Exact actual-endpoint counts/order/fields and independent current scope."""

    def setUp(self):
        """Use multi-client, mixed-active/placement/archived synthetic sources."""
        self.build_source()

    def walk(self, path, params, *, page_size=23):
        """Follow every actual next URL with finite explicit request/row bounds."""
        response = self.api.get(BASE + path, {**params, 'limit': page_size})
        rows, seen, offsets, total = [], set(), [], None
        for _ in range(64):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            body = response.json()
            if total is None:
                total = body['count']
            self.assertEqual(body['count'], total)
            self.assertLessEqual(len(body['results']), page_size)
            for row in body['results']:
                self.assertNotIn(row['pk'], seen)
                seen.add(row['pk'])
                rows.append(row)
            self.assertLessEqual(len(rows), 512)
            next_url = body['next']
            if next_url is None:
                self.assertEqual(total, len(rows))
                return rows, offsets
            parsed = urlsplit(next_url)
            self.assertEqual(parsed.netloc, 'testserver')
            self.assertEqual(parsed.path, BASE + path)
            offset = int(parse_qs(parsed.query)['offset'][0])
            self.assertNotIn(offset, offsets)
            self.assertEqual(offset, len(rows))
            offsets.append(offset)
            response = self.api.get(parsed.path + '?' + parsed.query)
        self.fail('Fixture traversal exceeded its declared page bound')

    def compare(self, path, params, *, page_size=23):
        """Capture full native truth first, then independently serve fixed bytes."""
        expected, offsets = self.walk(path, params, page_size=page_size)
        adapter, sources = self.simulated_adapter(expected)
        view = LocationMachines if path else LocationList
        actual_native = view._get_native
        with (
            self.settings(AIMMS_ASSET_READ_ADAPTER=adapter),
            patch.object(
                view, '_get_native', autospec=True, side_effect=actual_native
            ) as native,
        ):
            actual, optional_offsets = self.walk(path, params, page_size=page_size)
        self.assertEqual(actual, expected)
        self.assertEqual(optional_offsets, offsets)
        self.assertEqual(native.call_count, 0)
        self.assertEqual(len(sources), len(offsets) + 1)
        self.assertTrue(all(len(source.calls) == 1 for source in sources))
        return actual

    def test_machine_populations_vary_beyond_native_maximum_page(self):
        """No fixture-sized count or facility assumption may truncate the scan."""
        for extra in (0, 17, 117):
            while (
                AssetMachine.objects.filter(name__startswith='Paged ').count() < extra
            ):
                index = AssetMachine.objects.filter(name__startswith='Paged ').count()
                owner = self.owner if index % 2 else self.second_owner
                self.machine(
                    f'Paged {index:03}',
                    self.site if owner == self.owner else self.other_site,
                    client=owner,
                    serial=f'Paged serial {index}',
                    active=index % 4 != 0,
                )
            with self.subTest(extra=extra):
                rows = self.compare('machines/', {})
                self.assertEqual(len(rows), extra + 7)
                self.assertNotIn(self.hidden.pk, {row['pk'] for row in rows})
                self.assertEqual(
                    [(row['name'], row['pk']) for row in rows],
                    sorted((row['name'], row['pk']) for row in rows),
                )
        self.assertGreater(len(rows), 100)

    def test_combined_filters_keep_real_alarm_work_profile_and_null_facts(self):
        """Positive dependent native facts and empty matches survive final paging."""
        AssetMachine.objects.filter(pk=self.direct.pk).update(
            profile={'criticality': 'critical'}
        )
        now = timezone.now()
        MachineAnomaly.objects.create(
            machine=self.direct,
            fingerprint='synthetic-page-alarm',
            title='Synthetic real alarm',
            severity='warning',
            status='open',
            first_observed_at=now,
            last_observed_at=now,
        )
        WorkOrder.objects.create(
            title='Synthetic actual open Work',
            machine=self.direct,
            lifecycle_status='planned',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_HIGH,
        )
        for filters in (
            {'active': 'true', 'active_only': 'true'},
            {'active': 'false', 'location': self.site.pk},
            {'location': self.site.pk, 'include_descendants': 'false'},
            {'location': self.site.pk, 'include_descendants': 'true'},
            {'unassigned': 'true', 'search': 'Needle'},
            {'search': 'Needle serial'},
            {'search': 'Needle maker'},
            {'criticality': 'critical', 'has_alarms': 'true'},
            {'active_only': 'true', 'has_alarms': 'false'},
            {'search': 'deliberately-empty-population'},
        ):
            with self.subTest(filters=filters):
                self.compare('machines/', filters, page_size=1)
        rows = self.compare('machines/', {'has_alarms': 'true'}, page_size=1)
        self.assertEqual([row['pk'] for row in rows], [self.direct.pk])
        self.assertEqual(rows[0]['open_work']['count'], 1)
        self.assertEqual(rows[0]['criticality'], 'critical')

    def test_location_population_counts_hierarchy_and_casefold_across_pages(self):
        """More than one maximum native page, tied names and archive descendants."""
        for index in range(113):
            self.node(f'Paged location {index:03}', client=self.second_owner)
        self.node('Tied name', parent=self.room)
        self.node('Tied name', parent=self.other_site, client=self.second_owner)
        all_rows = self.compare('', {}, page_size=19)
        self.assertGreater(len(all_rows), 100)
        self.assertEqual(
            [(row['name'].casefold(), row['pk']) for row in all_rows],
            sorted((row['name'].casefold(), row['pk']) for row in all_rows),
        )
        for filters in (
            {'parent': 'root', 'active_only': 'true'},
            {'client': self.second_owner.pk},
            {'search': '  PAGED  ', 'include_counts': 'true'},
            {'parent': self.site.pk, 'include_counts': 'true', 'active': 'true'},
            {'include_counts': 'true', 'active_only': 'true', 'active': 'false'},
        ):
            with self.subTest(filters=filters):
                self.compare('', filters, page_size=19)

    def test_narrowed_scope_and_nonzero_offset_revoke_without_fallback(self):
        """Retaining both grants never widens native scope or a late response."""
        for index in range(107):
            self.machine(f'Late page {index:03}', self.site)
        scope = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.owner.pk)
        }
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: scope):
            oracle, _ = self.walk('machines/', {})
            self.assertNotIn(self.other.pk, {row['pk'] for row in oracle})
            self.assertTrue(
                ClientScopeGrant.objects.filter(
                    user=self.reader, client=self.second_owner
                ).exists()
            )

            def revoke(page):
                ClientScopeGrant.objects.filter(
                    user=self.reader, client=self.owner
                ).delete()
                return page

            adapter, sources = self.simulated_adapter(oracle, transform=revoke)
            native_read = LocationMachines._get_native
            with (
                self.settings(AIMMS_ASSET_READ_ADAPTER=adapter),
                patch.object(
                    LocationMachines,
                    '_get_native',
                    autospec=True,
                    side_effect=native_read,
                ) as native,
            ):
                response = self.api.get(BASE + 'machines/', {'limit': 7, 'offset': 100})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(set(response.data), {'code', 'detail'})
        self.assertEqual(response.data['code'], 'denied')
        self.assertEqual(native.call_count, 0)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].calls[0][0], 100)
