"""`make evals` (T-05a): run the free suites on the RING3 and permissive builds, write docs/evals/raw/*.json,
render docs/evals/results.md from the raw files.

    uv run python tools/eval/run_all.py                 # build HEAD, run E1 E2 E3 E5 E6, render
    uv run python tools/eval/run_all.py --suites e5,e6  # only these (the others' raw files are kept)
    uv run python tools/eval/run_all.py --render-only   # re-render results.md from the raw files

No network, no key, no model call: E4 (paid) has its own script (e4_agent.py) and the user runs it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import render  # noqa: E402

import build  # noqa: E402

RAW = build.REPO / "docs" / "evals" / "raw"
ALL = ("e1", "e2", "e3", "e5", "e6")


def log(msg: str) -> None:
    print(msg, flush=True)


def write_raw(name: str, data: dict) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="ascii")
    log(f"wrote docs/evals/raw/{name}.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_all.py")
    ap.add_argument("--suites", default=",".join(ALL), help=f"comma-separated subset of {','.join(ALL)}")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--e1-runs", type=int, default=5)
    ap.add_argument("--e3-frames", type=int, default=10000)
    a = ap.parse_args(argv)

    if not a.render_only:
        wanted = [s.strip() for s in a.suites.split(",") if s.strip()]
        bad = [s for s in wanted if s not in ALL]
        if bad:
            ap.error(f"unknown suite(s): {bad}")
        images = None
        if any(s in wanted for s in ("e1", "e2", "e3", "e5")):
            images = build.build_images(log)
            # tests.qemu and ring3bridge from the exported copy, so the harness matches the measured source
            sys.path[:0] = [str(images.src), str(images.src / "bridge")]
        import suites

        meta = {}
        if images is not None:
            meta = {
                "commit": images.commit,
                "policy_sha256": {
                    "RING3": images.policy_sha256,
                    "permissive": images.policy_sha256_permissive,
                },
            }
        for s in wanted:
            log(f"== {s.upper()}")
            if s == "e1":
                data = suites.e1_faults(images, a.e1_runs, log)
            elif s == "e2":
                data = suites.e2_syscalls(images, log)
            elif s == "e3":
                data = suites.e3_link_fuzz(images, log, frames=a.e3_frames)
            elif s == "e5":
                data = suites.e5_replay(images, log)
            else:
                data = suites.e6_cell()
            if meta and s != "e6":
                data["build"] = meta
            write_raw(s, data)

    text = render.render(RAW)
    out = build.REPO / "docs" / "evals" / "results.md"
    out.write_text(text, encoding="utf-8")
    log(f"rendered docs/evals/results.md ({len(text.splitlines())} lines)")
    failures = render.must_pass_failures(RAW)
    for f in failures:
        log(f"MUST-PASS FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
