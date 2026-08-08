"""Universal capability enforcement across every rail (execution-plan S11).

Before this slice the invocation guard covered wf8 only: wf2-wf6 ran with the
per-user RBAC list filter as their sole boundary, eight of eleven
``run_with_rbac`` call sites silently omitted ``context`` (disabling the voice
read-only narrowing), and two ``.as_agent()`` paths attached constructor
toolsets that bypassed the filter entirely. These tests pin the invariants that
close those holes and, crucially, would fail if a NEW workflow forgot any of
them.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from ai.core.tools.capabilities import capability_catalog, pack_workflows
from ai.core.workflows import rbac_run
from ai.core.workflows.registry import get_workflow_registry

WORKFLOW_DIR = Path(rbac_run.__file__).parent

#: Rails whose tools are dispatched by a registry other than the MAF catalog.
#: wf1 is tool-less by construction; wf7 dispatches through the diagnostic
#: registry, which re-authorizes every read against the actor's record roots.
_NON_CATALOG_WORKFLOWS = {"wf1", "wf7"}


def _registered_workflow_ids() -> set[str]:
    registry = get_workflow_registry()
    ids = set(registry._workflows)
    assert ids, "workflow registry exposed no definitions"
    return ids


def test_every_registered_workflow_is_either_catalogued_or_documented_toolless() -> None:
    """A workflow absent from the catalog is denied ``workflow_not_allowed``.

    That is the failure mode this test exists to prevent: attaching the
    middleware to a rail whose tools nobody catalogued denies its every call.
    """
    catalogued = {workflow for entry in capability_catalog() for workflow in entry.workflows}
    for workflow_id in _registered_workflow_ids():
        if workflow_id in _NON_CATALOG_WORKFLOWS:
            assert workflow_id not in catalogued, (
                f"{workflow_id} is documented tool-less but has catalog entries"
            )
            continue
        assert workflow_id in catalogued, (
            f"{workflow_id} has no capability catalog entries; attaching the "
            "invocation middleware would deny its every call"
        )


def test_wf5_is_retired_from_the_registry() -> None:
    """wf5 CPQ contradicts the client-scoped fork and ran outside RBAC."""
    assert "wf5" not in _registered_workflow_ids()


@pytest.mark.parametrize(
    "path",
    [
        WORKFLOW_DIR / "devui_adapters_v2.py",
        WORKFLOW_DIR.parent.parent / "run_devui.py",
    ],
)
def test_wf5_is_not_reachable_through_devui(path: Path) -> None:
    """Retiring a rail includes development entry points that execute it.

    The production registry no longer exposed wf5, but the adapters and the
    runner's manual fallback instantiated CPQ directly, preserving the
    retired path and its fabricated ``CFG-*``/``Q-*`` identifiers. The v1
    adapter and ``wf5_cpq.py`` itself were deleted in S15; import-absence is
    pinned in ``test_cache_safety_rules``.
    """
    source = path.read_text().casefold()
    assert "wf5" not in source, path
    assert "cpq" not in source, path


def test_run_with_rbac_requires_a_workflow_and_binds_the_run() -> None:
    """The bind point is the helper, so a call site cannot forget it."""
    signature = inspect.signature(rbac_run.run_with_rbac)
    workflow_param = signature.parameters["workflow"]
    assert workflow_param.default is inspect.Parameter.empty
    assert workflow_param.kind is inspect.Parameter.KEYWORD_ONLY
    source = inspect.getsource(rbac_run.run_with_rbac)
    assert "bind_capability_run" in source


@pytest.mark.parametrize(
    "module_name",
    ["wf2_parts_analysis", "wf3_research", "wf4_procurement", "wf6_documents"],
)
def test_no_call_site_omits_workflow_or_context(module_name: str) -> None:
    """AST guard: every ``run_with_rbac`` call names both keywords.

    Omitting ``context`` is invisible at runtime — it simply defaults the turn
    to text and hands a voice turn the full write toolset.
    """
    tree = ast.parse((WORKFLOW_DIR / f"{module_name}.py").read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_with_rbac"
    ]
    assert calls, f"{module_name} has no run_with_rbac call to check"
    for call in calls:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "workflow" in keywords, f"{module_name}:{call.lineno} omits workflow"
        assert "context" in keywords, f"{module_name}:{call.lineno} omits context"


@pytest.mark.parametrize(
    "module_name",
    [
        "wf2_parts_analysis",
        "wf3_research",
        "wf4_procurement",
        "wf6_documents",
        "wf8_lookup",
    ],
)
def test_no_agent_is_constructed_with_a_toolset(module_name: str) -> None:
    """A constructor toolset is unioned into every run, bypassing the filter."""
    tree = ast.parse((WORKFLOW_DIR / f"{module_name}.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ChatAgent"
        ):
            keywords = {keyword.arg for keyword in node.keywords}
            assert "tools" not in keywords, (
                f"{module_name}:{node.lineno} attaches a constructor toolset, "
                "which bypasses run_with_rbac"
            )
            assert "middleware" in keywords, (
                f"{module_name}:{node.lineno} builds an agent without the "
                "capability invocation middleware"
            )


def test_specialist_write_packs_are_not_reachable_from_wf8() -> None:
    """The everyday chat rail must not gain specialist write capability."""
    for pack_id in ("parts.write", "stock.write", "company.write", "sales.write"):
        assert "wf8" not in pack_workflows(pack_id)
        assert "general" not in pack_workflows(pack_id)
    assert pack_workflows("procurement.write") == frozenset({"wf4"})


def test_every_effectful_pack_has_an_explicit_fail_closed_workflow_map() -> None:
    """A new or misspelled write pack must not silently gain every rail.

    The former grant-all fallback authorized email writes from procurement and
    kanban writes from parts analysis. Read packs are intentionally shared;
    every externally effectful pack needs a deliberate map.
    """
    from ai.core.tools.capabilities import _PACK_SPECS, _PACK_WORKFLOWS, ToolEffect

    effectful = {
        pack_id
        for pack_id, (effect, _tools, _terms) in _PACK_SPECS.items()
        if effect is not ToolEffect.READ
    }
    assert effectful <= _PACK_WORKFLOWS.keys()
    assert pack_workflows("email.write") == frozenset({"wf3", "wf8", "general"})
    assert pack_workflows("misspelled.write") == frozenset()


def test_write_packs_carry_no_selection_terms() -> None:
    """Selection scores read packs only; a write pack must not be phrase-reachable."""
    from ai.core.tools.capabilities import _PACK_SPECS, ToolEffect

    for pack_id, (effect, _tools, terms) in _PACK_SPECS.items():
        if effect is ToolEffect.WRITE and pack_id not in {"email.write", "kanban.write"}:
            assert terms == (), f"{pack_id} exposes selection terms"


def test_identity_failures_are_never_downgraded_to_shadow() -> None:
    """Shadow mode softens workflow coverage, never "who is calling".

    ``missing_run_context`` carries no run context, so its workflow is
    ``None`` — which is in no enforced set. Keying the shadow decision on the
    workflow alone therefore logged an UNBOUND run and dispatched the tool
    anyway: the exact inverse of the guarantee ``run_with_rbac`` relies on.
    """
    from ai.core.tools.invocation_guard import _NEVER_SHADOWED

    assert {
        "missing_run_context",
        "stale_catalog",
        "missing_principal",
        "principal_mismatch",
    } <= _NEVER_SHADOWED


def test_enforced_workflows_cannot_be_emptied_by_configuration() -> None:
    """A blank or malformed setting must not make the guard advisory on wf8.

    The soaked rails are a floor, not an operator-erasable default: an empty
    value previously yielded an empty set, and since the middleware re-raises
    only when enforced, the entire guard would have gone advisory on the one
    rail it was already protecting.
    """
    from unittest.mock import patch

    from ai.core.tools.invocation_guard import _enforced_workflows

    for raw in ("", "   ", ",,", "typo_wf8"):
        with patch("ai.core.config.get_settings") as settings:
            settings.return_value.capability_broker_enforced_workflows = raw
            enforced = _enforced_workflows()
        assert "wf8" in enforced, raw
        assert "general" in enforced, raw


def test_a_newly_enforced_workflow_can_be_added_by_configuration() -> None:
    """The floor must not prevent widening enforcement to a specialist rail."""
    from unittest.mock import patch

    from ai.core.tools.invocation_guard import _enforced_workflows

    with patch("ai.core.config.get_settings") as settings:
        settings.return_value.capability_broker_enforced_workflows = "wf8,general,wf4"
        enforced = _enforced_workflows()
    assert "wf4" in enforced


def test_every_catalogued_rail_is_enforced_by_default() -> None:
    """Enforcement everywhere is the shipped default, not an env-only state.

    The 2026-08-06 flip observed zero denials across the soak; if the default
    regressed to the wf8/general floor, removing the env var (routine config
    hygiene) would silently drop wf2/wf3/wf4/wf6 back to advisory.
    """
    from ai.core.config import Settings

    settings = Settings(_env_file=None)
    configured = {
        token.strip()
        for token in settings.capability_broker_enforced_workflows.split(",")
        if token.strip()
    }
    assert configured == {"wf8", "general", "wf2", "wf3", "wf4", "wf6"}
