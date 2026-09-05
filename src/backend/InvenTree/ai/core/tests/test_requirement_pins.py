"""M1 pin ledger (plan of record §9.7 / GR-26): the four pin files agree.

``src/backend/requirements.in`` is the ONLY admission point for anything the
application imports; the hashed ``requirements.txt`` the image installs is
compiled from it. ``ai/requirements.txt`` (installed unhashed afterwards) and
``ai/pyproject.toml`` must repeat the agent-framework pin byte-for-byte, and
no mem0 distribution may appear anywhere (posture B ships dark until M3a).
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[6]
REQUIREMENTS_IN = REPO / "src" / "backend" / "requirements.in"
REQUIREMENTS_TXT = REPO / "src" / "backend" / "requirements.txt"
REQUIREMENTS_314 = REPO / "src" / "backend" / "requirements-3.14.txt"
AI_REQUIREMENTS = REPO / "src" / "backend" / "InvenTree" / "ai" / "requirements.txt"
AI_PYPROJECT = REPO / "src" / "backend" / "InvenTree" / "ai" / "pyproject.toml"
DOCKERFILE = REPO / "contrib" / "container" / "Dockerfile"

MAF_CORE_PIN = "agent-framework-core==1.0.0b251120"
MAF_DEVUI_PIN = "agent-framework-devui==1.0.0b251120"
FORBIDDEN_DISTRIBUTIONS = ("mem0ai", "agent-framework-mem0")


def _pins(path: pathlib.Path) -> dict[str, str]:
    """``name -> version`` for every ``name==version`` line (comments stripped)."""
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([A-Za-z0-9_.\-]+)$", line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_the_agent_framework_pin_is_identical_in_all_three_files():
    assert _pins(REQUIREMENTS_IN)["agent-framework-core"] == "1.0.0b251120"
    assert _pins(REQUIREMENTS_IN)["agent-framework-devui"] == "1.0.0b251120"
    assert _pins(AI_REQUIREMENTS)["agent-framework-core"] == "1.0.0b251120"
    assert _pins(AI_REQUIREMENTS)["agent-framework-devui"] == "1.0.0b251120"
    pyproject = AI_PYPROJECT.read_text(encoding="utf-8")
    assert f'"{MAF_CORE_PIN}"' in pyproject
    assert f'"{MAF_DEVUI_PIN}"' in pyproject
    # The meta-package (which drags in every optional integration, mem0
    # included) must never come back.
    assert not re.search(r'"agent-framework[>=<]', pyproject)
    assert not re.search(r'"agent-framework-azure[>=<]', pyproject)


def test_tiktoken_is_admitted_in_requirements_in_only():
    """App code imports tiktoken (ai/core/usage.py) -> hashed manifest, not ai/."""
    assert _pins(REQUIREMENTS_IN)["tiktoken"] == "0.14.0"
    assert "tiktoken" not in _pins(AI_REQUIREMENTS)
    assert "tiktoken" not in AI_PYPROJECT.read_text(encoding="utf-8")


def test_the_compiled_manifests_carry_the_new_pins_with_hashes():
    for compiled in (REQUIREMENTS_TXT, REQUIREMENTS_314):
        text = compiled.read_text(encoding="utf-8")
        pins = _pins(compiled)
        assert pins.get("tiktoken") == "0.14.0", compiled.name
        assert pins.get("pydantic") == _pins(REQUIREMENTS_IN)["pydantic"], compiled.name
        assert pins.get("pydantic-settings") == _pins(REQUIREMENTS_IN)["pydantic-settings"]
        block = text.split("tiktoken==0.14.0", 1)[1].split("\n\n", 1)[0]
        assert "--hash=sha256:" in block, f"{compiled.name}: tiktoken is unhashed"


def test_pydantic_pins_are_governed_by_the_maf_core_pin():
    """The retroactive pydantic pins name their governor in the comment."""
    for line in REQUIREMENTS_IN.read_text(encoding="utf-8").splitlines():
        if line.startswith(("pydantic==", "pydantic-settings==")):
            assert MAF_CORE_PIN in line, line


def test_no_mem0_distribution_anywhere():
    for path in (
        REQUIREMENTS_IN,
        REQUIREMENTS_TXT,
        REQUIREMENTS_314,
        AI_REQUIREMENTS,
        AI_PYPROJECT,
    ):
        text = path.read_text(encoding="utf-8").lower()
        for name in FORBIDDEN_DISTRIBUTIONS:
            assert not re.search(rf"^\s*{re.escape(name)}\b", text, re.MULTILINE), (
                f"{path.name} admits {name}"
            )
    # The image never installs a candidate overlay (GR-26).
    assert "requirements-mem0-candidate" not in DOCKERFILE.read_text(encoding="utf-8")
