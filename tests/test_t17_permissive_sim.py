"""T-17 Do 2 (D-98, D-99): the permissive sim-cell replay. The PERMISSIVE production image on the simulated
cell, the bundle's recorded model replies played back by the real bridge run loop; no key, no network.

A2: the banner is on every screendump (PNG strips and text dump), in the QEMU window title and on every
timeline line; the real board is refused by the tool, by its QEMU command guard and by the make target.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from ring3bridge import counterfactual as cf
from ring3bridge import replay
from tests.qemu import REPO, qemu_command

sys.path.insert(0, str(REPO / "tools"))
import counterfactual_sim as sim  # noqa: E402

BANNER = "PERMISSIVE BUILD: no capability, taint or confirmation checks (device limits kept)"


@pytest.fixture(scope="module", autouse=True)
def permissive_image():
    subprocess.run(["make", "-s", "POLICY=permissive"], cwd=REPO, check=True)
    assert sim.KERNEL.exists()


def test_banner_text_is_the_lead_s():
    assert sim.BANNER == cf.BANNER == BANNER


def test_window_title_is_the_banner():
    cmd = sim.labelled(qemu_command(sim.KERNEL, "screen", com2_port=4555, com3_port=4556), "gtk")
    name = cmd[cmd.index("-name") + 1]
    assert name == "guest=" + BANNER.replace(",", ",,") and cmd[cmd.index("-display") + 1] == "gtk"
    with pytest.raises(sim.Refused):
        sim.labelled(qemu_command(sim.KERNEL, "screen", com2_port=4555, com3_port="/dev/ttyACM0"), "gtk")


# ---- the real board is refused --------------------------------------------------------------------------
def test_tool_refuses_a_device_cell():
    with pytest.raises(sim.Refused, match="simulated cell only"):
        sim.refuse_device("/dev/ttyACM0")
    sim.refuse_device("sim")


def test_tool_refuses_cell_port_env(monkeypatch):
    monkeypatch.setenv("CELL_PORT", "/dev/ttyACM0")
    with pytest.raises(sim.Refused, match="CELL_PORT"):
        sim.refuse_device("sim")


def test_qemu_command_guard_refuses_the_board():
    """Even if the harness were asked for the board as COM3, the permissive command is refused."""
    cmd = qemu_command(sim.KERNEL, "screen", com2_port=4555, com3_port="/dev/ttyACM0")
    with pytest.raises(sim.Refused, match="not the simulated cell"):
        sim.guard_command(cmd)
    with pytest.raises(sim.Refused):
        sim.guard_command(qemu_command(sim.KERNEL, "screen", com2_port=4555, com3_port="tcp:10.0.0.2:1"))
    ok = qemu_command(sim.KERNEL, "screen", com2_port=4555, com3_port=4556)
    assert sim.guard_command(ok) == ok


@pytest.mark.parametrize("how", ["arg", "env", "make"])
def test_cli_and_make_refuse_the_board(how, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "CELL_PORT")}
    bundle = "replay/t04-run3-fan-limit"
    if how == "arg":
        cmd = ["uv", "run", "python", "tools/counterfactual_sim.py", bundle, "--cell", "/dev/ttyACM0"]
    elif how == "env":
        cmd = ["uv", "run", "python", "tools/counterfactual_sim.py", bundle]
        env["CELL_PORT"] = "/dev/ttyACM0"
    else:
        cmd = ["make", "-s", "run-cell-sim-permissive", f"BUNDLE={bundle}", "CELL_PORT=/dev/ttyACM0"]
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 2 and "refused" in (p.stdout + p.stderr), (p.stdout, p.stderr)
    assert "simulated cell only" in p.stdout + p.stderr


def test_state_dependent_bundle_is_refused():
    """The red-team bundle's decisions came from the real cell's E-stop: the sim cannot show them."""
    with pytest.raises(sim.Refused, match="AUTO_HALT, DENY_ESTOP"):
        sim.Bundle(REPO / "replay" / "e4-redteam-20261103-230412")


# ---- the runs -------------------------------------------------------------------------------------------
def run_bundle(name: str, tmp_path):
    lines: list[str] = []
    run_dir = tmp_path / f"cf-sim-{name}"
    res = sim.run(REPO / "replay" / name, "none", tmp_path / "shots", run_dir, say=lines.append, pace_s=0.8)
    return res, lines, run_dir


def check_labels(res: dict, lines: list[str], shots, n_requests: int) -> None:
    import cv2

    assert lines and all(sim.TAG in x for x in lines)
    assert lines[0].endswith(BANNER) and lines[-1].endswith(BANNER)
    assert sum(f"{BANNER} | request" in x for x in lines) == n_requests
    assert res["shots"], "no screendump taken"
    for png in res["shots"]:
        img = cv2.imread(str(shots / png))
        assert img.shape[0] == 400 + 2 * 34, img.shape  # the kernel's 720x400 screen between two strips
        for strip in (img[:34], img[-34:]):
            b, g, r = (int(strip[..., c].mean()) for c in range(3))
            assert r > 150 and g < 60 and b < 60, (png, r, g, b)  # red strip with the banner text
            assert (strip.max(axis=2) > 200).sum() > 200, png  # white banner text drawn on it
        txt = (shots / png).with_suffix(".txt").read_text("utf-8").splitlines()
        assert txt[0] == BANNER and txt[-1] == BANNER and len(txt) == 27


def test_permissive_sim_taint_write_allowed(tmp_path):
    """The headline: the tainted write to scratch_0 that RING3 sent to the human is allowed without a prompt
    on the permissive kernel; the screen says ALLOW obj_write(scratch_0) between the banner strips."""
    res, lines, run_dir = run_bundle("t04-run4-taint-prompt", tmp_path)
    assert res["outcomes"] == ["DONE"]
    audits = sim.audits(run_dir)
    assert audits and all(a["permissive"] == 1 for a in audits)
    (w,) = [a for a in audits if a["subject"] == "obj_write"]
    assert (w["code_name"], w["reason_name"], w["tainted"]) == ("ALLOW", "permissive", 1)
    assert not [a for a in audits if a["code_name"].startswith("CONFIRM")], "the permissive build asks nobody"
    assert "Allow? [y/N]" not in res["com1"]
    # the reply after the divergence is played but marked hypothetical
    assert res["hypothetical"] == 1 and res["unused"] == [0]
    assert sum(cf.HYPOTHETICAL in x for x in lines) == 1
    shot = next(s for s in res["shots"] if "obj_write-allow" in s)
    txt = (tmp_path / "shots" / shot).with_suffix(".txt").read_text("utf-8")
    assert "ALLOW       obj_write(scratch_0)" in txt and "TAINTED: YES" in txt
    check_labels(res, lines, tmp_path / "shots", 1)
    # this run's own tape replays through the permissive host build (the bridge was there before boot)
    r = replay.replay_run(run_dir)
    assert r.ok, r.message


def test_permissive_sim_fan_100_still_refused(tmp_path):
    """fan_set(100) on the permissive kernel: DENY_LIMIT fan_range; the sim cell gets no duty above 80."""
    res, lines, run_dir = run_bundle("t04-run3-fan-limit", tmp_path)
    assert res["outcomes"] == ["DONE", "DONE"]
    (f,) = [a for a in sim.audits(run_dir) if a["subject"] == "cell_fan" and a["target"] == 100]
    assert (f["code_name"], f["reason_name"], f["result"]) == ("DENY_LIMIT", "fan_range", -200)
    assert all(d <= 80 for d in res["fans"]) and 100 not in res["fans"]
    assert res["hypothetical"] == 0
    check_labels(res, lines, tmp_path / "shots", 2)
    assert replay.replay_run(run_dir).ok
