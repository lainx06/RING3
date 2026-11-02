"""Versioned context packs (docs/context-packs/agent-vN.md). SHA-256 of the exact bytes goes in run_start."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ring3bridge.config import REPO

PACK_DIR = REPO / "docs" / "context-packs"
CURRENT = "agent-v1"


@dataclass(frozen=True)
class ContextPack:
    name: str
    text: str
    sha256: str


def load(name: str = CURRENT) -> ContextPack:
    if not name.replace("-", "").isalnum():
        raise ValueError("bad context pack name")
    data = (PACK_DIR / f"{name}.md").read_bytes()
    return ContextPack(name, data.decode("ascii"), hashlib.sha256(data).hexdigest())
