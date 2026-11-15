"""Pre-dispatch and post-read fast-path authority controls with fake provider."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import FastPathRouter, RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.workflows.root import RootWorkflow
from django.test import SimpleTestCase


class FastPathAuthorityOrderingTests(SimpleTestCase):
    """Observe the real root and router; provider and profiles are synthetic."""

    def _run(
        self,
        modality,
        profiles,
        *,
        principal=True,
        switch_actor=False,
        switch_during_recheck=False,
        switch_during_precheck=False,
    ):
        events = []
        decisions = []
        allowed = frozenset({("part", "view"), ("stock", "view")})
        current = [SimpleNamespace(user_pk="1") if principal else None]

        class Provider:
            async def search_parts_page(self, **kwargs):
                events.append("provider")
                if switch_actor:
                    current[0] = SimpleNamespace(user_pk="2")
                return {
                    "results": [{"pk": 1, "in_stock": 4}],
                    "population_count": 1,
                    "has_more": False,
                }

        class Conversations:
            def get_or_create_state(self, thread_id, user_id):
                return SimpleNamespace()

            async def gather_context(self, query, thread_id, user_id):
                return {"modality": modality}

        class Fallback:
            async def run_stream(self, **kwargs):
                decisions.append(kwargs["decision"])
                yield "synthetic fallback"

        profile_values = iter(profiles)

        async def fresh(user_pk):  # noqa: RUF029
            events.append("permission")
            if switch_during_precheck and events.count("permission") == 1:
                current[0] = SimpleNamespace(user_pk="2")
            if switch_during_recheck and events.count("permission") == 2:
                current[0] = SimpleNamespace(user_pk="2")
            result = next(profile_values, allowed)
            if isinstance(result, Exception):
                raise result
            return result

        async def execute():
            router = UnifiedRouter()
            router.fast_path._data_provider = Provider()
            router.semantic.route = AsyncMock(
                return_value=RoutingDecision(
                    workflow_type=WorkflowType.T1_LOOKUP,
                    confidence=1.0,
                    reasoning="synthetic fallback",
                )
            )
            root = RootWorkflow(
                router=router,
                registry=SimpleNamespace(get_workflow=lambda _: Fallback()),
                conversation_manager=Conversations(),
            )
            settings = Settings(
                _env_file=None,
                TURN_WALL_CLOCK_CAP_S=10,
                TURN_ROUTING_BUDGET_S=5,
                FEATURE_VOICE_FAST_PATH=True,
            )
            with (
                patch("ai.core.workflows.root.get_settings", return_value=settings),
                patch("ai.core.config.get_settings", return_value=settings),
                patch(
                    "ai.core.auth.get_current_principal",
                    side_effect=lambda: current[0],
                ),
                patch("ai.core.tools.invocation_guard.fresh_permission_profile", side_effect=fresh),
            ):
                return [
                    chunk
                    async for chunk in root.run_stream(
                        "How many resistors do we have in stock?",
                        thread_id="synthetic",
                        user_id="1",
                    )
                ]

        chunks = asyncio.run(execute())
        return events, decisions, chunks

    def test_denied_or_missing_actor_never_invokes_provider(self):
        for modality in ("voice", "text"):
            for principal in (False, True):
                with self.subTest(modality=modality, principal=principal):
                    events, decisions, chunks = self._run(
                        modality, [frozenset()], principal=principal
                    )
                    self.assertNotIn("provider", events)
                    self.assertEqual(chunks, ["synthetic fallback"])
                    self.assertIsNone(decisions[0].fast_path_result)

    def test_authorized_voice_remains_available(self):
        allowed = frozenset({("part", "view"), ("stock", "view")})
        events, _, chunks = self._run("voice", [allowed] * 3)
        self.assertLess(events.index("permission"), events.index("provider"))
        self.assertEqual(chunks, ["We have 4 resistors in stock across 1 part."])

    def test_revocation_after_provider_read_does_not_reach_fallback_carrier(self):
        allowed = frozenset({("part", "view"), ("stock", "view")})
        for modality in ("voice", "text"):
            with self.subTest(modality=modality):
                events, decisions, chunks = self._run(modality, [allowed, frozenset()])
                self.assertLess(events.index("permission"), events.index("provider"))
                self.assertEqual(chunks, ["synthetic fallback"])
                self.assertIsNone(decisions[0].fast_path_result)

    def test_actor_change_during_provider_read_discards_decision(self):
        allowed = frozenset({("part", "view"), ("stock", "view")})
        events, decisions, chunks = self._run("text", [allowed], switch_actor=True)
        self.assertIn("provider", events)
        self.assertEqual(chunks, ["synthetic fallback"])
        self.assertIsNone(decisions[0].fast_path_result)

    def test_actor_change_during_final_permission_check_discards_decision(self):
        allowed = frozenset({("part", "view"), ("stock", "view")})
        events, decisions, chunks = self._run(
            "text", [allowed, allowed], switch_during_recheck=True
        )
        self.assertIn("provider", events)
        self.assertEqual(chunks, ["synthetic fallback"])
        self.assertIsNone(decisions[0].fast_path_result)

    def test_actor_change_during_initial_permission_check_prevents_provider(self):
        allowed = frozenset({("part", "view"), ("stock", "view")})
        events, decisions, chunks = self._run("text", [allowed], switch_during_precheck=True)
        self.assertNotIn("provider", events)
        self.assertEqual(chunks, ["synthetic fallback"])
        self.assertIsNone(decisions[0].fast_path_result)

    def test_voice_output_denial_does_not_pass_the_result_to_fallback(self):
        """Revocation at the later voice gate must not forward provider data."""
        allowed = frozenset({("part", "view"), ("stock", "view")})
        events, decisions, chunks = self._run("voice", [allowed, allowed, frozenset()])
        self.assertIn("provider", events)
        self.assertEqual(chunks, ["synthetic fallback"])
        self.assertIsNone(decisions[0].fast_path_result)

    def test_text_fallback_receives_route_but_not_provider_result(self):
        """A non-voice fallback needs routing, not the service-provider payload."""
        allowed = frozenset({("part", "view"), ("stock", "view")})
        events, decisions, chunks = self._run("text", [allowed, allowed])
        self.assertIn("provider", events)
        self.assertEqual(chunks, ["synthetic fallback"])
        self.assertEqual(decisions[0].workflow_type, WorkflowType.T1_LOOKUP)
        self.assertIsNone(decisions[0].fast_path_result)

    def test_permission_check_errors_fail_closed_before_and_after_provider(self):
        allowed = frozenset({("part", "view"), ("stock", "view")})
        for profiles, expected_provider in (
            ([RuntimeError("synthetic failure")], False),
            ([allowed, RuntimeError("synthetic failure")], True),
        ):
            with self.subTest(expected_provider=expected_provider):
                events, decisions, chunks = self._run("voice", profiles)
                self.assertEqual("provider" in events, expected_provider)
                self.assertEqual(chunks, ["synthetic fallback"])
                self.assertIsNone(decisions[0].fast_path_result)

    def test_revocation_between_search_and_dependent_read_prevents_second_call(self):
        """Do not issue dependent provider reads after a selected role loss."""
        allowed = frozenset({("part", "view"), ("stock", "view")})
        cases = (
            ("How much stock of bolt?", "get_stock_page"),
            ("Tell me about part bolt", "get_part"),
            ("Show bom for bolt", "get_bom_page"),
            ("Where is bolt?", "get_stock_page"),
        )
        for query, dependent in cases:
            with self.subTest(query=query):
                active = [True]

                async def search(*args, active=active, **kwargs):  # noqa: RUF029
                    active[0] = False
                    return [{"pk": 1, "name": "Synthetic"}]

                async def fresh(actor_pk, active=active):  # noqa: RUF029
                    return allowed if active[0] else frozenset()

                provider = SimpleNamespace(
                    search_parts=AsyncMock(side_effect=search),
                    get_stock_page=AsyncMock(
                        return_value={"results": [], "population_count": 0, "has_more": False}
                    ),
                    get_part=AsyncMock(return_value={"pk": 1, "name": "Synthetic"}),
                    get_bom_page=AsyncMock(
                        return_value={"results": [], "population_count": 0, "has_more": False}
                    ),
                )
                router = FastPathRouter()
                router._data_provider = provider
                with (
                    patch(
                        "ai.core.auth.get_current_principal",
                        return_value=SimpleNamespace(user_pk="1"),
                    ),
                    patch(
                        "ai.core.tools.invocation_guard.fresh_permission_profile", side_effect=fresh
                    ),
                ):
                    result = asyncio.run(router.try_fast_path(query, "synthetic"))
                self.assertIsNone(result)
                provider.search_parts.assert_awaited_once()
                getattr(provider, dependent).assert_not_awaited()

    def test_every_category_checks_its_required_role_before_dispatch(self):
        """Part-only authority admits part/BOM, not stock-derived categories."""
        cases = (
            ("How many resistors do we have in stock?", "stock_group", False),
            ("How much stock of bolt?", "stock_check", False),
            ("Tell me about part bolt", "part_details", True),
            ("Show bom for bolt", "bom", True),
            ("Where is bolt?", "location", False),
        )
        for query, result_type, permitted in cases:
            with self.subTest(query=query):
                router = FastPathRouter()
                execute = AsyncMock(return_value={"type": result_type})
                with (
                    patch.object(router, "_execute_fast_path", new=execute),
                    patch(
                        "ai.core.auth.get_current_principal",
                        return_value=SimpleNamespace(user_pk="1"),
                    ),
                    patch(
                        "ai.core.tools.invocation_guard.fresh_permission_profile",
                        new=AsyncMock(return_value=frozenset({("part", "view")})),
                    ),
                ):
                    decision = asyncio.run(router.try_fast_path(query, "synthetic"))
                self.assertEqual(decision is not None, permitted)
                self.assertEqual(execute.await_count, int(permitted))
