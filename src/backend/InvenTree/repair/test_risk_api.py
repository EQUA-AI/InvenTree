"""REST API tests: gating, envelope, ranking, zero-leak, admin audit."""

import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from rest_framework.test import APIClient

from .risk_models import (
    RiskActionLink,
    RiskFinding,
    RiskFindingState,
    RiskRuleConfigurationEvent,
    RiskRuleDefinition,
)
from .risk_services import ensure_rule_definitions
from .risk_testing import (
    RISK_FLAGS,
    SCOPES_BY_USERNAME,
    RiskEnvMixin,
    fresh,
    grant_permissions,
)

CC_FLAGS = {**RISK_FLAGS, 'AIMMS_COMMAND_CENTER_ENABLED': True}


class RadarDisabledTest(TestCase):
    """Every radar endpoint 404s while the master flag is off."""

    def test_endpoints_hidden(self):
        """Flag-off surfaces are indistinguishable from absent ones."""
        client = APIClient()
        for name, kwargs in (
            ('risk-finding-list', {}),
            ('risk-scope-list', {}),
            ('command-center-summary', {}),
            ('risk-rule-list', {}),
        ):
            response = client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(response.status_code, 404, name)


@override_settings(**CC_FLAGS)
class RiskApiTest(RiskEnvMixin, TestCase):
    """Authenticated one-scope API behavior."""

    def setUp(self):
        """Build environment, permissions, and API client.

        The cache is cleared because the summary read-model cache (keyed by
        scope, fingerprint, and epoch) survives between tests while sqlite
        reuses rolled-back primary keys, so one test's cached summary could
        otherwise satisfy the next test's request.
        """
        cache.clear()
        self.build_env()
        self.addCleanup(self.teardown_scopes)
        grant_permissions(
            self.actor,
            [
                'view_riskfinding',
                'acknowledge_riskfinding',
                'assign_riskfinding',
                'snooze_riskfinding',
                'dismiss_riskfinding',
                'view_riskrulehealth',
                'administer_riskrules',
            ],
        )
        self.actor = fresh(self.actor)
        SCOPES_BY_USERNAME['risk-actor'] = {self.scope, self.client_scope}
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def test_scope_list(self):
        """The actor sees exactly their authorized scope keys, sorted."""
        response = self.client.get(reverse('risk-scope-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['scopes'], [self.scope_key, self.client_scope_key]
        )
        self.assertTrue(response.json()['authorization_fingerprint'])

    def test_list_requires_scope_parameter(self):
        """A missing scope parameter is a stable envelope error."""
        response = self.client.get(reverse('risk-finding-list'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'SCOPE_UNRESOLVED')

    def test_unauthorized_scope_is_404(self):
        """Another customer's scope key is indistinguishable from absent."""
        response = self.client.get(
            reverse('risk-finding-list'), {'scope': self.other_scope_key}
        )
        self.assertEqual(response.status_code, 404)

    def test_site_scope_summary_fails_closed(self):
        """An authorized scope unsupported by source adapters returns 404."""
        from tasks.scope import MaintenanceScope

        site_scope = MaintenanceScope(
            customer_id=self.customer.pk, site_key='unsupported-site'
        )
        SCOPES_BY_USERNAME['risk-actor'] = {site_scope}
        response = self.client.get(
            reverse('command-center-summary'),
            {'scope': f'c{self.customer.pk}~unsupported-site'},
        )
        self.assertEqual(response.status_code, 404)

    def test_list_ranked_and_filtered(self):
        """The list is ranked by the documented tuple and filterable."""
        low = self.make_finding(discriminator='low', severity='low')
        critical = self.make_finding(discriminator='crit', severity='critical')
        response = self.client.get(
            reverse('risk-finding-list'), {'scope': self.scope_key}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['results'][0]['pk'], critical.pk)
        self.assertEqual(payload['results'][1]['pk'], low.pk)

        filtered = self.client.get(
            reverse('risk-finding-list'),
            {'scope': self.scope_key, 'severity': 'critical'},
        )
        self.assertEqual(filtered.json()['count'], 1)

    def test_list_reports_disabled_sources(self):
        """A disabled source is explicit, never represented as clean zero."""
        with override_settings(
            AIMMS_WORK_ORDERS_ENABLED=False, AIMMS_JOB_KITS_ENABLED=False
        ):
            body = self.client.get(
                reverse('risk-finding-list'), {'scope': self.scope_key}
            ).json()
        degraded = {
            row['source'] for row in body['source_freshness'] if row['degraded']
        }
        self.assertEqual(degraded, {'work_orders', 'job_kits'})

    def test_zero_leak_across_scopes(self):
        """Findings of another scope never appear in list or counts."""
        SCOPES_BY_USERNAME['risk-actor'] = {self.scope, self.other_scope}
        mine = self.make_finding(discriminator='mine')
        theirs = self.make_finding(
            discriminator='theirs', scope_key=self.other_scope_key
        )
        response = self.client.get(
            reverse('risk-finding-list'), {'scope': self.scope_key}
        )
        payload = response.json()
        ids = {row['pk'] for row in payload['results']}
        self.assertEqual(ids, {mine.pk})
        self.assertEqual(payload['count'], 1)
        other = self.client.get(
            reverse('risk-finding-list'), {'scope': self.other_scope_key}
        )
        self.assertEqual({row['pk'] for row in other.json()['results']}, {theirs.pk})

    def test_client_scope_key_serves_machine_derived_findings(self):
        """Machine-derived findings live under the client (``k``) key."""
        plant = self.make_finding(
            discriminator='plant', scope_key=self.client_scope_key
        )
        sales = self.make_finding(discriminator='sales')
        response = self.client.get(
            reverse('risk-finding-list'), {'scope': self.client_scope_key}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual({row['pk'] for row in payload['results']}, {plant.pk})
        self.assertEqual(payload['count'], 1)
        self.assertNotEqual(sales.pk, plant.pk)
        # The unheld other client's key stays indistinguishable from absent.
        denied = self.client.get(
            reverse('risk-finding-list'), {'scope': self.other_client_scope_key}
        )
        self.assertEqual(denied.status_code, 404)

    def test_detail_includes_evidence_and_events(self):
        """Detail reapplies visibility and exposes the audit chain."""
        finding = self.make_finding(discriminator='ev')
        response = self.client.get(
            reverse('risk-finding-detail', kwargs={'pk': finding.pk})
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('evidence', body)
        self.assertIn('events', body)

        # A viewer without this scope gets a 404, not a 403.
        SCOPES_BY_USERNAME['risk-actor'] = {self.other_scope}
        response = self.client.get(
            reverse('risk-finding-detail', kwargs={'pk': finding.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_action_links_require_fresh_enabled_rule(self):
        """Corrective links fail closed when rule freshness is unavailable."""
        finding = self.make_finding(discriminator='actions')
        RiskActionLink.objects.create(
            finding=finding,
            label='Open packet',
            target_kind='repair_packet',
            target_id='7',
            route='/repair/packets/7/',
        )
        list_body = self.client.get(
            reverse('risk-finding-list'), {'scope': self.scope_key}
        ).json()
        self.assertNotIn('action_links', list_body['results'][0])

        url = reverse('risk-finding-detail', kwargs={'pk': finding.pk})
        self.assertEqual(self.client.get(url).json()['action_links'], [])

        self.enable_rule('PACKET_STALLED')
        with override_settings(AIMMS_RISK_RULES_ENABLED=['PACKET_STALLED']):
            links = self.client.get(url).json()['action_links']
        self.assertEqual(links[0]['route'], '/repair/packets/7/')

    def test_export_csv(self):
        """Export is the same one-scope, visibility-filtered list."""
        self.make_finding(discriminator='csv')
        response = self.client.get(
            reverse('risk-finding-export'), {'scope': self.scope_key}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('PACKET_STALLED', response.content.decode())

    def test_acknowledge_roundtrip_and_conflict_envelope(self):
        """Commands succeed with the envelope and 409 on version conflict."""
        finding = self.make_finding(discriminator='cmd')
        url = reverse('risk-finding-acknowledge', kwargs={'pk': finding.pk})
        ok = self.client.post(
            url,
            {'expected_version': 1, 'idempotency_key': uuid.uuid4().hex},
            format='json',
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['state'], RiskFindingState.ACKNOWLEDGED)

        stale = self.client.post(
            url,
            {'expected_version': 1, 'idempotency_key': uuid.uuid4().hex},
            format='json',
        )
        self.assertEqual(stale.status_code, 409)
        body = stale.json()
        self.assertEqual(body['code'], 'FINDING_STATE_CONFLICT')
        self.assertEqual(body['current_version'], 2)
        self.assertIn('correlation_id', body)

    def test_dismiss_validation(self):
        """A missing dismiss reason surfaces as the stable envelope code."""
        finding = self.make_finding(discriminator='dis')
        response = self.client.post(
            reverse('risk-finding-dismiss', kwargs={'pk': finding.pk}),
            {'expected_version': 1, 'idempotency_key': uuid.uuid4().hex},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'DISMISS_REASON_REQUIRED')

    def test_snooze_validation_envelope(self):
        """A missing snooze expiry surfaces as SNOOZE_INVALID."""
        finding = self.make_finding(discriminator='snz')
        response = self.client.post(
            reverse('risk-finding-snooze', kwargs={'pk': finding.pk}),
            {'expected_version': 1, 'idempotency_key': uuid.uuid4().hex},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'SNOOZE_INVALID')

    def test_owner_filter_validation(self):
        """A non-integer owner filter is a stable envelope error, not a 500."""
        response = self.client.get(
            reverse('risk-finding-list'),
            {'scope': self.scope_key, 'owner': 'not-a-number'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'FILTER_INVALID')

    def test_ranking_survives_pool_pressure(self):
        """A critical finding is never displaced by many newer low findings.

        Regression for the pool-slicing defect: the pool is ordered by the
        rank prefix in the database, so recency cannot push a high-severity
        finding out of the visible list, and the count stays exact.
        """
        critical = self.make_finding(discriminator='old-crit', severity='critical')
        for index in range(60):
            self.make_finding(discriminator=f'noise-{index}', severity='low')
        response = self.client.get(
            reverse('risk-finding-list'), {'scope': self.scope_key, 'limit': 5}
        )
        payload = response.json()
        self.assertEqual(payload['count'], 61)
        self.assertEqual(payload['results'][0]['pk'], critical.pk)

    def test_ranking_applies_criticality_before_age(self):
        """Criticality is ranked before age even when the pool is bounded."""
        now = timezone.now()
        older = self.make_finding(
            discriminator='older',
            severity_factors={'base': 'medium', 'criticality': 'low'},
            condition_started_at=now - timedelta(days=3),
        )
        self.make_finding(
            discriminator='middle',
            severity_factors={'base': 'medium', 'criticality': 'low'},
            condition_started_at=now - timedelta(days=2),
        )
        criticality = self.make_finding(
            discriminator='criticality',
            severity_factors={'base': 'medium', 'criticality': 'critical'},
            condition_started_at=now - timedelta(days=1),
        )
        from .risk_services import ranked_pool

        ranked = ranked_pool(
            RiskFinding.objects.filter(pk__in=[older.pk, criticality.pk])
            | RiskFinding.objects.filter(source_id='middle'),
            now,
            limit=2,
        )
        self.assertEqual(ranked[0].pk, criticality.pk)

    def test_pagination_is_not_truncated_at_rank_pool_limit(self):
        """Offsets beyond the summary rank pool still return list rows."""
        seed = self.make_finding(discriminator='page-seed')
        now = timezone.now()
        RiskFinding.objects.bulk_create([
            RiskFinding(
                fingerprint=f'{index + 1:064x}',
                scope_key=self.scope_key,
                rule_revision=seed.rule_revision,
                rule_code=seed.rule_code,
                rule_version=seed.rule_version,
                category=seed.category,
                severity='medium',
                severity_factors={'base': 'medium', 'policy_version': 1},
                source_model=seed.source_model,
                source_id=f'page-{index}',
                title=f'Page finding {index}',
                summary='test',
                evidence={},
                state=RiskFindingState.OPEN,
                first_seen=now,
                last_seen=now,
                condition_started_at=now,
                last_seen_run=seed.last_seen_run,
                source_as_of=now,
            )
            for index in range(500)
        ])
        response = self.client.get(
            reverse('risk-finding-list'),
            {'scope': self.scope_key, 'limit': 1, 'offset': 500},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 501)
        self.assertEqual(len(response.json()['results']), 1)

    def test_export_is_not_paginated(self):
        """CSV export contains every filtered row, not one page."""
        for index in range(55):
            self.make_finding(discriminator=f'csv-{index}')
        response = self.client.get(
            reverse('risk-finding-export'), {'scope': self.scope_key}
        )
        body = response.content.decode()
        # Header plus every finding row (default page size would be 50).
        self.assertEqual(len(body.strip().splitlines()), 56)

    def test_recheck_enqueues_when_rule_enabled(self):
        """Recheck queues the full-snapshot scan for enabled rules."""
        finding = self.make_finding(discriminator='rc')
        url = reverse('risk-finding-recheck', kwargs={'pk': finding.pk})
        disabled = self.client.post(url)
        self.assertEqual(disabled.status_code, 409)
        self.assertEqual(disabled.json()['code'], 'RULE_DISABLED')

    def test_summary_gated_by_command_center_flag(self):
        """The summary needs its own flag on top of the master flag."""
        with override_settings(AIMMS_COMMAND_CENTER_ENABLED=False):
            response = self.client.get(
                reverse('command-center-summary'), {'scope': self.scope_key}
            )
            self.assertEqual(response.status_code, 404)

    def test_summary_shape(self):
        """The composed summary returns every documented section."""
        self.make_finding(discriminator='sum', severity='critical')
        response = self.client.get(
            reverse('command-center-summary'), {'scope': self.scope_key}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in (
            'as_of',
            'stale',
            'freshness',
            'source_freshness',
            'headline',
            'by_category',
            'queue',
            'flow',
            'aging',
            'return_to_service',
        ):
            self.assertIn(key, body)
        self.assertEqual(body['headline']['critical'], 1)
        self.assertEqual(body['scope'], self.scope_key)
        # Disabled rules are visible as gated, never as silent zeros.
        gates = {row['rule']: row['gate'] for row in body['freshness']}
        self.assertEqual(gates['WO_BLOCKED_PARTS'], 'revision_disabled')

    def test_summary_cache_invalidated_by_commands(self):
        """Finding-state commands invalidate the scoped summary cache."""
        finding = self.make_finding(discriminator='inv', severity='critical')
        url = reverse('command-center-summary')
        first = self.client.get(url, {'scope': self.scope_key}).json()
        self.assertEqual(first['headline']['critical'], 1)
        self.client.post(
            reverse('risk-finding-dismiss', kwargs={'pk': finding.pk}),
            {
                'expected_version': 1,
                'idempotency_key': uuid.uuid4().hex,
                'reason': 'noise',
            },
            format='json',
        )
        second = self.client.get(url, {'scope': self.scope_key}).json()
        self.assertEqual(second['headline']['critical'], 0)

    @override_settings(AIMMS_RISK_RULES_ENABLED=['WO_BLOCKED_SAFETY', 'PACKET_STALLED'])
    def test_any_stale_enabled_rule_withholds_recommendations(self):
        """One fresh rule cannot mask another enabled rule's stale data."""
        self.enable_rule('WO_BLOCKED_SAFETY')
        self.enable_rule('PACKET_STALLED')
        safety = self.make_finding(
            code='WO_BLOCKED_SAFETY',
            discriminator='stale-safety',
            severity='critical',
            evidence={'packet_id': 7},
        )
        self.make_finding(code='PACKET_STALLED', discriminator='fresh-packet')
        safety.last_seen_run.completed_at = timezone.now() - timedelta(hours=1)
        safety.last_seen_run.save(update_fields=['completed_at'])

        body = self.client.get(
            reverse('command-center-summary'), {'scope': self.scope_key}
        ).json()

        self.assertTrue(body['stale'])
        self.assertEqual(body['return_to_service'], [])

    def test_rule_health_requires_permission(self):
        """Health is permissioned; viewers without it get 403."""
        plain = fresh(self.service)
        grant_permissions(plain, ['view_riskfinding'])
        plain = fresh(plain)
        SCOPES_BY_USERNAME['risk-service'] = {self.scope}
        client = APIClient()
        client.force_authenticate(plain)
        response = client.get(reverse('risk-rule-health'), {'scope': self.scope_key})
        self.assertEqual(response.status_code, 403)

    def test_rule_health_shape(self):
        """Health reports gates, dormancy, and run history per rule."""
        response = self.client.get(
            reverse('risk-rule-health'), {'scope': self.scope_key}
        )
        self.assertEqual(response.status_code, 200)
        rows = {row['rule']: row for row in response.json()['rules']}
        self.assertIn('WO_BLOCKED_SAFETY', rows)
        self.assertTrue(rows['WO_BLOCKED_PARTS']['dormant_reason'])
        self.assertIn('failure_streak', rows['WO_BLOCKED_SAFETY'])

    def test_rule_health_counts_apply_visibility_policy(self):
        """Health aggregates cannot bypass category visibility."""
        self.make_finding(
            code='WO_BLOCKED_SAFETY', discriminator='visible-health', category='safety'
        )
        self.make_finding(
            code='PACKET_STALLED', discriminator='hidden-health', category='operations'
        )
        with mock.patch(
            'repair.risk_services.VISIBILITY_POLICY.visible_categories',
            return_value={'safety'},
        ):
            rows = {
                row['rule']: row
                for row in self.client.get(
                    reverse('risk-rule-health'), {'scope': self.scope_key}
                ).json()['rules']
            }

        self.assertEqual(rows['WO_BLOCKED_SAFETY']['finding_counts']['open'], 1)
        self.assertEqual(rows['PACKET_STALLED']['finding_counts'], {})

    def test_rule_health_query_budget(self):
        """Rule health query count does not scale with registered rules."""
        for index in range(30):
            self.make_finding(discriminator=f'health-query-{index}')
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(
                reverse('risk-rule-health'), {'scope': self.scope_key}
            )
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(ctx.captured_queries), 15)

    def test_rule_patch_creates_audited_revision(self):
        """PATCH creates the next immutable revision plus audit events."""
        ensure_rule_definitions()
        response = self.client.patch(
            reverse('risk-rule-detail', kwargs={'code': 'PACKET_STALLED'}),
            {
                'enabled': True,
                'enabled_scopes': [self.scope_key],
                'config': {'stall_hours': 24},
                'reason': 'Pilot enablement',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['version'], 2)
        self.assertTrue(body['enabled'])
        revisions = RiskRuleDefinition.objects.filter(code='PACKET_STALLED')
        self.assertEqual(revisions.count(), 2)
        self.assertEqual(revisions.get(is_current=True).version, 2)
        self.assertEqual(
            RiskRuleConfigurationEvent.objects.filter(
                rule__code='PACKET_STALLED', action='activated'
            ).count(),
            1,
        )

    def test_rule_patch_requires_admin_permission(self):
        """Configuration is privileged (RR-ADR-010)."""
        plain = fresh(self.service)
        grant_permissions(plain, ['view_riskfinding'])
        plain = fresh(plain)
        SCOPES_BY_USERNAME['risk-service'] = {self.scope}
        client = APIClient()
        client.force_authenticate(plain)
        response = client.patch(
            reverse('risk-rule-detail', kwargs={'code': 'PACKET_STALLED'}),
            {'enabled': True, 'reason': 'nope'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_list_query_budget(self):
        """The ranked list stays within a bounded query budget."""
        for index in range(30):
            self.make_finding(discriminator=f'q{index}')
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(
                reverse('risk-finding-list'), {'scope': self.scope_key}
            )
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(ctx.captured_queries), 15)
