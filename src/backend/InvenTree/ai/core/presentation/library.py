"""Verify the packaged, versioned library artifacts without source-tree paths."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def library_available() -> bool:
    """Missing or changed release artifacts disable optional generation."""
    try:
        root = files("ai.core.presentation").joinpath("generated")
        manifest = json.loads(root.joinpath("manifest.json").read_text())
        if (manifest["renderer"], manifest["cli"], manifest["library"], manifest["zod"]) != (
            "1.0.1",
            "0.6.0",
            "aimms-chat@1",
            "4.6.7",
        ):
            return False
        expected = {"chat-library.txt", "chat-library.spec.json"}
        if set(manifest["artifacts"]) != expected:
            return False
        return all(
            hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
            == manifest["artifacts"][name]
            for name in expected
        )
    except Exception:
        return False
