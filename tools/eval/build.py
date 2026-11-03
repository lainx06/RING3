"""Eval builds (T-05a): the committed HEAD, exported to build/eval/src and built there.

Why a copy: other builders edit kernel/ and bridge/ in the same checkout and run `make` on the same build/
tree; the evals must measure one committed source, not a half-edited working tree. The copy is `git archive
HEAD`, so uncommitted changes (including this card's own) are never part of the measured build.

Also builds the host policy for any recorded policy SHA-256 (E5): the policy sources are hashed exactly as the
Makefile does (policy.h + policy.c + variant_<v>.h) at every commit that touched kernel/policy/, and the
commit whose hash matches is exported and compiled for the host, so a tape replays on the policy that ran.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL_BUILD = REPO / "build" / "eval"
SRC = EVAL_BUILD / "src"
POLICY_BUILDS = EVAL_BUILD / "policy"
HOST_CFLAGS = ["-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


@dataclass(frozen=True)
class Images:
    commit: str  # short hash of the exported commit
    src: Path
    kernel: Path  # production image, policy default (E3)
    kernel_permissive: Path  # production image, permissive policy (E3)
    test: Path  # test image, default (E1, E2)
    test_permissive: Path  # test image, permissive (E1, E2)
    policy_sha256: str
    policy_sha256_permissive: str


def policy_hash_at(commit: str, variant: str = "default") -> str | None:
    files = ("kernel/policy/policy.h", "kernel/policy/policy.c", f"kernel/policy/variant_{variant}.h")
    h = hashlib.sha256()
    for f in files:
        p = subprocess.run(["git", "show", f"{commit}:{f}"], cwd=REPO, capture_output=True, check=False)
        if p.returncode != 0:
            return None
        h.update(p.stdout)
    return h.hexdigest()


def export_head() -> str:
    commit = git("rev-parse", "--short", "HEAD")
    if SRC.exists():
        shutil.rmtree(SRC)
    SRC.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=REPO, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(SRC)], input=archive, check=True)
    return commit


def build_images(log) -> Images:
    commit = export_head()
    log(f"eval build: HEAD {commit} exported to {SRC.relative_to(REPO)}")
    for args in ([], ["POLICY=permissive"], ["TEST=1"], ["TEST=1", "POLICY=permissive"], ["host-policy"]):
        subprocess.run(
            ["make", "-s", "-C", str(SRC), f"BUILD_HASH={commit}", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        log(f"  make {' '.join(args) or '(production)'}: ok")
    b = SRC / "build"
    return Images(
        commit=commit,
        src=SRC,
        kernel=b / "kernel.elf",
        kernel_permissive=b / "kernel-permissive.elf",
        test=b / "kernel-test.elf",
        test_permissive=b / "kernel-test-permissive.elf",
        policy_sha256=policy_hash_at(commit) or "",
        policy_sha256_permissive=policy_hash_at(commit, "permissive") or "",
    )


def policy_commits() -> list[str]:
    """Every commit that touched kernel/policy/, newest first (full hashes)."""
    return git("log", "--format=%H", "--", "kernel/policy").split()


def host_policy_for(sha256: str, variant: str = "default") -> tuple[Path, str] | None:
    """(host policy_test binary, short commit) built from the commit whose policy sources hash to `sha256`;
    None if no commit matches."""
    for c in policy_commits():
        if policy_hash_at(c, variant) == sha256:
            short = c[:7]
            out = POLICY_BUILDS / f"{sha256[:12]}-{variant}"
            exe = out / "policy_test"
            if not exe.exists():
                if out.exists():
                    shutil.rmtree(out)
                out.mkdir(parents=True)
                paths = ["kernel/policy", "tests/host/policy_test.c"]
                archive = subprocess.run(
                    ["git", "archive", c, *paths], cwd=REPO, capture_output=True, check=True
                ).stdout
                subprocess.run(["tar", "-x", "-C", str(out)], input=archive, check=True)
                subprocess.run(
                    [
                        "cc",
                        *HOST_CFLAGS,
                        "-Ikernel/policy",
                        f'-DPOLICY_VARIANT_HEADER="variant_{variant}.h"',
                        "tests/host/policy_test.c",
                        "kernel/policy/policy.c",
                        "-o",
                        "policy_test",
                    ],
                    cwd=out,
                    check=True,
                    capture_output=True,
                )
            return exe, short
    return None
