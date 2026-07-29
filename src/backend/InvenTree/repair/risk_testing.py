"""Shared fixtures for the Risk Radar test suites.

ORM builders in the style of ``tasks/tests/closeout_fixtures.py`` — no
Django fixture files. Also hosts the test scope resolver referenced via
``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` so scope survives user refetches.
"""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from tasks.scope import MaintenanceScope

from .risk_models import RiskFinding, RiskFindingState, RiskScanRun, RiskScanStatus
from .risk_rules import RiskCandidate, RiskRule, RuleSpec
from .risk_services import current_rule_revision, ensure_rule_definitions

# Registered per-test by RiskEnvMixin; consulted by scope_resolver below.
SCOPES_BY_USERNAME: dict[str, set[MaintenanceScope]] = {}

RISK_FLAGS = {
    'AIMMS_RISK_RADAR_ENABLED': True,
    'AIMMS_WORK_ORDERS_ENABLED': True,
    'AIMMS_JOB_KITS_ENABLED': True,
    'AIMMS_MAINTENANCE_SCOPE_RESOLVER': 'repair.risk_testing.scope_resolver',
}


def scope_resolver(actor) -> set[MaintenanceScope]:
    """Resolve test scopes by username (survives user refetches)."""
    return SCOPES_BY_USERNAME.get(getattr(actor, 'username', ''), set())


class ScriptedRule(RiskRule):
    """A controllable rule for exercising engine semantics.

    ``script`` is a list of steps; each step is either a list of
    ``RiskCandidate`` (yielded as one page), a callable (invoked between
    pages for interleaving side effects), or an exception instance (raised
    mid-evaluation). The final candidate page is marked complete unless
    ``never_complete`` is set.
    """

    code = 'SCRIPTED_RULE'
    version = 1
    category = 'operations'
    cadence = 'hourly'
    source_kind = 'asset_machine'
    severity_base = 'medium'
    default_config: dict = {}

    def __init__(self):
        """Start with an empty script."""
        self.script: list = []
        self.never_complete = False

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Replay the scripted pages.

        Yields:
            RiskEvaluationPage: each scripted candidate page in order.
        """
        now = timezone.now()
        page_steps = [step for step in self.script if isinstance(step, list)]
        emitted = 0
        for step in self.script:
            if isinstance(step, Exception):
                raise step
            if callable(step):
                step()
                continue
            emitted += 1
            complete = (not self.never_complete) and emitted == len(page_steps)
            yield from self._page(step, now, complete=complete)
        if not page_steps and not self.never_complete:
            yield from self._page([], now, complete=True)

    def _page(self, candidates, now, *, complete):
        """Yield one page of scripted candidates."""
        from .risk_rules import RiskEvaluationPage

        yield RiskEvaluationPage(
            candidates=tuple(candidates),
            source_as_of=now,
            next_watermark={'strategy': 'full_snapshot', 'as_of': now.isoformat()},
            complete=complete,
        )


def make_candidate(discriminator: str = 'c1', **overrides) -> RiskCandidate:
    """Build a minimal valid candidate for the scripted rule."""
    now = timezone.now()
    values = {
        'fingerprint_parts': (discriminator,),
        'source_model': 'assets.AssetMachine',
        'source_id': discriminator,
        'title': f'Scripted condition {discriminator}',
        'summary': 'A scripted risky condition',
        'severity_factors': {'base': 'medium'},
        'evidence': {'discriminator': discriminator},
        'source_as_of': now,
        'condition_started_at': now,
        'due_at': None,
        'action_links': [],
    }
    values.update(overrides)
    return RiskCandidate(**values)


def scripted_spec(rule: ScriptedRule) -> RuleSpec:
    """Wrap a scripted rule instance in a registry spec."""
    return RuleSpec(
        code=rule.code,
        category=rule.category,
        cadence=rule.cadence,
        source_kind=rule.source_kind,
        severity_base=rule.severity_base,
        critical_rule=False,
        default_config={},
        requires_flags=(),
        evaluator=rule,
    )


def grant_permissions(user, codenames) -> None:
    """Grant repair-app permissions and return a cache-clean user."""
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(codename=codename, content_type__app_label='repair')
        )


def fresh(user):
    """Refetch a user to clear the permission cache."""
    return get_user_model().objects.get(pk=user.pk)


class RiskEnvMixin:
    """Common environment builder for risk tests."""

    def build_env(self):
        """Create customers, clients, users, scope registrations, and machines.

        Work orders carry explicit customers (customer scopes, ``c`` keys);
        machines belong to client tenants (client scopes, ``k`` keys). Actors
        hold both grants for their side, mirroring a deployment where one
        organization sees its sales jobs and its own plant.
        """
        from assets.models import AssetMachine, Client
        from company.models import Company

        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(name='Customer A', is_customer=True)
        self.other_customer = Company.objects.create(
            name='Customer B', is_customer=True
        )
        self.client_tenant = Client.objects.create(
            name=f'Client A {suffix}', code=f'client-a-{suffix}'
        )
        self.other_client = Client.objects.create(
            name=f'Client B {suffix}', code=f'client-b-{suffix}'
        )
        self.scope = MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        self.other_scope = MaintenanceScope(
            customer_id=self.other_customer.pk, site_key=None
        )
        self.client_scope = MaintenanceScope(
            customer_id=None, site_key=None, client_id=self.client_tenant.pk
        )
        self.other_client_scope = MaintenanceScope(
            customer_id=None, site_key=None, client_id=self.other_client.pk
        )
        from .risk_scope import encode_scope

        self.scope_key = encode_scope(self.scope)
        self.other_scope_key = encode_scope(self.other_scope)
        self.client_scope_key = encode_scope(self.client_scope)
        self.other_client_scope_key = encode_scope(self.other_client_scope)

        User = get_user_model()
        self.service = User.objects.create_user(
            username='risk-service', email='svc@example.com', password='pw'
        )
        self.actor = User.objects.create_user(
            username='risk-actor', email='actor@example.com', password='pw'
        )
        SCOPES_BY_USERNAME.clear()
        SCOPES_BY_USERNAME['risk-service'] = {self.scope, self.client_scope}
        SCOPES_BY_USERNAME['risk-actor'] = {self.scope, self.client_scope}

        self.machine = AssetMachine.objects.create(
            name=f'Press {uuid.uuid4().hex[:8]}', client=self.client_tenant
        )
        self.other_machine = AssetMachine.objects.create(
            name=f'Lathe {uuid.uuid4().hex[:8]}', client=self.other_client
        )

    def teardown_scopes(self):
        """Clear the shared scope registry."""
        SCOPES_BY_USERNAME.clear()

    def enable_rule(
        self, code, *, config=None, notification_policy=None, scopes=None, critical=None
    ):
        """Provision and enable a rule revision for the test scope."""
        ensure_rule_definitions()
        revision = current_rule_revision(code)
        revision.enabled = True
        revision.enabled_scopes = scopes if scopes is not None else [self.scope_key]
        if config is not None:
            revision.config = config
        if notification_policy is not None:
            revision.notification_policy = notification_policy
        if critical is not None:
            revision.critical_rule = critical
        revision.save()
        return revision

    def make_work_order(
        self,
        *,
        customer=None,
        machine=None,
        lifecycle='ready',
        assigned_to=None,
        title='WO',
    ):
        """Create a minimal work order (WorkOrder)."""
        from tasks.models import WorkOrder

        return WorkOrder.objects.create(
            title=title,
            status='backlog',
            priority='medium',
            lifecycle_status=lifecycle,
            customer=customer if customer is not None else self.customer,
            machine=machine,
            assigned_to=assigned_to,
        )

    def make_finding(
        self,
        *,
        code='PACKET_STALLED',
        discriminator='f1',
        state=RiskFindingState.OPEN,
        scope_key=None,
        severity='medium',
        category=None,
        **overrides,
    ):
        """Create a finding directly (with a synthetic complete run)."""
        ensure_rule_definitions()
        revision = current_rule_revision(code)
        now = timezone.now()
        run = RiskScanRun.objects.create(
            rule=revision,
            rule_version=revision.version,
            activation_generation=revision.activation_generation,
            scope_key=scope_key or self.scope_key,
            lease_token='test-token',
            started_at=now,
            completed_at=now,
            status=RiskScanStatus.COMPLETE,
        )
        values = {
            'fingerprint': uuid.uuid4().hex + uuid.uuid4().hex,
            'scope_key': scope_key or self.scope_key,
            'rule_revision': revision,
            'rule_code': code,
            'rule_version': revision.version,
            'category': category or revision.category,
            'severity': severity,
            'severity_factors': {'base': severity, 'policy_version': 1},
            'source_model': 'assets.AssetMachine',
            'source_id': discriminator,
            'title': f'Finding {discriminator}',
            'summary': 'test',
            'evidence': {'discriminator': discriminator},
            'state': state,
            'first_seen': now,
            'last_seen': now,
            'condition_started_at': now,
            'last_seen_run': run,
            'source_as_of': now,
        }
        values.update(overrides)
        return RiskFinding.objects.create(**values)
