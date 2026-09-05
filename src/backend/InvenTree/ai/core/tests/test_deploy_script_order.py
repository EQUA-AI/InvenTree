"""CR-9: the deploy script keeps the schema-protocol order (web -> smoke -> worker -> traffic).

The script and its bash test live under ``LocalDocs/scripts`` (an operator
directory that is not in the git tree), so this wrapper runs the ordering test
when the checkout carries them and skips honestly otherwise.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

_TEST = (
    pathlib.Path(__file__).resolve().parents[6]
    / "LocalDocs"
    / "scripts"
    / "tests"
    / "test_aimms_deploy_order.sh"
)


@pytest.mark.skipif(not _TEST.exists(), reason="LocalDocs/scripts is not part of this checkout")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_deploy_script_keeps_the_schema_protocol_order() -> None:
    """The stub-driven bash test must print PASS."""
    completed = subprocess.run(
        ["bash", str(_TEST)], capture_output=True, text=True, timeout=120, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout
