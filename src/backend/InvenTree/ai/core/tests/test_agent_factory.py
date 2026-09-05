"""M1 PR A: one agent constructor, explicit replay seams, no stray toolsets.

AST pins (plan §9.4 double-replay invariants): ``ChatAgent(`` and
``AzureOpenAIChatClient(`` appear only in the factory, the client builder
and the memory adapter; the factory always passes ``context_providers`` and
``chat_message_store_factory`` explicitly; a constructor toolset exists only
on wf1's legacy agents; every catalogued tool rail carries the capability
middleware; nothing calls ``agent.run(thread=...)``.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest
from ai.core.agents import factory

CORE = pathlib.Path(factory.__file__).resolve().parents[1]
ALLOWED_CONSTRUCTOR_FILES = {
    CORE / "agents" / "factory.py",
    CORE / "integrations" / "azure_openai_client.py",
}
ALLOWED_CONSTRUCTOR_DIRS = (CORE / "memory" / "maf_adapter",)
RAILS_WITH_MIDDLEWARE = {
    "wf2_parts_analysis.py",
    "wf3_research.py",
    "wf4_procurement.py",
    "wf6_documents.py",
    "wf8_lookup.py",
    "wf9_rag_retrieval.py",
}


def _source_files():
    for path in sorted(CORE.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


def _calls(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                yield node


def test_chat_agent_and_client_are_constructed_only_in_the_factory():
    offenders = []
    for path in _source_files():
        if path in ALLOWED_CONSTRUCTOR_FILES or any(
            d in path.parents for d in ALLOWED_CONSTRUCTOR_DIRS
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in ("ChatAgent", "AzureOpenAIChatClient"):
            offenders.extend(
                f"{path.relative_to(CORE)}:{c.lineno} {name}(" for c in _calls(tree, name)
            )
    assert offenders == [], offenders


def test_the_factory_passes_the_replay_seams_explicitly():
    tree = ast.parse(pathlib.Path(factory.__file__).read_text(encoding="utf-8"))
    calls = list(_calls(tree, "ChatAgent"))
    assert len(calls) == 1
    keywords = {kw.arg for kw in calls[0].keywords}
    assert {"context_providers", "chat_message_store_factory", "middleware", "tools"} <= keywords
    store = next(kw for kw in calls[0].keywords if kw.arg == "chat_message_store_factory")
    assert isinstance(store.value, ast.Constant) and store.value.value is None


def test_constructor_toolsets_exist_only_on_wf1():
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _calls(tree, "AgentSpec"):
            keywords = {kw.arg for kw in call.keywords}
            if "constructor_tools" in keywords:
                assert path.name == "wf1_diagnostics.py", f"{path.name}:{call.lineno}"
            if path.name in RAILS_WITH_MIDDLEWARE:
                assert "middleware" in keywords, f"{path.name}:{call.lineno} lacks middleware"
            assert "context_providers" not in keywords, (
                f"{path.name}:{call.lineno}: context providers arrive with the memory adapter (PR C+)"
            )


def test_no_rail_runs_an_agent_with_a_thread():
    """``agent.run(thread=...)`` would let the SDK replay history on its own."""
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "run_stream")
            ):
                assert "thread" not in {kw.arg for kw in node.keywords}, (
                    f"{path.name}:{node.lineno}"
                )


def test_build_agent_refuses_a_toolset_outside_wf1(monkeypatch):
    monkeypatch.setattr(factory, "build_chat_client", lambda *_a, **_k: object())
    with pytest.raises(ValueError, match="run_with_rbac"):
        factory.build_agent(
            factory.AgentSpec(
                deployment="d",
                instructions="i",
                name="n",
                workflow="wf8",
                constructor_tools=(object(),),
            )
        )


def test_build_agent_applies_the_spec(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clients = []

    def fake_client(deployment, *, max_iterations=None, include_detailed_errors=None):
        clients.append((deployment, max_iterations, include_detailed_errors))
        return "client"

    monkeypatch.setattr(factory, "ChatAgent", FakeAgent)
    monkeypatch.setattr(factory, "build_chat_client", fake_client)
    middleware = object()
    agent = factory.build_agent(
        factory.AgentSpec(
            deployment="gpt-x",
            instructions="be brief",
            name="Agent",
            description="",
            middleware=middleware,
            max_tool_iterations=8,
            include_detailed_errors=False,
            workflow="wf8",
        )
    )
    assert isinstance(agent, FakeAgent)
    assert clients == [("gpt-x", 8, False)]
    assert captured["chat_client"] == "client"
    assert captured["description"] is None
    assert captured["tools"] is None
    assert captured["context_providers"] is None
    assert captured["chat_message_store_factory"] is None
    assert captured["middleware"] is middleware


def test_client_builder_applies_invocation_limits(monkeypatch):
    from ai.core.integrations import azure_openai_client as builder

    config = SimpleNamespace(max_iterations=40, include_detailed_errors=True)

    class FakeClient:
        function_invocation_config = config

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(builder, "AzureOpenAIChatClient", FakeClient)
    monkeypatch.setattr(
        builder,
        "get_settings",
        lambda: SimpleNamespace(
            azure_openai_endpoint="https://example.invalid", azure_openai_api_key="k"
        ),
    )
    client = builder.build_chat_client("dep", max_iterations=3, include_detailed_errors=False)
    assert client.kwargs["deployment_name"] == "dep"
    assert config.max_iterations == 3 and config.include_detailed_errors is False
    # None leaves the SDK defaults alone.
    builder.build_chat_client("dep")
    assert config.max_iterations == 3


def test_prompt_cache_options_are_dark_by_default(monkeypatch):
    monkeypatch.setattr(
        factory, "get_settings", lambda: SimpleNamespace(aimms_prompt_cache_key_deployments="")
    )
    assert (
        factory.prompt_cache_options("gpt-5.1", client_code="c", thread_id="t", mode="default")
        == {}
    )


def test_prompt_cache_options_ride_only_listed_deployments(monkeypatch):
    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(aimms_prompt_cache_key_deployments="gpt-5.1, gpt-5.6-luna"),
    )
    assert factory.prompt_cache_options(
        "gpt-5.1", client_code="acme", thread_id="t1", mode="voice"
    ) == {"prompt_cache_key": "acme:t1:voice"}
    assert (
        factory.prompt_cache_options("gpt-4.1", client_code="acme", thread_id="t1", mode="voice")
        == {}
    )
    assert (
        factory.prompt_cache_options("gpt-5.1", client_code="acme", thread_id="", mode="voice")
        == {}
    )
    with pytest.raises(ValueError):
        factory.prompt_cache_options("gpt-5.1", client_code="acme", thread_id="t1", mode="bogus")
