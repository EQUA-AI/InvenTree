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


def test_write_packs_carry_no_selection_terms() -> None:
    """Selection scores read packs only; a write pack must not be phrase-reachable."""
    from ai.core.tools.capabilities import _PACK_SPECS, ToolEffect

    for pack_id, (effect, _tools, terms) in _PACK_SPECS.items():
        if effect is ToolEffect.WRITE and pack_id not in {"email.write", "kanban.write"}:
            assert terms == (), f"{pack_id} exposes selection terms"
