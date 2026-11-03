"""INV-14: the camera witness only reports evidence. CAN'T VERIFY for anything uncertain, never upgraded, and
nothing in the witness can reach the kernel link or the model key."""

import ast
from pathlib import Path

import pytest

from ring3bridge.models.transport import TransportResponse
from ring3bridge.witness import (
    AMBIGUOUS,
    BOUNDARY,
    CALIBRATION,
    CANT_VERIFY,
    COMMAND_BOUNDARY,
    LOW_CONFIDENCE,
    MARKERS,
    NOT_FOUND,
    PASS,
    SAM_FORMAT,
    SHAPE,
    STALE_FRAME,
    ZONE_MATCH,
    CantVerify,
    pipeline,
)
from ring3bridge.witness.calibrate import fit
from ring3bridge.witness.capture import request_frame
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.sam import SamStop
from ring3bridge.witness.verdict import Judgement
from tests.witness import synth
from tests.witness.fakes import make_client
from tests.witness.synth import OFFSET_DEG, Scene

GEOM = load_geometry()
REPO = Path(__file__).resolve().parents[2]
WITNESS = REPO / "bridge" / "ring3bridge" / "witness"
GOOD_CAL = fit([(c, (c + int(OFFSET_DEG)) * 1000) for c in synth.CAL_CMDS])
BAD_CAL = fit([(c, c * 1000 + (9000 if c == 90 else 0)) for c in synth.CAL_CMDS])
FAR_BOX = "1 <|box;x1=700;y1=40;x2=760;y2=90;w=60;h=50;c=0.95|><|mask;x=0;y=0;c=0.95;data=570,920,a85:abc|>"

# (name, scene, commanded, calibration, conf, forced SAM line, reason, SAM called?)
CASES = [
    ("markers missing", Scene([94.0], cover_marker=2), 90, GOOD_CAL, "0.91", None, MARKERS, False),
    ("markers missing (id 0)", Scene([94.0], cover_marker=0), 90, GOOD_CAL, "0.91", None, MARKERS, False),
    ("arm not found (no mask)", Scene([]), 90, GOOD_CAL, "0.91", None, NOT_FOUND, True),
    ("two candidates (two masks)", Scene([64.0, 124.0]), 90, GOOD_CAL, "0.91", None, AMBIGUOUS, True),
    ("two overlapping candidates", Scene([90.0, 100.0]), 90, GOOD_CAL, "0.91", None, AMBIGUOUS, True),
    ("boundary band", Scene([69.0]), 50, GOOD_CAL, "0.91", None, BOUNDARY, True),
    ("bad calibration", Scene([94.0]), 90, BAD_CAL, "0.91", None, CALIBRATION, False),
    ("not calibrated", Scene([94.0]), 90, fit([]), "0.91", None, CALIBRATION, False),
    ("unparseable SAM line", Scene([94.0]), 90, GOOD_CAL, "0.91", "the arm points up", SAM_FORMAT, True),
    ("low confidence", Scene([94.0]), 90, GOOD_CAL, "0.21", None, LOW_CONFIDENCE, True),
    ("no confidence", Scene([94.0]), 90, GOOD_CAL, "", None, LOW_CONFIDENCE, True),
    ("box not arm-shaped", Scene([94.0]), 90, GOOD_CAL, "0.91", FAR_BOX, SHAPE, True),
    ("command on a boundary", Scene([74.0]), 70, GOOD_CAL, "0.91", None, COMMAND_BOUNDARY, False),
]


@pytest.mark.parametrize("name,scene,cmd,cal,conf,line,reason,called", CASES, ids=[c[0] for c in CASES])
def test_inv_14_cant_verify_cases(tmp_path, name, scene, cmd, cal, conf, line, reason, called):
    res, t, ledger, _ = synth.run_check(scene, cmd, cal, GEOM, tmp_path, conf=conf, line=line)
    assert res.verdict == CANT_VERIFY and res.reason == reason, (name, res)
    assert res.calibrated_mdeg is None and res.measured_zone == ""
    assert len(t.posts) == (1 if called else 0)
    assert ledger.committed_uusd() == (2500 if called else 0)  # nothing reserved when SAM isn't needed


def test_inv_14_cant_verify_stale_frame(tmp_path):
    with pytest.raises(CantVerify) as e:
        request_frame(tmp_path, timeout_s=0.1, poll_s=0.01)
    assert e.value.reason == STALE_FRAME


@pytest.mark.parametrize("name,scene,cmd,cal,conf,line,reason,called", CASES, ids=[c[0] for c in CASES])
def test_inv_14_never_upgraded(tmp_path, monkeypatch, name, scene, cmd, cal, conf, line, reason, called):
    """Even if the last stage said PASS for anything, an earlier CAN'T VERIFY stands."""
    if reason in (BOUNDARY, COMMAND_BOUNDARY):
        pytest.skip("raised by the verdict stage itself")
    monkeypatch.setattr(pipeline, "judge", lambda c, m: Judgement(PASS, ZONE_MATCH, "MID", "MID"))
    res, *_ = synth.run_check(scene, cmd, cal, GEOM, tmp_path, conf=conf, line=line)
    assert res.verdict == CANT_VERIFY and res.reason == reason


@pytest.mark.parametrize("status", [401, 403])
def test_inv_14_auth_failure_stops_instead_of_cant_verify(tmp_path, status):
    frame, *_ = synth.render(Scene([94.0]))
    client, t, ledger = make_client(tmp_path, TransportResponse(status, b"{}"))
    with pytest.raises(SamStop):
        pipeline.check(frame, 90, GOOD_CAL, GEOM, client)
    assert len(t.posts) == 1 and ledger.unsettled_uusd() == 0


# ---- static: not in the decision path ----

ALLOWED_IMPORTS = {
    "__future__", "base64", "collections.abc", "dataclasses", "hashlib", "json", "math", "os", "pathlib",
    "re", "secrets", "time", "tomllib", "typing", "cv2", "numpy",
    "ring3bridge.config", "ring3bridge.ledger", "ring3bridge.models.transport", "ring3bridge.witness",
}  # fmt: skip
LINK_SIDE = {"ring3bridge.link", "ring3bridge.framing", "ring3bridge.actions", "ring3bridge.run",
             "ring3bridge.abi", "ring3bridge.replay", "ring3bridge.fuzz", "ring3bridge.cli", "socket",
             "serial", "subprocess", "http", "urllib", "ssl", "asyncio", "selectors"}  # fmt: skip
SECRET_TOKENS = ("MODEL_API_KEY", "environ", "getenv", "HttpsTransport", "Authorization", "putenv")
WRITERS = {"write_bytes", "write_text", "write", "sendall", "send", "sendto"}


def _is_write(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        if fn.attr in WRITERS:
            return fn.attr
        if (
            isinstance(fn.value, ast.Name)
            and fn.value.id == "os"
            and fn.attr in ("replace", "rename", "open")
        ):
            return f"os.{fn.attr}"
    if isinstance(fn, ast.Name) and fn.id == "open":
        mode = (
            node.args[1]
            if len(node.args) > 1
            else next((k.value for k in node.keywords if k.arg == "mode"), None)
        )
        if not (isinstance(mode, ast.Constant) and set(str(mode.value)) <= set("rbt")):
            return "open (not read-only)"
    return None


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _witness_sources():
    files = sorted(WITNESS.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "dial.py", "sam.py", "angle.py", "verdict.py",
                                        "calibrate.py", "capture.py", "pipeline.py"}  # fmt: skip
    return files


def test_inv_14_not_in_decision_path():
    problems = []
    for f in _witness_sources():
        src = f.read_text()
        tree = ast.parse(src)
        for mod in _imports(tree):
            ok = mod in ALLOWED_IMPORTS or mod.startswith("ring3bridge.witness.")
            if not ok or any(mod == b or mod.startswith(b + ".") for b in LINK_SIDE):
                problems.append(f"{f.name} imports {mod}")
        for tok in SECRET_TOKENS:
            if tok in src:
                problems.append(f"{f.name} mentions {tok}")
        for node in ast.walk(tree):
            # the only writes are capture.py's request files in the shared capture folder
            if isinstance(node, ast.Call) and (w := _is_write(node)) and f.name != "capture.py":
                problems.append(f"{f.name}:{node.lineno} calls {w}()")
    assert problems == []


def test_inv_14_static_scan_catches_a_link_import(tmp_path):
    """The scanner itself works: a planted link import and key read are reported."""
    bad = "from ring3bridge.link import Link\nimport os\nk = os.environ['MODEL_API_KEY']\nopen('x', 'wb')\n"
    tree = ast.parse(bad)
    mods = list(_imports(tree))
    assert "ring3bridge.link" in mods and any(t in bad for t in SECRET_TOKENS)
    assert [_is_write(n) for n in ast.walk(tree) if isinstance(n, ast.Call)].count(
        "open (not read-only)"
    ) == 1


def test_capture_helper_has_no_network_and_runs_nothing():
    src = (REPO / "tools" / "witness_capture_win.py").read_text()
    mods = set(_imports(ast.parse(src)))
    assert mods <= {"__future__", "argparse", "os", "re", "sys", "time", "pathlib", "cv2"}, mods
    for tok in ("socket", "http", "urllib", "subprocess", "eval(", "exec(", "MODEL_API_KEY"):
        assert tok not in src, tok
