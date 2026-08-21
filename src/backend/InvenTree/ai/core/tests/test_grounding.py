"""S27: capture ledger + cite-or-downgrade grounding validator."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.grounding import (  # noqa: E402
    DOWNGRADE_TEMPLATE,
    _heuristic_grounded,
    evaluate_manual_grounding,
    ungrounded_identifiers,
)
from ai.core.tools.capture_ledger import (  # noqa: E402
    MAX_CAPTURES,
    ToolCaptureLedger,
    bind_tool_captures,
    current_tool_captures,
    record_tool_result,
)

_MANUAL_TITLE = "Xylem Flygt NP 3301 O&M manual (rev 3)"


def _manuals_result(chunk_ids=("chunk-1", "chunk-2"), asset_id=""):
    return {
        "chunks": [
            {
                "excerpt": "20.2 Repair boundaries: isolate MCC-HW-01 first.",
                "score": 3.2,
                "citation": {
                    "document": _MANUAL_TITLE,
                    "document_id": "DOC-PS1",
                    "revision": "3",
                    "section_id": "20.2",
                    "section_path": "20 Service / 20.2 Repair boundaries",
                    "chunk_id": chunk_id,
                    "as_of": "2026-08-01",
                    "asset_id": asset_id,
                    "excerpt_hash": "abc",
                },
            }
            for chunk_id in chunk_ids
        ],
        "total": len(chunk_ids),
        "machine_filter": "TC-INF-PS1-001",
    }


def _ledger_with_manuals() -> ToolCaptureLedger:
    ledger = ToolCaptureLedger()
    ledger.record("manuals.read:search_manuals", _manuals_result())
    return ledger


class TestCaptureLedger:
    """Bounded observation of tool results."""

    def test_manuals_citations_are_extracted(self) -> None:
        ledger = _ledger_with_manuals()
        citations = ledger.manuals_citations()
        assert [c["chunk_id"] for c in citations] == ["chunk-1", "chunk-2"]
        assert citations[0]["document"] == _MANUAL_TITLE

    def test_citation_asset_id_is_carried_through(self) -> None:
        """P8-W0a: the fence needs the cited chunk's machine identity."""
        ledger = ToolCaptureLedger()
        ledger.record("manuals.read:search_manuals", _manuals_result(asset_id="SER-PS1-001"))
        citations = ledger.manuals_citations()
        assert {c["asset_id"] for c in citations} == {"SER-PS1-001"}

    def test_attachment_citations_carry_the_trust_tier(self) -> None:
        """R2: attachment-corpus citations join the pool with their tier."""
        ledger = ToolCaptureLedger()
        result = _manuals_result(asset_id="PVS351-UL-2012-0173")
        for chunk in result["chunks"]:
            chunk["citation"]["access_class"] = "attachment_uploaded"
            chunk["citation"]["source_file_name"] = "UL1741_UserManual.pdf"
        ledger.record("documents.read:search_attachment_docs", result)
        citations = ledger.manuals_citations()
        assert {c["access_class"] for c in citations} == {"attachment_uploaded"}
        assert {c["source_file_name"] for c in citations} == {"UL1741_UserManual.pdf"}

    def test_governed_citations_default_to_an_empty_tier(self) -> None:
        """Governed rows carry no access_class field; the capture stays ''."""
        ledger = _ledger_with_manuals()
        citations = ledger.manuals_citations()
        assert {c["access_class"] for c in citations} == {""}
        assert {c["source_file_name"] for c in citations} == {""}

    def test_governed_citations_default_the_media_keys_to_empty(self) -> None:
        """R3 additive keys: governed and doc rows carry '' for every media
        coordinate, so the capture shape stays uniform across corpora."""
        ledger = _ledger_with_manuals()
        for citation in ledger.manuals_citations():
            for key in (
                "media_type",
                "work_order_id",
                "timecode_start_s",
                "timecode_end_s",
            ):
                assert citation[key] == "", key

    def test_media_citations_carry_their_evidence_coordinates(self) -> None:
        """R3: an evidence-media result joins the pool with its media keys."""
        payload = {
            "chunks": [
                {
                    "excerpt": "Nameplate: Flygt NP 3301, 415 V",
                    "score": 2.7,
                    "citation": {
                        "document": "nameplate-hx200",
                        "source_file_name": "nameplate-hx200.png",
                        "chunk_id": "att-9-abc123def456-img-0",
                        "access_class": "evidence_recording",
                        "media_type": "image",
                        "work_order_id": 104,
                        "timecode_start_s": None,
                        "timecode_end_s": None,
                        "asset_id": "SER-PS1-001",
                        "excerpt_hash": "abc",
                    },
                }
            ],
            "total": 1,
            "machine_filter": "HX-200",
            "work_order_filter": "WO-EVAL-HX200",
        }
        ledger = ToolCaptureLedger()
        ledger.record("evidence.read:search_evidence_media", payload)
        citations = ledger.manuals_citations()
        assert [c["chunk_id"] for c in citations] == ["att-9-abc123def456-img-0"]
        citation = citations[0]
        assert citation["access_class"] == "evidence_recording"
        assert citation["media_type"] == "image"
        assert citation["work_order_id"] == "104"
        # An image has no timecodes; None projects to the same '' default.
        assert citation["timecode_start_s"] == ""
        assert citation["timecode_end_s"] == ""
        # thumbnail_path is deliberately not captured: the stored path embeds
        # the uploader-chosen filename (review finding, R3).
        assert "thumbnail_path" not in citation
        assert citation["asset_id"] == "SER-PS1-001"
        # The cited work-order id counts as observed once a tool returned it.
        assert "104" in ledger.observed_values()

    def test_media_tool_machine_candidates_are_captured(self) -> None:
        """The candidates gate accepts the R3 tool id alongside the R2 pair."""
        payload = {
            "chunks": [],
            "total": 0,
            "machine_filter": "ambiguous",
            "machine_candidates": [
                {"machine_id": 1, "name": "Pump A", "serial": "A"},
                {"machine_id": 2, "name": "Pump B", "serial": "B"},
            ],
        }
        ledger = ToolCaptureLedger()
        ledger.record("search_evidence_media", payload)
        assert len(ledger.manuals_machine_candidates()) == 2

    def test_attachment_tool_machine_candidates_are_captured(self) -> None:
        """The candidates gate admits both retrieval tools, nothing else."""
        payload = {
            "chunks": [],
            "total": 0,
            "machine_filter": "ambiguous",
            "machine_candidates": [
                {"machine_id": 1, "name": "Pump A", "serial": "A"},
                {"machine_id": 2, "name": "Pump B", "serial": "B"},
            ],
        }
        ledger = ToolCaptureLedger()
        ledger.record("search_attachment_docs", payload)
        assert len(ledger.manuals_machine_candidates()) == 2
        other = ToolCaptureLedger()
        other.record("some_other_tool", payload)
        assert other.manuals_machine_candidates() == []

    def test_generic_results_feed_observed_values(self) -> None:
        ledger = ToolCaptureLedger()
        ledger.record(
            "machines.read:machine_overview",
            {
                "identity": {"machine_id": 44, "serial": "TC-INF-PS1-001"},
                "installed_parts": {"parts": [{"part_id": 7, "ipn": "EQ-INF-SEL-0080"}]},
            },
        )
        observed = ledger.observed_values()
        assert {"44", "TC-INF-PS1-001", "7", "EQ-INF-SEL-0080"} <= observed

    def test_capture_count_is_bounded(self) -> None:
        ledger = ToolCaptureLedger()
        for index in range(MAX_CAPTURES + 10):
            ledger.record("t", {"id": index})
        assert len(ledger.captures) == MAX_CAPTURES

    def test_unserializable_results_are_dropped_silently(self) -> None:
        ledger = ToolCaptureLedger()
        ledger.record("t", {"bad": object()})
        # json.dumps(default=str) keeps it stringly; nothing raises either way.
        record_tool_result("t", {"also": "fine"})  # unbound: no-op, no raise

    def test_rebinding_gives_a_fresh_ledger(self) -> None:
        first = bind_tool_captures()
        first.record("t", {"id": 1})
        second = bind_tool_captures()
        assert current_tool_captures() is second
        assert second.captures == []


class TestUngroundedIdentifiers:
    """Code-shaped identifiers the server never showed."""

    def test_extraction_and_closure_filtering(self) -> None:
        text = "Order EQ-INF-SEL-0080 and clear fault AL-OVERTEMP, then check XX-FAKE-99."
        known = frozenset({"eq-inf-sel-0080", "AL-OVERTEMP"})
        assert ungrounded_identifiers(text, known) == ("XX-FAKE-99",)

    def test_prose_never_matches(self) -> None:
        assert ungrounded_identifiers("Check the seal and the impeller.", frozenset()) == ()

    def test_report_is_capped(self) -> None:
        text = " ".join(f"ZZ-CODE-{index:02d}" for index in range(30))
        assert len(ungrounded_identifiers(text, frozenset())) == 10


class TestHeuristic:
    """The cheap layer can only pass, never downgrade."""

    def test_title_mention_grounds(self) -> None:
        citations = _ledger_with_manuals().manuals_citations()
        message = "Per the Xylem Flygt NP 3301 O&M manual, isolate MCC-HW-01 first."
        assert _heuristic_grounded(message, citations)

    def test_document_id_mention_grounds(self) -> None:
        citations = _ledger_with_manuals().manuals_citations()
        assert _heuristic_grounded("See DOC-PS1 section 20.2.", citations)

    def test_no_reference_fails(self) -> None:
        citations = _ledger_with_manuals().manuals_citations()
        assert not _heuristic_grounded("Just replace the seal.", citations)


class TestEvaluateManualGrounding:
    """Mode semantics, audit validation, and the outage rule."""

    def test_mode_off_and_no_citations_do_not_apply(self) -> None:
        message = "Replace the seal."
        assert evaluate_manual_grounding(
            message=message, ledger=_ledger_with_manuals(), mode="off"
        ) == (message, None)
        empty = ToolCaptureLedger()
        assert evaluate_manual_grounding(message=message, ledger=empty, mode="shadow") == (
            message,
            None,
        )
        assert evaluate_manual_grounding(message=message, ledger=None, mode="shadow") == (
            message,
            None,
        )

    def test_heuristic_pass_skips_the_audit(self) -> None:
        calls = []

        def audit(message, citations):
            calls.append(message)
            return {}

        message = "The manual DOC-PS1 says isolate MCC-HW-01."
        out, assessment = evaluate_manual_grounding(
            message=message, ledger=_ledger_with_manuals(), mode="enforce", audit_call=audit
        )
        assert out == message
        assert assessment.heuristic_grounded is True
        assert assessment.would_downgrade is False
        assert calls == []

    def test_shadow_records_would_downgrade_without_changing_the_answer(self) -> None:
        def audit(message, citations):
            return {
                "claims": [{"text": "replace seal", "citation_ids": []}],
                "insufficient_evidence": False,
            }

        message = "Just replace the seal."
        out, assessment = evaluate_manual_grounding(
            message=message, ledger=_ledger_with_manuals(), mode="shadow", audit_call=audit
        )
        assert out == message
        assert assessment.would_downgrade is True
        assert assessment.downgraded is False
        assert assessment.audit_ran is True

    def test_enforce_downgrades_to_the_exact_template(self) -> None:
        def audit(message, citations):
            return {"claims": [], "insufficient_evidence": True}

        out, assessment = evaluate_manual_grounding(
            message="Just replace the seal.",
            ledger=_ledger_with_manuals(),
            mode="enforce",
            audit_call=audit,
        )
        assert out == DOWNGRADE_TEMPLATE.format(titles=_MANUAL_TITLE)
        assert assessment.downgraded is True

    def test_audit_cannot_authorize_unknown_chunk_ids(self) -> None:
        def audit(message, citations):
            # The auditor claims support from a chunk the server never returned.
            return {
                "claims": [{"text": "replace seal", "citation_ids": ["forged-chunk"]}],
                "insufficient_evidence": False,
            }

        out, assessment = evaluate_manual_grounding(
            message="Just replace the seal.",
            ledger=_ledger_with_manuals(),
            mode="enforce",
            audit_call=audit,
        )
        assert assessment.audit_grounded is False
        assert assessment.downgraded is True
        assert out.startswith("I found relevant sections in")

    def test_valid_audit_citations_keep_the_answer(self) -> None:
        def audit(message, citations):
            return {
                "claims": [{"text": "isolate first", "citation_ids": ["chunk-1"]}],
                "insufficient_evidence": False,
            }

        message = "Isolate power before opening the volute."
        out, assessment = evaluate_manual_grounding(
            message=message, ledger=_ledger_with_manuals(), mode="enforce", audit_call=audit
        )
        assert out == message
        assert assessment.audit_grounded is True
        assert assessment.would_downgrade is False

    def test_audit_outage_never_changes_behavior(self) -> None:
        def audit(message, citations):
            raise TimeoutError("audit deployment down")

        message = "Just replace the seal."
        for mode in ("shadow", "enforce"):
            out, assessment = evaluate_manual_grounding(
                message=message,
                ledger=_ledger_with_manuals(),
                mode=mode,
                audit_call=audit,
            )
            assert out == message, mode
            assert assessment.audit_error is True
            assert assessment.would_downgrade is False
            assert assessment.downgraded is False

    def test_identifier_report_uses_closure_and_observed_values(self) -> None:
        ledger = _ledger_with_manuals()
        ledger.record("machines.read", {"serial": "TC-INF-PS1-001"})

        def audit(message, citations):
            return {
                "claims": [{"text": "x", "citation_ids": ["chunk-1"]}],
                "insufficient_evidence": False,
            }

        message = "On TC-INF-PS1-001 clear AL-OVERTEMP and order QQ-INVENTED-1."
        _, assessment = evaluate_manual_grounding(
            message=message,
            ledger=ledger,
            mode="shadow",
            closure_values=frozenset({"AL-OVERTEMP"}),
            audit_call=audit,
        )
        assert assessment.ungrounded_identifiers == ("QQ-INVENTED-1",)


class TestGoldenCorpusReality:
    """The 1-manual corpus makes downgrade the DOMINANT path off PS1."""

    def test_other_machine_answer_without_manual_reference_would_downgrade(self) -> None:
        # A technician asks about the aeration blower; retrieval still returns
        # pump-station chunks (the only manual). An answer that never touches
        # them must show up in the shadow log.
        def audit(message, citations):
            return {
                "claims": [{"text": "blower", "citation_ids": []}],
                "insufficient_evidence": False,
            }

        message = "For the blower, check the inlet filter and coupling alignment."
        out, assessment = evaluate_manual_grounding(
            message=message, ledger=_ledger_with_manuals(), mode="shadow", audit_call=audit
        )
        assert out == message
        assert assessment.would_downgrade is True

    def test_ps1_answer_citing_the_manual_passes_heuristically(self) -> None:
        message = (
            "Per 20 Service / 20.2 Repair boundaries, isolate MCC-HW-01 and "
            "confirm wet-well level before entry."
        )
        _, assessment = evaluate_manual_grounding(
            message=message, ledger=_ledger_with_manuals(), mode="enforce"
        )
        assert assessment.heuristic_grounded is True


class TestLegacyTurnSeam:
    """The grounding seam in the legacy branch, end to end."""

    @staticmethod
    def _service_and_workflow(message_text: str):
        from ai.core.streaming import AGUIEvent, EventType
        from ai.core.tests.test_normalized_turn_service import (
            _Repository,
            _TestTurnService,
        )

        class _ManualsWorkflow:
            async def run_stream(self, **kwargs):
                # Simulate the middleware's post-dispatch capture: the tool
                # returned manual chunks during this run.
                record_tool_result("manuals.read:search_manuals", _manuals_result())
                await kwargs["emitter"].emit(
                    AGUIEvent(
                        event_type=EventType.RUN_STARTED,
                        thread_id=kwargs["thread_id"],
                        run_id="run-grounding",
                    )
                )
                yield message_text
                await kwargs["emitter"].emit(
                    AGUIEvent(
                        event_type=EventType.RUN_FINISHED,
                        thread_id=kwargs["thread_id"],
                        run_id="run-grounding",
                    )
                )

        repository = _Repository()
        service = _TestTurnService(
            workflow_factory=lambda: _ManualsWorkflow(),
            repository_factory=lambda actor, context: repository,  # noqa: ARG005
        )
        return service, repository

    @staticmethod
    def _run_turn(service):
        import asyncio

        from ai.core.tests.test_normalized_turn_service import _context, _principal

        return asyncio.run(
            service.process(
                actor=_principal(),
                thread_id="thread_grounding",
                content="What does the manual say about the pump?",
                modality="text",
                trusted_context=_context(),
                modality_metadata={},
                idempotency_key="grounding:one",
                correlation_id=_context().correlation_id,
            )
        )

    def test_shadow_persists_the_assessment_without_changing_the_answer(self) -> None:
        """Default mode: assessment in output_metadata, answer untouched.

        The real default audit call fails fast (no endpoint configured in
        tests), which exercises the outage rule on the true default path.
        """
        service, repository = self._service_and_workflow("Just replace the seal.")
        result = self._run_turn(service)
        assert result.message == "Just replace the seal."
        metadata = repository.terminal_calls[-1]["output_metadata"]
        grounding = metadata["grounding"]
        assert grounding["mode"] == "shadow"
        assert grounding["applied"] is True
        assert grounding["downgraded"] is False

    def test_enforce_downgrade_reaches_the_persisted_and_spoken_answer(self) -> None:
        """A confirmed-ungrounded answer is downgraded before the wrapper."""
        from types import SimpleNamespace

        service, repository = self._service_and_workflow("Just replace the seal.")
        settings = SimpleNamespace(
            manual_grounding_mode="enforce",
            chat_history_messages=0,
            chat_history_max_message_chars=0,
            chat_history_max_total_chars=0,
            feature_turn_usage_persistence=False,
        )
        with (
            patch("ai.core.config.get_settings", return_value=settings),
            patch(
                "ai.core.grounding._default_citation_audit",
                return_value={
                    "claims": [{"text": "seal", "citation_ids": []}],
                    "insufficient_evidence": False,
                },
            ),
        ):
            result = self._run_turn(service)
        assert result.message == DOWNGRADE_TEMPLATE.format(titles=_MANUAL_TITLE)
        grounding = repository.terminal_calls[-1]["output_metadata"]["grounding"]
        assert grounding["downgraded"] is True

    def test_grounded_answer_passes_untouched_in_enforce(self) -> None:
        """Heuristic grounding needs no audit and never modifies the text."""
        from types import SimpleNamespace

        message = "Per DOC-PS1 section 20.2, isolate MCC-HW-01 first."
        service, repository = self._service_and_workflow(message)
        settings = SimpleNamespace(
            manual_grounding_mode="enforce",
            chat_history_messages=0,
            chat_history_max_message_chars=0,
            chat_history_max_total_chars=0,
            feature_turn_usage_persistence=False,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            result = self._run_turn(service)
        assert result.message == message
        grounding = repository.terminal_calls[-1]["output_metadata"]["grounding"]
        assert grounding["heuristic_grounded"] is True


class TestMiddlewareCapture:
    """The invocation middleware records results post-dispatch, fail-soft."""

    def test_process_records_the_tool_result(self) -> None:
        import asyncio

        from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware

        ledger = bind_tool_captures()

        class _Context:
            function = None
            arguments: dict = {}  # noqa: RUF012
            result = _manuals_result(chunk_ids=("chunk-9",))

        async def _next(context):
            del context
            await asyncio.sleep(0)

        with (
            patch(
                "ai.core.tools.invocation_guard.tool_name",
                return_value="manuals.read:search_manuals",
            ),
            patch(
                "ai.core.tools.invocation_guard.authorize_invocation",
                return_value=None,
            ),
        ):
            asyncio.run(CapabilityInvocationMiddleware().process(_Context(), _next))

        assert [c["chunk_id"] for c in ledger.manuals_citations()] == ["chunk-9"]


class TestCrossMachineFence:
    """P8-W0a: a citation from the WRONG machine's manual forces the
    ungrounded path server-side — no heuristic pass, no LLM audit."""

    @staticmethod
    def _ledger(asset_id: str) -> ToolCaptureLedger:
        ledger = ToolCaptureLedger()
        ledger.record("manuals.read:search_manuals", _manuals_result(asset_id=asset_id))
        return ledger

    def test_wrong_machine_citation_would_downgrade_in_shadow(self) -> None:
        def audit(message, citations):  # pragma: no cover - must not run
            raise AssertionError("the fence must decide before any audit")

        message = f"Per {_MANUAL_TITLE}, replace the seal."
        out, assessment = evaluate_manual_grounding(
            message=message,
            ledger=self._ledger("SER-OTHER-MACHINE"),
            mode="shadow",
            turn_machine_serials=frozenset({"ser-ps1-001"}),
            audit_call=audit,
        )
        assert out == message
        assert assessment.would_downgrade is True
        assert assessment.downgraded is False
        assert assessment.audit_ran is False
        assert assessment.heuristic_grounded is False
        assert assessment.cross_machine_count == 2
        assert assessment.to_meta()["cross_machine_count"] == 2
        assert assessment.to_meta()["fence_armed"] is True

    def test_wrong_machine_citation_downgrades_in_enforce(self) -> None:
        from ai.core.i18n_templates import GROUNDING_CROSS_MACHINE, deterministic_template

        out, assessment = evaluate_manual_grounding(
            message=f"Per {_MANUAL_TITLE}, replace the seal.",
            ledger=self._ledger("SER-OTHER-MACHINE"),
            mode="enforce",
            turn_machine_serials=frozenset({"ser-ps1-001"}),
        )
        # The dedicated template never names (= endorses) the wrong manual.
        assert out == deterministic_template(GROUNDING_CROSS_MACHINE, "en")
        assert _MANUAL_TITLE not in out
        assert assessment.downgraded is True
        assert assessment.cross_machine_count == 2

    def test_matching_serial_takes_the_normal_path(self) -> None:
        message = f"Per {_MANUAL_TITLE}, isolate MCC-HW-01 first."
        out, assessment = evaluate_manual_grounding(
            message=message,
            ledger=self._ledger("SER-PS1-001"),
            mode="shadow",
            turn_machine_serials=frozenset({"ser-ps1-001"}),
        )
        assert out == message
        assert assessment.cross_machine_count == 0
        assert assessment.fence_armed is True
        assert assessment.heuristic_grounded is True

    def test_comparison_normalizes_case_and_whitespace(self) -> None:
        """Operator-entered asset_id stamps must not false-positive on
        case/whitespace drift (ingest is free text; serials are editable)."""
        message = f"Per {_MANUAL_TITLE}, isolate MCC-HW-01 first."
        out, assessment = evaluate_manual_grounding(
            message=message,
            ledger=self._ledger("  SER-ps1-001 "),
            mode="shadow",
            turn_machine_serials=frozenset({"ser-ps1-001"}),
        )
        assert out == message
        assert assessment.cross_machine_count == 0

    def test_empty_asset_id_never_mismatches(self) -> None:
        """Site-wide documents carry no machine identity — never fenced."""
        message = f"Per {_MANUAL_TITLE}, isolate MCC-HW-01 first."
        out, assessment = evaluate_manual_grounding(
            message=message,
            ledger=_ledger_with_manuals(),
            mode="shadow",
            turn_machine_serials=frozenset({"SER-PS1-001"}),
        )
        assert out == message
        assert assessment.cross_machine_count == 0

    def test_machine_serials_error_returns_empty_set(self) -> None:
        """A resolution error must DISABLE the fence, never half-arm it.

        The island has no assets app, so the fake module is injected: the
        first machine resolves (a serial IS collected), the second raises —
        the partial set must be discarded, not returned.
        """
        import sys
        import types

        from ai.core.grounding import machine_serials

        calls = {"n": 0}

        class _Machine:
            serial = "SER-PS1-001"

        def flaky(user, machine_id):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("transient")
            return _Machine()

        fake_pkg = types.ModuleType("assets")
        fake_mod = types.ModuleType("assets.ai_read")
        fake_mod.authorized_machine = flaky
        fake_pkg.ai_read = fake_mod
        with patch.dict(sys.modules, {"assets": fake_pkg, "assets.ai_read": fake_mod}):
            result = machine_serials(object(), [1, 2, 3])
        assert calls["n"] == 2
        assert result == frozenset()

    def test_machine_serials_normalizes_and_collects(self) -> None:
        import sys
        import types

        from ai.core.grounding import machine_serials

        class _Machine:
            def __init__(self, serial):
                self.serial = serial

        machines = {1: _Machine(" SER-PS1-001 "), 2: _Machine(""), 3: None}
        fake_pkg = types.ModuleType("assets")
        fake_mod = types.ModuleType("assets.ai_read")
        fake_mod.authorized_machine = lambda _user, mid: machines.get(mid)
        fake_pkg.ai_read = fake_mod
        with patch.dict(sys.modules, {"assets": fake_pkg, "assets.ai_read": fake_mod}):
            result = machine_serials(object(), [1, 2, 3])
        assert result == frozenset({"ser-ps1-001"})

    def test_fence_inert_without_turn_machines(self) -> None:
        """No authorized machines this turn -> the fence cannot apply."""
        message = f"Per {_MANUAL_TITLE}, isolate MCC-HW-01 first."
        out, assessment = evaluate_manual_grounding(
            message=message,
            ledger=self._ledger("SER-OTHER-MACHINE"),
            mode="shadow",
            turn_machine_serials=frozenset(),
        )
        assert out == message
        assert assessment.cross_machine_count == 0
        assert assessment.fence_armed is False
        assert assessment.heuristic_grounded is True
