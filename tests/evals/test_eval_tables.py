"""T-05a: the eval tables are generated, not typed; the parsers read the recorded evidence correctly; the
replay bundles verify and replay with no key; the scrub scan catches what it must. Offline: no QEMU boot, no
network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from tests.qemu import REPO

sys.path.insert(0, str(REPO / "tools" / "eval"))

import bundles  # noqa: E402
import render  # noqa: E402
import suites  # noqa: E402

RAW = REPO / "docs" / "evals" / "raw"
RESULTS = REPO / "docs" / "evals" / "results.md"
BANNED = __import__("re").compile(r"\b(success\w*|secure\w*|safe|safely|real-?time)\b", 2)


def test_results_md_is_rendered_from_raw():
    """A1: results.md is exactly what the renderer makes from the committed raw files (no hand edits)."""
    assert render.render(RAW) == RESULTS.read_text("utf-8")


def test_render_is_deterministic():
    assert render.render(RAW) == render.render(RAW)


def test_results_md_wording():
    text = RESULTS.read_text("utf-8")
    assert not BANNED.search(text), BANNED.search(text)
    assert "success" not in text.lower()


def test_must_pass_rows_hold():
    """A2/A3/A5: the must-pass rows on the RING3 build hold in the committed raw files."""
    assert render.must_pass_failures(RAW) == []


def test_must_pass_detects_a_failure(tmp_path):
    """The check is not vacuous: one E1 run that ended EXIT instead of PROC_FAULT, and one replayable tape
    that mismatches, are both reported."""
    for f in RAW.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    e1 = json.loads((tmp_path / "e1.json").read_text())
    e1["builds"]["RING3"]["programs"]["null"][0]["end"] = "EXIT"
    (tmp_path / "e1.json").write_text(json.dumps(e1))
    e5 = json.loads((tmp_path / "e5.json").read_text())
    t = next(t for t in e5["tapes"] if not t.get("late_link") and not t.get("missing"))
    t["recorded"] = "MISMATCH"
    (tmp_path / "e5.json").write_text(json.dumps(e5))
    fails = render.must_pass_failures(tmp_path)
    assert any(f.startswith("E1: null") for f in fails) and any(f.startswith("E5: ") for f in fails), fails
    assert "FAILED" in render.render(tmp_path)


def test_late_link_runs_are_listed_not_hidden():
    """A3 / D-70: T-04 runs 1-2 appear with the reason and are not counted as replayed."""
    e5 = json.loads((RAW / "e5.json").read_text())
    late = [t for t in e5["tapes"] if t.get("late_link")]
    assert {t["source"] for t in late} == {"runs/20261103-153922", "runs/20261103-154507"}
    text = RESULTS.read_text("utf-8")
    assert text.count("not replayable (late link, D-70)") == 2


def test_e6_parsers_on_recorded_transcripts():
    """The E6 numbers come from the board's own t_ms on the recorded T-04 tapes (values the T-04 README cites:
    AUTO_HALT in the same tick and frame; LINK_LOST + fan 0 at t_ms 2466)."""
    d = suites.e6_cell()
    i, e, w = d["kernel_hw"]["interlock"], d["kernel_hw"]["estop"], d["kernel_hw"]["watchdog"]
    assert i["auto_halt"] and i["kernel_ticks_after_frame"] == 0 and i["board_fan0_ms_after_hand"] == 0
    assert i["frames_after_cut_fan_gt0"] == 0
    assert e["auto_halt"] and e["board_latched_flag"] and e["motion_denied_while_latched"] == 2
    assert e["console_resets"] == ["DENY_ESTOP estop_button", "CONFIRM_YES estop_reset"]
    assert w["link_lost"] and w["t_ms_link_lost_fan0"] == 2466 and w["frames_after_fan_gt0"] == 0
    assert w["qemu_kill_user_report"] == "y"
    fw = d["firmware"]["after_d49"]
    assert fw["link_loss"]["ms"] == [1093, 1056] and fw["discard_presence"]["fail"] == 1


def test_scrub_catches_paths_and_headers(tmp_path):
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "events.jsonl").write_text('{"x": "plain text, build/kernel.elf"}\n')
    assert bundles.scrub(b) == []
    for bad in ('{"p": "/home/someone/x"}', '{"h": "Authorization: x"}', '{"p": "C:\\\\Users\\\\x"}'):
        (b / "blob").write_text(bad)
        assert bundles.scrub(b), bad


@pytest.mark.parametrize("name", [n for n, _, _ in bundles.BUNDLES])
def test_bundle_verifies_and_replays_without_key(name):
    """A4: each committed bundle: hash chain OK, REPLAY MATCH, secret scan and scrub 0 hits, no key in "
    "the env."""
    env = {k: v for k, v in os.environ.items() if k != "MODEL_API_KEY"}
    rel = f"replay/{name}"
    for cmd, want in (
        (["uv", "run", "ring3", "verify", rel], "CHAIN OK"),
        (["uv", "run", "ring3", "replay", rel], "REPLAY MATCH"),
        (["uv", "run", "python", str(bundles.SCAN.relative_to(REPO)), rel], ": 0 hit(s)"),
    ):
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, env=env, check=False)
        assert p.returncode == 0 and want in p.stdout, (cmd, p.stdout, p.stderr)
    assert bundles.scrub(REPO / rel) == []
    assert (REPO / rel / "README.md").read_text().count("\n") == 1


def test_bundle_replay_detects_a_changed_decision(tmp_path):
    """The bundle replay is not vacuous: flip one recorded decision and the replay must not match."""
    src = REPO / "replay" / "t04-run3-fan-limit"
    dst = tmp_path / "b"
    shutil.copytree(src, dst)
    lines = (dst / "events.jsonl").read_text("ascii").splitlines()
    for k, line in enumerate(lines):
        r = json.loads(line)
        if r.get("type") == "audit" and r.get("code_name") == "DENY_LIMIT":
            r["code_name"] = "ALLOW"
            lines[k] = json.dumps(r, sort_keys=True, separators=(",", ":"))
            break
    else:
        pytest.fail("no DENY_LIMIT in the bundle")
    (dst / "events.jsonl").write_text("\n".join(lines) + "\n", "ascii")
    p = subprocess.run(
        ["uv", "run", "ring3", "replay", str(dst)], cwd=REPO, capture_output=True, text=True, check=False
    )
    assert p.returncode != 0 and "MISMATCH" in p.stdout, p.stdout
