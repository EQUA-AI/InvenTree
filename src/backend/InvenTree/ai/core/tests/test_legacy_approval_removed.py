"""Phase C has one governed decision rail, no retired routes or context grants."""

import ast
from pathlib import Path


def test_removed_legacy_routes_and_context_types():
    root = Path(__file__).resolve().parents[1]
    app = ast.parse((root / "app.py").read_text())
    names = {
        node.name
        for node in ast.walk(app)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not any("respond_to_" in name and name != "respond_to_question" for name in names)
    base = ast.parse((root / "tools/inventree/base.py").read_text())
    assert not any(
        isinstance(node, ast.ClassDef) and node.name.endswith(("Context", "PendingError"))
        for node in ast.walk(base)
    )


def test_confirmation_marker_confers_no_authority():
    from ai.core.tools.inventree.base import require_confirmation

    async def write_example(value):  # noqa: RUF029 -- model-visible tools use a coroutine ABI
        return value

    declared = require_confirmation("Example write", ["value"])(write_example)
    assert declared is write_example
    assert declared._requires_confirmation is True
    assert declared._confirmation_display_fields == ["value"]


def test_legacy_identifiers_are_absent_from_backend_ai_sources():
    marker = ("HI" + "TL").lower()
    root = Path(__file__).resolve().parents[2]
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            name = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else getattr(node, "name", "")
            )
            if isinstance(name, str):
                assert marker not in name.lower(), (path, name)
