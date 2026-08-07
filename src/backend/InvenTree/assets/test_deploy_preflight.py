"""Tests for the deploy_preflight audit/grant command.

Pins the contract left by the 2026-08 upstream sync: the AI service token
account keeps every ``*_detail`` field it consumes (upstream #12529 silently
drops them without model view permission), grants are idempotent and never
downgrade, and the duplicate-serial audit finds exactly the rows that would
abort ``stock/0126``.
"""

import json
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from assets.management.commands.deploy_preflight import (
    SERVICE_ACCOUNT_REQUIRED_VIEWS,
    SERVICE_GROUP_NAME,
    TECHNICIAN_VIEW_RULESETS,
    Command,
    grant_ruleset,
)
from InvenTree.unit_test import InvenTreeAPITestCase


def _preflight(*args) -> dict:
    """Run the command with --json and return the parsed report."""
    out = StringIO()
    call_command('deploy_preflight', '--json', *args, stdout=out)
    return json.loads(out.getvalue())


class ServiceTokenDetailFieldContractTest(InvenTreeAPITestCase):
    """The service-account grant keeps AI-consumed detail fields alive.

    Exercises the exact deployed auth path: a token-authenticated request to
    the stock list, with the account granted via the command itself.
    """

    fixtures = [
        'category',
        'part',
        'test_templates',
        'bom',
        'company',
        'location',
        'supplier_part',
        'stock',
        'stock_tests',
    ]

    def setUp(self):
        """Create the service account, grant it via the command, mint a token."""
        super().setUp()

        from users.models import ApiToken

        self.service_user = get_user_model().objects.create_user(
            username='ai-service', password='irrelevant'
        )
        call_command(
            'deploy_preflight',
            '--service-user=ai-service',
            '--grant-service-roles',
            stdout=StringIO(),
        )
        token = ApiToken.objects.create(user=self.service_user, name='svc')
        self.auth = {'HTTP_AUTHORIZATION': f'Token {token.key}'}
        # The session user must not mask the token account
        self.client.logout()

    def _rows(self, response):
        """Return list rows whether or not the endpoint paginated."""
        data = response.json()
        return data['results'] if isinstance(data, dict) else data

    def test_detail_fields_present_for_granted_account(self):
        """The granted account receives every requested detail block."""
        response = self.client.get(
            reverse('api-stock-list'),
            {'location_detail': 'true', 'part_detail': 'true'},
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertTrue(rows)
        self.assertIn('location_detail', rows[0])
        self.assertIn('part_detail', rows[0])

    def test_missing_embedded_role_drops_only_that_detail(self):
        """Losing every StockLocation-covering role blanks location_detail only.

        StockLocation is covered by BOTH the stock_location and build rulesets
        (users/ruleset.py), so the gate only closes when neither grants view -
        which is also why the 8-role contract is resilient to a single revoke.
        """
        group = Group.objects.get(name=SERVICE_GROUP_NAME)
        for name in ('stock_location', 'build'):
            ruleset = group.rule_sets.get(name=name)
            ruleset.can_view = False
            ruleset.save()

        response = self.client.get(
            reverse('api-stock-list'),
            {'location_detail': 'true', 'part_detail': 'true'},
            **self.auth,
        )
        # stock.view is intact, so the endpoint itself still answers
        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertTrue(rows)
        self.assertNotIn('location_detail', rows[0])
        self.assertIn('part_detail', rows[0])

    def test_audit_reports_the_revoked_role(self):
        """The audit names exactly the ruleset that lost view."""
        group = Group.objects.get(name=SERVICE_GROUP_NAME)
        ruleset = group.rule_sets.get(name='stock_location')
        ruleset.can_view = False
        ruleset.save()

        report = _preflight('--service-user=ai-service')
        self.assertFalse(report['service_account']['satisfied'])
        self.assertEqual(report['service_account']['missing_views'], ['stock_location'])


class GrantIdempotencyTest(TestCase):
    """Grants converge and never downgrade."""

    def setUp(self):
        """Create the account the grants target."""
        self.user = get_user_model().objects.create_user(username='svc-user')

    def test_service_grant_is_idempotent(self):
        """Two grant runs converge; the second applies nothing."""
        for _ in range(2):
            call_command(
                'deploy_preflight',
                '--service-user=svc-user',
                '--grant-service-roles',
                stdout=StringIO(),
            )

        group = Group.objects.get(name=SERVICE_GROUP_NAME)
        for name in SERVICE_ACCOUNT_REQUIRED_VIEWS:
            self.assertTrue(group.rule_sets.get(name=name).can_view)
        self.assertTrue(self.user.groups.filter(pk=group.pk).exists())

        # Second run reported no new grants
        report = _preflight('--service-user=svc-user', '--grant-service-roles')
        self.assertEqual(report['grants_applied'], [])
        self.assertTrue(report['service_account']['satisfied'])

    def test_grant_never_downgrades(self):
        """Re-granting view never clears a broader existing flag."""
        group = Group.objects.create(name='pre-granted')
        grant_ruleset(group, 'part', can_view=True, can_add=True)

        grant_ruleset(group, 'part', can_view=True)

        ruleset = group.rule_sets.get(name='part')
        self.assertTrue(ruleset.can_view)
        self.assertTrue(ruleset.can_add)

    def test_technician_grant_covers_flagged_groups(self):
        """The blanket grant clears every flagged group."""
        techs = Group.objects.create(name='techs')
        grant_ruleset(techs, 'work_order', can_view=True)

        report = _preflight()
        self.assertIn(
            {'group': 'techs', 'missing_views': list(TECHNICIAN_VIEW_RULESETS)},
            report['technician_groups'],
        )

        report = _preflight('--grant-technician-views')
        for name in TECHNICIAN_VIEW_RULESETS:
            self.assertTrue(techs.rule_sets.get(name=name).can_view)
        self.assertNotIn(
            'techs', [finding['group'] for finding in report['technician_groups']]
        )


class PreflightReportTest(TestCase):
    """Report shape on a clean database."""

    def test_clean_run(self):
        """A clean database yields an all-empty report."""
        report = _preflight()
        self.assertEqual(report['duplicate_serials'], [])
        self.assertEqual(report['pending_migrations'], [])
        self.assertIsNone(report['service_account'])
        self.assertEqual(report['grants_applied'], [])

    def test_unknown_service_user_is_reported(self):
        """A bad username is reported, not raised."""
        report = _preflight('--service-user=no-such-user')
        self.assertEqual(report['service_account']['error'], 'user not found')


class DuplicateSerialDetectionTest(TransactionTestCase):
    """The audit finds rows that would abort stock/0126.

    The constraint already exists in the test schema, so it is dropped for the
    duration of this test (and restored after the offending rows are removed).
    """

    def test_duplicate_pairs_are_reported(self):
        """Duplicate (part, serial) rows are found and detailed."""
        from part.models import Part
        from stock.models import StockItem

        constraint = next(
            c
            for c in StockItem._meta.constraints
            if c.name == 'stock_item_unique_part_serial'
        )

        with connection.schema_editor() as editor:
            editor.remove_constraint(StockItem, constraint)

        try:
            part = Part.objects.create(
                name='Serialized widget',
                description='A part with duplicated serials',
                trackable=True,
            )
            StockItem.objects.bulk_create([
                StockItem(part=part, serial='DUP-1', quantity=Decimal(1)),
                StockItem(part=part, serial='DUP-1', quantity=Decimal(1)),
                StockItem(part=part, serial='UNIQUE-1', quantity=Decimal(1)),
            ])

            findings = Command().audit_duplicate_serials()

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]['part'], part.pk)
            self.assertEqual(findings[0]['serial'], 'DUP-1')
            self.assertEqual(len(findings[0]['rows']), 2)
        finally:
            # The constraint cannot be rebuilt over duplicate rows
            StockItem.objects.all().delete()
            with connection.schema_editor() as editor:
                editor.add_constraint(StockItem, constraint)
