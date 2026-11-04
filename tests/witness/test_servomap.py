"""D-103 (T-16): the servo map. The fit from the six measured arm angles of zc-20261104-153656, the stored
config re-fitted and its provenance required, clamping of the zone-check targets to the kernel's 30-150,
the expected zone and the boundary band judged on the mapped angle (no call when it lands in a band), and
both angles on the result (the run loop logs them). The SAM backend never sees the map."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ring3bridge import abi, context_pack, zonecheck
from ring3bridge.calibration import route
from ring3bridge.config import ConfigError
from ring3bridge.witness import (
    CANT_VERIFY,
    COMMAND_BOUNDARY,
    COMMAND_RANGE,
    PASS,
    CantVerify,
    servomap,
    vision,
)
from ring3bridge.witness.dial import load_geometry
from ring3bridge.witness.servomap import IDENTITY, ServoMap, fit_servo_map, load_servo_map
from tests.witness.synth import Scene
from tests.witness.test_vision import make_vision, zone_reply

REPO = Path(__file__).resolve().parents[2]
GEOM = load_geometry()
PACK = context_pack.load(vision.PACK_NAME)
#: the six arm angles measured in the crops of zc-20261104-153656 (docs/evidence/T-16/README.md)
MEASURED = [(50, 50700), (90, 75900), (130, 109700), (90, 75500), (50, 50000), (130, 110600)]


def test_fit_from_the_measured_angles():
    m = fit_servo_map(MEASURED, source_run="zc-20261104-153656")
    assert (m.gain_ppm, m.offset_mdeg) == (747500, 11458)  # gauge = 0.7475 x command + 11.46 deg
    assert m.residual_mdeg == 3233 and m.points == tuple(MEASURED) and not m.identity
    assert [m.mapped_mdeg(c) for c in (50, 90, 130, 150)] == [48833, 78733, 108633, 123583]
    # integers in, integers out, rounded half up; a float command is refused
    assert type(m.mapped_mdeg(90)) is int
    with pytest.raises(TypeError):
        m.mapped_mdeg(90.0)  # type: ignore[arg-type]
    assert IDENTITY.identity and IDENTITY.mapped_mdeg(137) == 137_000
    with pytest.raises(ValueError):
        fit_servo_map(MEASURED[:3])
    with pytest.raises(ValueError):
        fit_servo_map([(80, 1), (90, 2), (100, 3), (110, 4)])  # span under 40 deg


def test_stored_map_is_the_fit_with_provenance_and_refuses_tampering(tmp_path):
    m = load_servo_map()
    assert (m.gain_ppm, m.offset_mdeg, m.source_run, m.measured) == (
        747500, 11458, "zc-20261104-153656", "2026-11-04")  # fmt: skip
    assert m.evidence == "docs/evidence/T-16/README.md" and (REPO / m.evidence).is_file()
    assert m.points == tuple(MEASURED) and "pivot" in m.method
    text = servomap.SERVO_MAP_TOML.read_text()
    bad = tmp_path / "servo_map.toml"
    bad.write_text(text.replace("gain_ppm = 747500", "gain_ppm = 900000"))
    with pytest.raises(ConfigError):
        load_servo_map(bad)
    bad.write_text(text.replace('source_run = "zc-20261104-153656"', 'source_run = ""'))
    with pytest.raises(ConfigError):
        load_servo_map(bad)
    bad.write_text(text.replace("[[50, 50700]", "[[50, 150700]"))
    with pytest.raises(ConfigError):
        load_servo_map(bad)
    assert load_servo_map(tmp_path / "missing.toml") is IDENTITY


def test_zone_check_targets_are_mapped_centres_clamped_and_reachable():
    m = load_servo_map()
    targets = vision.zone_targets(m)
    assert [(z, c) for z, c, _ in targets] == [("LOW", 52), ("MID", 105), ("HIGH", 150)]  # 150: clamped
    for zone, cmd, mapped in targets:
        assert m.mapped_mdeg(cmd) == mapped and 30 <= cmd <= 150
        assert vision.expected_zone(cmd, m) == (zone, mapped)  # each target's mapped angle is in its zone
    assert abs(targets[0][2] - 50_000) <= 1000 and abs(targets[1][2] - 90_000) <= 1000
    assert targets[2][2] == 123583 and targets[2][2] - 115_000 >= 5000  # HIGH: clear of the 110 band
    rounds = zonecheck.rounds_for(m)
    assert rounds == ((52, 105, 150), (150, 105, 52))
    # every round fits the kernel's request (at most 7 moves of at most 30 deg) from where it starts
    cur = 90
    for r in rounds:
        moves = route(cur, r)
        degs = [d for d, _ in moves]
        assert len(moves) <= abi.ACTIONS_PER_REQUEST - 1
        assert all(abs(b - a) <= 30 for a, b in zip([cur, *degs[:-1]], degs, strict=True))
        cur = moves[-1][0]
    assert zonecheck.rounds_for(IDENTITY) == ((50, 90, 130), (130, 90, 50))
    # a servo so slow that no command reaches a centre still gets a clamped, reachable target
    slow = ServoMap(500_000, 20_000)
    assert [c for _, c, _ in vision.zone_targets(slow)] == [60, 140, 150]


def test_expected_zone_and_boundary_on_the_mapped_angle():
    m = load_servo_map()
    assert vision.expected_zone(105, m) == ("MID", 89946)
    assert vision.expected_zone(52, m) == ("LOW", 50328)
    with pytest.raises(CantVerify) as e:
        vision.expected_zone(130, m)  # the agent pack's HIGH centre lands on the 110 band through this map
    assert e.value.reason == COMMAND_BOUNDARY and "commanded 130 deg maps to 108.6 deg" in e.value.detail
    assert vision.expected_zone(90, m) == ("MID", 78733)  # 3.7 deg clear of the 70 band's edge at 75
    with pytest.raises(CantVerify) as e:
        vision.expected_zone(20, m)
    assert e.value.reason == COMMAND_RANGE
    with pytest.raises(CantVerify) as e:
        vision.expected_zone(78, m)  # maps to 69.8: inside the 70 band
    assert e.value.reason == COMMAND_BOUNDARY
    # the identity map keeps the SAM-era words for the command itself
    assert vision.expected_zone(90) == ("MID", 90_000)
    with pytest.raises(CantVerify) as e:
        vision.expected_zone(70)
    assert e.value.detail == "commanded 70 deg is within 5 deg of the 70 deg edge"


def test_mapped_band_makes_no_call_and_both_angles_are_on_the_result(tmp_path):
    from tests.witness.test_vision import Camera

    m = load_servo_map()
    client, t, ledger, log = make_vision(tmp_path, zone_reply("HIGH"), zone_reply("MID"))
    cam = Camera(Scene([124.0]))
    w = vision.VisionWitness(cam, client, GEOM, PACK, vision.CHECKING, m)
    r = w.check(130).result  # mapped 108.6: on the band -> no frame, no call
    assert (r.verdict, r.reason) == (CANT_VERIFY, COMMAND_BOUNDARY)
    assert (r.commanded_deg, r.mapped_mdeg) == (130, 108633) and cam.calls == 0 and t.posts == []
    r = w.check(150).result  # mapped 123.6: HIGH expected; the model reads HIGH -> PASS
    assert (r.verdict, r.commanded_zone, r.measured_zone) == (PASS, "HIGH", "HIGH")
    assert (r.commanded_deg, r.mapped_mdeg) == (150, 123583) and len(t.posts) == 1
    r = w.check(105).result  # mapped 89.9: MID expected; the model reads MID -> PASS
    assert (r.verdict, r.commanded_zone) == (PASS, "MID") and r.mapped_mdeg == 89946
    recs = [x for x in log.path.read_text().splitlines() if "witness_vision" in x]
    assert len(recs) == 2


def test_zone_check_file_records_the_map_and_refuses_another(tmp_path):
    from ring3bridge.eventlog import EventLog
    from tests.witness.test_vision import ALL_PASS, GEOM_SHA

    m = load_servo_map()
    targets = zonecheck.flat_targets(zonecheck.rounds_for(m))
    results = [type(r)(**{**r.__dict__, "commanded_deg": t}) for r, t in zip(ALL_PASS, targets, strict=True)]
    zc = vision.zone_check(results, targets, "zc-m", PACK.sha256, GEOM_SHA, m)
    assert zc.ok and zc.servo_map == (747500, 11458, "zc-20261104-153656")
    d = tmp_path / "zc-m"
    log = EventLog(d)
    log.append("run_start", run_id="zc-m")
    doc = zonecheck.zonecheck_doc(zc)
    assert doc["servo_map"] == {"gain_ppm": 747500, "offset_mdeg": 11458, "source_run": "zc-20261104-153656"}
    sha = zonecheck.write_zonecheck(d, doc)
    log.append("zonecheck", **{k: v for k, v in doc.items() if k not in ("results", "servo_map")},
               file_sha256=sha)  # fmt: skip
    log.append("run_end", records=log.seq)
    loaded, _, _ = zonecheck.load_zonecheck(d, PACK.sha256, GEOM_SHA, m)
    assert loaded.ok and loaded.servo_map == zc.servo_map
    with pytest.raises(zonecheck.ZoneCheckError):
        zonecheck.load_zonecheck(d, PACK.sha256, GEOM_SHA, IDENTITY)  # the map changed: refused


def test_sam_backend_never_reads_the_map():
    for name in ("pipeline.py", "service.py", "sam.py", "angle.py", "calibrate.py", "verdict.py"):
        src = (REPO / "bridge" / "ring3bridge" / "witness" / name).read_text()
        mods = [n.module or "" for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)]
        assert not any("servomap" in mod for mod in mods), name
        assert "servo_map" not in src, name
