"""API-layer tests for the Right-Part Finder verification endpoints.

Exercises the additive ``/api/part/verification/`` surface with a plain DRF
``APIClient``: feature-flag gating, session lifecycle commands, candidate
review, evidence handling, decision resources, revalidation previews, and
authentication behaviour.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.test.utils import override_settings

from rest_framework.test import APIClient

from assets.models import AssetMachine, MachinePart
from common.models import Parameter, ParameterTemplate
from part.models import Part, PartRelated
from part.verification import services
from part.verification.policy import create_policy_version
from part.verification.scope import VerificationScope
from part.verification_models import PartVerificationSession

BASE = '/api/part/verification/'
SESSIONS = f'{BASE}sessions/'

FLAGS = {
    'AIMMS_RPF_ENABLED': True,
    'AIMMS_RPF_COLLECTION_ENABLED': True,
    'AIMMS_RPF_EVALUATION_ENABLED': True,
    'AIMMS_RPF_CONFIRMATION_ENABLED': True,
    'AIMMS_RPF_SCOPE_RESOLVER': 'part.test_verification_api._scope_resolver',
}

POLICY = {
    'schema_version': 1,
    'description': 'API layer test policy',
    'requirements': [
        {
            'key': 'electrical.voltage',
            'category': 'electrical',
            'value_kind': 'range',
            'operator': 'range_within',
            'unit': 'V',
            'hard': True,
            'sources': [
                {'kind': 'observation'},
                {'kind': 'parameter', 'template': 'APIVerifVoltage'},
            ],
            'missing_blocker': 'NAMEPLATE_REQUIRED',
        },
        {
            'key': 'electrical.phase',
            'value_kind': 'decimal',
            'operator': 'eq',
            'unit': '',
            'hard': True,
            'sources': [{'kind': 'parameter', 'template': 'APIVerifPhase'}],
            'candidate_missing': 'exclude',
            'conflict_code': 'PHASE_CONFLICT',
        },
    ],
    'retrieval': {'max_candidates': 50, 'tier_cap': 25},
    'rank_factors': [
        {'id': 'exact_requested_identity', 'max': 25},
        {'id': 'exact_application_relation', 'max': 25},
        {'id': 'evidence_coverage', 'max': 15},
        {'id': 'preferred_representation', 'max': 4},
        {'id': 'freshness', 'max': 3},
    ],
    'revalidation': {
        'non_material_paths': ['requested_part.description'],
        'expiry_hours': 24,
    },
}


def _scope_resolver(actor):
    """Resolve every actor to the explicit global verification scope."""
    return {VerificationScope(customer_id=None, site_key=None)}


@override_settings(**FLAGS)
class VerificationAPIBase(TestCase):
    """Shared fixture: superuser, active policy, catalog, and one machine."""

    client_class = APIClient

    @classmethod
    def setUpTestData(cls):
        """Create the shared catalog, policy, and machine fixture."""
        cls.user = get_user_model().objects.create_superuser(
            username='rpf_api_admin', email='rpf-api@example.com', password='x'
        )

        cls.policy = create_policy_version(
            key='rpf-core', version=1, definition=POLICY, activate=True
        )

        cls.requested = Part.objects.create(
            name='RPF API Motor A', IPN='RPF-API-001', active=True, component=True
        )
        cls.good = Part.objects.create(
            name='RPF API Motor B', IPN='RPF-API-002', active=True, component=True
        )
        cls.bad = Part.objects.create(
            name='RPF API Motor C', IPN='RPF-API-003', active=True, component=True
        )
        PartRelated.objects.create(part_1=cls.requested, part_2=cls.good)
        PartRelated.objects.create(part_1=cls.requested, part_2=cls.bad)

        part_ct = ContentType.objects.get_for_model(Part)
        voltage = ParameterTemplate.objects.create(name='APIVerifVoltage', units='V')
        phase = ParameterTemplate.objects.create(name='APIVerifPhase')
        rows = (
            (cls.requested, '460', '3'),
            (cls.good, '460', '3'),
            (cls.bad, '460', '1'),
        )
        for part, volts, phases in rows:
            Parameter.objects.create(
                model_type=part_ct, model_id=part.pk, template=voltage, data=volts
            )
            Parameter.objects.create(
                model_type=part_ct, model_id=part.pk, template=phase, data=phases
            )

        cls.machine = AssetMachine.objects.create(name='RPF API Machine', customer=None)
        MachinePart.objects.create(machine=cls.machine, part=cls.requested, quantity=1)

    def setUp(self):
        """Authenticate the API client as the fixture superuser."""
        super().setUp()
        self.client.force_authenticate(self.user)

    # ------------------------------------------------------------- helpers

    def _url(self, session_pk, suffix=''):
        """Return a session-scoped API URL."""
        return f'{SESSIONS}{session_pk}/{suffix}'

    def _create_session(self, key, **overrides):
        """POST a session-create command with the standard full context."""
        payload = {
            'purpose': 'installed_replacement',
            'idempotency_key': key,
            'requested_part_id': self.requested.pk,
            'machine_id': self.machine.pk,
        }
        payload.update(overrides)
        payload = {name: value for name, value in payload.items() if value is not None}
        return self.client.post(SESSIONS, payload, format='json')

    def _evaluate(self, session_pk, key, expected_revision=1):
        """POST an evaluate command for one session."""
        return self.client.post(
            self._url(session_pk, 'evaluate/'),
            {'idempotency_key': key, 'expected_revision': expected_revision},
            format='json',
        )

    def _reviewed_session(self):
        """Create and evaluate a session; return (body, candidate rows)."""
        response = self._create_session('helper-create-1')
        self.assertEqual(response.status_code, 201)
        session = response.json()

        response = self._evaluate(session['pk'], 'helper-eval-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state'], 'review_required')

        response = self.client.get(self._url(session['pk'], 'candidates/'))
        self.assertEqual(response.status_code, 200)
        return session, response.json()

    def _confirm(self, session_pk, evaluation_pk, key='helper-confirm-1'):
        """POST a confirm command for one candidate evaluation."""
        return self.client.post(
            self._url(session_pk, f'candidates/{evaluation_pk}/confirm/'),
            {'idempotency_key': key, 'expected_revision': 1, 'reason': 'api test'},
            format='json',
        )

    def _session_state(self, session_pk):
        """Return the current persisted state of one session."""
        return PartVerificationSession.objects.get(pk=session_pk).state


class VerificationSessionRoutesTest(VerificationAPIBase):
    """Flag gating, session creation, list/detail behaviour, and auth."""

    @override_settings(AIMMS_RPF_ENABLED=False)
    def test_disabled_flag_hides_every_route(self):
        """With the feature flag off, every route returns a plain 404."""
        self.assertEqual(self.client.get(SESSIONS).status_code, 404)

        response = self._create_session('disabled-create-1')
        self.assertEqual(response.status_code, 404)

        self.assertEqual(self.client.get(self._url(1)).status_code, 404)
        self.assertEqual(self._evaluate(1, 'disabled-eval-1').status_code, 404)

    def test_create_session_returns_reference(self):
        """Session creation returns 201 with a PVS reference and context."""
        response = self._create_session('create-ref-1')
        self.assertEqual(response.status_code, 201)

        body = response.json()
        self.assertTrue(body['reference'].startswith('PVS-'))
        self.assertEqual(body['purpose'], 'installed_replacement')
        self.assertEqual(body['state'], 'collecting')
        self.assertEqual(body['revision'], 1)
        self.assertEqual(body['requested_part'], self.requested.pk)
        self.assertEqual(body['machine'], self.machine.pk)
        self.assertEqual(body['policy_key'], 'rpf-core')
        self.assertEqual(body['policy_version'], 1)

    def test_create_session_requires_idempotency_key(self):
        """Missing idempotency_key is a serializer-level 400."""
        response = self.client.post(
            SESSIONS,
            {
                'purpose': 'installed_replacement',
                'requested_part_id': self.requested.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_create_session_replay_returns_consistent_body(self):
        """Replaying the same create command yields the same session."""
        first = self._create_session('create-replay-1')
        self.assertEqual(first.status_code, 201)

        second = self._create_session('create-replay-1')
        self.assertIn(second.status_code, (200, 201))

        self.assertEqual(first.json()['pk'], second.json()['pk'])
        self.assertEqual(first.json()['reference'], second.json()['reference'])

    def test_list_filters_by_state_and_purpose(self):
        """The session list honours simple state and purpose filters."""
        installed = self._create_session('list-installed-1').json()
        manual = self._create_session(
            'list-manual-1', purpose='manual', machine_id=None
        ).json()

        response = self.client.get(SESSIONS)
        self.assertEqual(response.status_code, 200)
        pks = {row['pk'] for row in response.json()}
        self.assertEqual(pks, {installed['pk'], manual['pk']})

        response = self.client.get(SESSIONS, {'purpose': 'manual'})
        self.assertEqual([row['pk'] for row in response.json()], [manual['pk']])

        response = self.client.get(SESSIONS, {'purpose': 'installed_replacement'})
        self.assertEqual([row['pk'] for row in response.json()], [installed['pk']])

        response = self.client.get(SESSIONS, {'state': 'collecting'})
        self.assertEqual(len(response.json()), 2)

        response = self.client.get(SESSIONS, {'state': 'confirmed'})
        self.assertEqual(response.json(), [])

    def test_session_detail_get(self):
        """The detail route returns the created session."""
        session = self._create_session('detail-get-1').json()

        response = self.client.get(self._url(session['pk']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pk'], session['pk'])
        self.assertEqual(response.json()['reference'], session['reference'])

    def test_session_detail_rejects_write_methods(self):
        """No generic PATCH/PUT/DELETE exists on the session resource."""
        session = self._create_session('detail-write-1').json()
        url = self._url(session['pk'])

        self.assertEqual(
            self.client.patch(url, {'state': 'confirmed'}).status_code, 405
        )
        self.assertEqual(self.client.put(url, {'state': 'confirmed'}).status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_unauthenticated_requests_rejected_on_every_route(self):
        """Every verification route requires authentication."""
        anonymous = APIClient()
        routes = [
            ('get', SESSIONS),
            ('post', SESSIONS),
            ('get', self._url(1)),
            ('post', self._url(1, 'evaluate/')),
            ('post', self._url(1, 'reevaluate/')),
            ('post', self._url(1, 'cancel/')),
            ('post', self._url(1, 'invalidate/')),
            ('get', self._url(1, 'readiness/')),
            ('get', self._url(1, 'current-observation/')),
            ('get', self._url(1, 'requirements/')),
            ('get', self._url(1, 'events/')),
            ('get', self._url(1, 'evidence/')),
            ('post', self._url(1, 'evidence/')),
            ('post', self._url(1, 'evidence/1/decide/')),
            ('get', self._url(1, 'candidates/')),
            ('get', self._url(1, 'candidates/1/')),
            ('post', self._url(1, 'candidates/1/reject/')),
            ('post', self._url(1, 'candidates/1/confirm/')),
            ('post', self._url(1, 'no-safe-match/')),
            ('get', self._url(1, 'decisions/')),
            ('get', f'{BASE}decisions/1/'),
            ('get', f'{BASE}decisions/1/uses/'),
        ]

        for method, url in routes:
            response = getattr(anonymous, method)(url)
            self.assertIn(response.status_code, (401, 403), f'{method.upper()} {url}')


class VerificationEvaluationAPITest(VerificationAPIBase):
    """Evaluate, readiness, candidate review, confirm, and no-safe-match."""

    def test_evaluate_happy_path(self):
        """A full-context session evaluates into review_required."""
        session = self._create_session('eval-happy-1').json()

        response = self._evaluate(session['pk'], 'eval-happy-cmd-1')
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body['state'], 'review_required')
        self.assertEqual(body['revision'], 1)
        self.assertEqual(body['blockers'], [])

        detail = self.client.get(self._url(session['pk'])).json()
        self.assertEqual(detail['state'], 'review_required')
        self.assertTrue(detail['universe_complete'])
        self.assertEqual(detail['considered_count'], 3)
        self.assertEqual(detail['eligible_count'], 2)

    def test_evaluate_wrong_revision_returns_conflict_envelope(self):
        """A wrong expected_revision yields the stable 409 envelope."""
        session = self._create_session('eval-conflict-1').json()

        response = self._evaluate(session['pk'], 'eval-conflict-cmd-1', 5)
        self.assertEqual(response.status_code, 409)

        body = response.json()
        self.assertEqual(body['code'], 'RPF_REVISION_CONFLICT')
        self.assertEqual(body['current_revision'], 1)
        self.assertTrue(body['correlation_id'])
        for field in ('detail', 'field_errors', 'blockers', 'retryable'):
            self.assertIn(field, body)

        # The rejected command changed nothing
        self.assertEqual(self._session_state(session['pk']), 'collecting')

    def test_readiness_reports_context_blockers(self):
        """Ambiguous installed context yields stable readiness blockers."""
        bare = AssetMachine.objects.create(name='RPF API Bare Machine', customer=None)
        session = self._create_session('ready-blocked-1', machine_id=bare.pk).json()

        response = self.client.get(self._url(session['pk'], 'readiness/'))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertFalse(body['ready'])
        self.assertEqual(body['ready_for'], 'collection')
        self.assertEqual(body['state'], 'collecting')

        codes = [blocker['code'] for blocker in body['blockers']]
        self.assertIn('ASSET_POSITION_REQUIRED', codes)
        for blocker in body['blockers']:
            for field in ('code', 'attribute', 'message', 'remediation'):
                self.assertIn(field, blocker)

    def test_readiness_ready_after_successful_evaluate(self):
        """A cleanly evaluated session reports ready for human review."""
        session, _ = self._reviewed_session()

        response = self.client.get(self._url(session['pk'], 'readiness/'))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertTrue(body['ready'])
        self.assertEqual(body['ready_for'], 'human_review')
        self.assertEqual(body['state'], 'review_required')
        self.assertEqual(body['blockers'], [])
        self.assertEqual(body['policy'], {'key': 'rpf-core', 'version': 1})

    def test_candidates_ordered_survivors_first(self):
        """Survivors sort by ascending rank; exclusions follow with no rank."""
        _, rows = self._reviewed_session()
        self.assertEqual(len(rows), 3)

        self.assertEqual(rows[0]['candidate'], self.requested.pk)
        self.assertEqual(rows[0]['rank'], 1)
        self.assertTrue(rows[0]['eligible'])

        self.assertEqual(rows[1]['candidate'], self.good.pk)
        self.assertEqual(rows[1]['rank'], 2)
        self.assertTrue(rows[1]['eligible'])

        self.assertEqual(rows[2]['candidate'], self.bad.pk)
        self.assertIsNone(rows[2]['rank'])
        self.assertFalse(rows[2]['eligible'])

    def test_candidates_eligible_filter(self):
        """The ?eligible filter selects survivors or exclusions only."""
        session, _ = self._reviewed_session()
        url = self._url(session['pk'], 'candidates/')

        response = self.client.get(url, {'eligible': 'false'})
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual([row['candidate'] for row in rows], [self.bad.pk])
        self.assertFalse(rows[0]['eligible'])

        response = self.client.get(url, {'eligible': 'true'})
        self.assertEqual(
            [row['candidate'] for row in response.json()],
            [self.requested.pk, self.good.pk],
        )

    def test_candidate_detail_shows_hard_conflicts(self):
        """An excluded candidate exposes display values for each conflict."""
        session, rows = self._reviewed_session()
        excluded = rows[2]

        response = self.client.get(
            self._url(session['pk'], f'candidates/{excluded["pk"]}/')
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertFalse(body['eligible'])
        self.assertIsNone(body['rank'])
        self.assertEqual(len(body['hard_conflicts']), 1)

        conflict = body['hard_conflicts'][0]
        self.assertEqual(conflict['key'], 'electrical.phase')
        self.assertEqual(conflict['reason_code'], 'PHASE_CONFLICT')
        self.assertEqual(conflict['requirement']['operator'], 'eq')
        self.assertIsNotNone(conflict['requirement']['value'])
        self.assertEqual(conflict['candidate']['raw'], '1')
        self.assertIsNotNone(conflict['candidate']['value'])

    def test_confirm_candidate_returns_decision(self):
        """Confirming an eligible candidate returns the decision payload."""
        session, rows = self._reviewed_session()
        survivor = rows[1]

        response = self._confirm(session['pk'], survivor['pk'], 'confirm-good-1')
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body['kind'], 'confirmed')
        self.assertEqual(body['selected_part'], self.good.pk)
        self.assertEqual(body['selected_evaluation'], survivor['pk'])
        self.assertEqual(body['session'], session['pk'])
        self.assertEqual(body['decided_by'], self.user.pk)

        detail = self.client.get(self._url(session['pk'])).json()
        self.assertEqual(detail['state'], 'confirmed')
        self.assertEqual(detail['current_decision'], body['pk'])

    def test_confirm_excluded_candidate_conflict(self):
        """Confirming an excluded candidate fails with a stable 409 code."""
        session, rows = self._reviewed_session()
        excluded = rows[2]

        response = self._confirm(session['pk'], excluded['pk'], 'confirm-bad-1')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'RPF_CANDIDATE_INELIGIBLE')

        self.assertEqual(self._session_state(session['pk']), 'review_required')

    def test_no_safe_match_rejected_while_survivors_exist(self):
        """No-safe-match is invalid while eligible candidates remain."""
        session, _ = self._reviewed_session()

        response = self.client.post(
            self._url(session['pk'], 'no-safe-match/'),
            {
                'idempotency_key': 'nsm-invalid-1',
                'expected_revision': 1,
                'reason': 'attempted abstention',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'RPF_NO_SAFE_MATCH_INVALID')

        self.assertEqual(self._session_state(session['pk']), 'review_required')


class VerificationEvidenceAndDecisionAPITest(VerificationAPIBase):
    """Evidence commands, decision resources, and observation previews."""

    def test_attach_and_accept_evidence(self):
        """Attached evidence starts proposed and becomes accepted on decide."""
        session = self._create_session('evidence-create-1').json()

        response = self.client.post(
            self._url(session['pk'], 'evidence/'),
            {
                'idempotency_key': 'evidence-attach-1',
                'requirement_key': 'electrical.voltage',
                'value': '460',
                'unit': 'V',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)

        evidence = response.json()
        self.assertEqual(evidence['decision'], 'proposed')
        self.assertEqual(evidence['requirement_key'], 'electrical.voltage')
        self.assertEqual(evidence['unit'], 'V')

        response = self.client.post(
            self._url(session['pk'], f'evidence/{evidence["pk"]}/decide/'),
            {'idempotency_key': 'evidence-accept-1', 'accept': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['decision'], 'accepted')
        self.assertEqual(response.json()['decided_by'], self.user.pk)

    def test_decision_resources_after_confirm(self):
        """Decision list, decision detail, and use list read back a confirm."""
        session, rows = self._reviewed_session()
        decision = self._confirm(session['pk'], rows[1]['pk']).json()

        response = self.client.get(self._url(session['pk'], 'decisions/'))
        self.assertEqual(response.status_code, 200)
        listed = response.json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['pk'], decision['pk'])
        self.assertEqual(listed[0]['kind'], 'confirmed')

        response = self.client.get(f'{BASE}decisions/{decision["pk"]}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pk'], decision['pk'])
        self.assertEqual(response.json()['session'], session['pk'])

        # Bind one consumer use through the service layer, then read it back
        use = services.validate_and_bind_use(
            decision_id=decision['pk'],
            actor=self.user,
            consumer_kind='job_kit',
            consumer_action='substitution_decide',
            idempotency_key='api-use-1',
            expected_requested_part_id=self.requested.pk,
            expected_selected_part_id=self.good.pk,
            command_hash='sha256:test',
        )

        response = self.client.get(f'{BASE}decisions/{decision["pk"]}/uses/')
        self.assertEqual(response.status_code, 200)
        uses = response.json()
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0]['pk'], use.pk)
        self.assertEqual(uses[0]['consumer_kind'], 'job_kit')
        self.assertEqual(uses[0]['consumer_action'], 'substitution_decide')
        self.assertEqual(uses[0]['decision'], decision['pk'])

    def test_observation_preview_null_before_any_decision(self):
        """Without a decision the preview reports no severity."""
        session = self._create_session('preview-none-1').json()

        response = self.client.get(self._url(session['pk'], 'current-observation/'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'severity': None, 'differences': []})

    def test_observation_preview_after_source_drift(self):
        """After confirm, source drift shows in the preview without staling."""
        session, rows = self._reviewed_session()
        self._confirm(session['pk'], rows[1]['pk'], 'preview-confirm-1')

        machine = AssetMachine.objects.get(pk=self.machine.pk)
        machine.model = 'DRIFTED-MODEL'
        machine.save()

        response = self.client.get(self._url(session['pk'], 'current-observation/'))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body['severity'], 'material_review')
        paths = [difference['path'] for difference in body['differences']]
        self.assertIn('machine.model', paths)

        # The preview carries no authority: the session stays confirmed
        self.assertEqual(self._session_state(session['pk']), 'confirmed')
