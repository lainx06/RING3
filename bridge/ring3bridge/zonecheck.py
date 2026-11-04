"""``ring3 calibrate --witness-backend vision`` (T-16): the zone check's targets and its file.

Six images, two per zone, away from the edges, over two console requests (the first ``50, 90, 130``, the
second ``90, 50, 130``, each at most 7 moves from where the previous one ended), through the normal kernel
path like calibration (``calibration.CalibrationScript`` with ``checks=True`` and ``rounds``). The result
(``witness.vision.zone_check``) is written next to the run's ``events.jsonl`` as ``zonecheck.json``
(integers only) and logged as a ``zonecheck`` record with the file's SHA-256. ``ring3 run
--witness-backend vision --calibration <run>`` re-verifies the chain, the file hash, the pack and gauge
hashes, and recomputes the verdict from the stored rows: anything else is refused (fails closed).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ring3bridge import eventlog
from ring3bridge.witness import PASS
from ring3bridge.witness.vision import BACKEND, ZoneCheck

ZONE_TARGETS: tuple[tuple[int, ...], ...] = ((50, 90, 130), (90, 50, 130))
FILE = "zonecheck.json"


class ZoneCheckError(Exception):
    pass


def flat_targets(rounds: tuple[tuple[int, ...], ...] = ZONE_TARGETS) -> tuple[int, ...]:
    return tuple(d for r in rounds for d in r)


def zonecheck_doc(zc: ZoneCheck) -> dict:
    return {
        "run_id": zc.run_id,
        "backend": BACKEND,
        "targets": list(zc.targets),
        "results": [list(row) for row in zc.results],
        "passed": zc.passed,
        "n": zc.n,
        "ok": int(zc.ok),
        "reason": zc.reason,
        "pack_sha256": zc.pack_sha256,
        "geometry_sha256": zc.geometry_sha256,
    }


def write_zonecheck(run_dir: Path, doc: dict) -> str:
    data = (eventlog.canonical(doc) + "\n").encode("ascii")
    (Path(run_dir) / FILE).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _row_ok(row: object) -> bool:
    return (
        type(row) is list
        and len(row) == 5
        and type(row[0]) is int
        and all(type(x) is str for x in row[1:4])
        and (row[4] is None or type(row[4]) is int)
    )


def load_zonecheck(run_dir: Path, pack_sha: str, geom_sha: str) -> tuple[ZoneCheck, dict, str]:
    """-> (the zone check recomputed from the stored rows, the stored document, its SHA-256)."""
    run_dir = Path(run_dir)
    v = eventlog.verify(run_dir)
    if not v.ok:
        raise ZoneCheckError(f"zone-check run does not verify: {v.message}")
    try:
        data = (run_dir / FILE).read_bytes()
    except OSError as e:
        raise ZoneCheckError(f"no {FILE}: {e}") from None
    sha = hashlib.sha256(data).hexdigest()
    recs = [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines()]
    logged = [r for r in recs if r.get("type") == "zonecheck"]
    if not logged or logged[-1].get("file_sha256") != sha:
        raise ZoneCheckError(f"{FILE} is not the file the zone-check run logged")
    doc = json.loads(data)
    if type(doc) is not dict or doc.get("backend") != BACKEND:
        raise ZoneCheckError("not a vision zone check")
    if doc.get("pack_sha256") != pack_sha:
        raise ZoneCheckError("zone check made with another witness context pack")
    if doc.get("geometry_sha256") != geom_sha:
        raise ZoneCheckError("zone check made on another gauge geometry")
    targets, rows = doc.get("targets"), doc.get("results")
    if type(targets) is not list or not all(type(t) is int for t in targets) or not targets:
        raise ZoneCheckError("bad targets")
    if type(rows) is not list or not all(_row_ok(r) for r in rows):
        raise ZoneCheckError("bad result rows")
    passed = sum(1 for i, t in enumerate(targets) if i < len(rows) and rows[i][0] == t and rows[i][1] == PASS)
    ok = len(rows) == len(targets) and passed == len(targets)
    if (bool(doc.get("ok")), doc.get("passed"), doc.get("n")) != (ok, passed, len(targets)):
        raise ZoneCheckError("stored verdict differs from the stored result rows")
    reason = doc.get("reason") if type(doc.get("reason")) is str else ""
    if ok and reason:
        raise ZoneCheckError("stored verdict differs from the stored result rows")
    if not ok and not reason:
        reason = f"{passed} of {len(targets)} PASS"
    zc = ZoneCheck(ok, reason, passed, len(targets), str(doc.get("run_id", "")), pack_sha, geom_sha,
                   tuple(targets), tuple(tuple(r) for r in rows))  # fmt: skip
    return zc, doc, sha
