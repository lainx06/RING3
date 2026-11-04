"""Hash-chained event log (INV-10, D-15): runs/<run_id>/events.jsonl + content-addressed blobs.

Each line is one canonical JSON object (sorted keys, no spaces, ASCII, integers only - no floats, no bools):
  {"h":<sha256 hex>, "prev":<h of the previous record, or 64 zeros>, "seq":<0..>, "type":<record type>, ...}
``h`` = SHA-256 of the canonical record without ``h``. Blobs live at blobs/<sha256 hex> and are referenced by
fields named ``blob`` or ending in ``_blob``.

``verify(run_dir)`` re-reads the bytes, checks every line is exactly canonical, the seq/prev/h chain, the
record types, integers only, that every referenced blob exists, and recomputes the SHA-256 of every blob.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

ZERO_H = "0" * 64

#: ARCHITECTURE §6 record names, plus ``event`` for decoded EVENT frames (D-52) and, from T-03b, ``frame``
#: (every link frame in either direction, body as hex), ``frame_error`` (a K->B frame the bridge dropped),
#: ``final_sent`` / ``stop_sent`` (the bridge's own end-of-request frames), and from T-07 Part B ``witness``
#: (one camera check, integer millidegrees, images and the SAM line as blobs) and ``calibration`` (the fit a
#: run uses, or the one ``ring3 calibrate`` produced), and from T-16 ``zonecheck`` (the vision backend's
#: 6-image zone check, or the one a run loaded).
RECORD_TYPES = frozenset(
    {
        "run_start",
        "user_request",
        "spend_reserved",
        "model_request",
        "model_reply",
        "reply_rejected",
        "action_sent",
        "final_sent",
        "stop_sent",
        "frame",
        "frame_error",
        "audit",
        "event",
        "observation",
        "request_end",
        "spend_settled",
        "witness",
        "calibration",
        "sam_probe",
        "zonecheck",
        "voice_request",
        "voice_reply",
        "voice_heard",
        "voice_sent",
        "voice_discarded",
        "run_end",
    }
)
RESERVED = frozenset({"h", "prev", "seq", "type"})


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _is_hex64(v: object) -> bool:
    return type(v) is str and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def check_values(v: object, where: str = "") -> str | None:
    """Integers only (D-15): returns the path of the first float/bool, else None."""
    if type(v) is bool or type(v) is float:
        return where or "."
    if type(v) is dict:
        for k, x in v.items():
            if type(k) is not str:
                return where + "/<key>"
            r = check_values(x, f"{where}/{k}")
            if r:
                return r
    elif type(v) is list:
        for i, x in enumerate(v):
            r = check_values(x, f"{where}/{i}")
            if r:
                return r
    elif v is not None and type(v) not in (int, str):
        return where or "."
    return None


def blob_refs(rec: dict) -> list[str]:
    return [v for k, v in rec.items() if k == "blob" or k.endswith("_blob")]


class EventLog:
    """Writer. One instance per run; appends are flushed and fsync'd."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = Path(run_dir)
        self.path = self.dir / "events.jsonl"
        self.blobs = self.dir / "blobs"
        if self.path.exists():
            raise FileExistsError(f"run log exists: {self.path.name}")
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.seq = 0
        self.prev = ZERO_H

    def blob(self, data: bytes) -> str:
        h = sha256_hex(data)
        p = self.blobs / h
        if not p.exists():
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return h

    def append(self, rtype: str, **fields: object) -> dict:
        if rtype not in RECORD_TYPES:
            raise ValueError(f"unknown record type {rtype!r}")
        if RESERVED & fields.keys():
            raise ValueError("reserved field name")
        bad = check_values(fields)
        if bad:
            raise ValueError(f"not an integer-only record: {bad}")
        for ref in blob_refs(fields):
            if not _is_hex64(ref) or not (self.blobs / ref).exists():
                raise ValueError("blob reference to a blob that was not stored")
        rec = {"type": rtype, "seq": self.seq, "prev": self.prev, **fields}
        rec["h"] = sha256_hex(canonical(rec).encode("ascii"))
        with open(self.path, "a", encoding="ascii", newline="\n") as f:
            f.write(canonical(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.seq += 1
        self.prev = rec["h"]
        return rec


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    message: str
    records: int = 0
    blobs: int = 0
    complete: bool = False  # last record is run_end


def _fail(msg: str, n: int = 0, b: int = 0) -> VerifyResult:
    return VerifyResult(False, msg, n, b)


def verify(run_dir: Path) -> VerifyResult:
    run_dir = Path(run_dir)
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return _fail("BROKEN: no events.jsonl")
    data = path.read_bytes()
    if not data:
        return _fail("BROKEN: empty log")
    if not data.endswith(b"\n"):
        return _fail("BROKEN: log does not end with a newline (truncated or edited last record)")
    lines = data[:-1].split(b"\n")
    prev = ZERO_H
    refs: set[str] = set()
    last_type = ""
    for i, raw in enumerate(lines):
        where = f"BROKEN at record #{i}"
        try:
            text = raw.decode("ascii")
            rec = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return _fail(f"{where}: not a JSON line", i)
        if type(rec) is not dict or canonical(rec) != text:
            return _fail(f"{where}: not canonical JSON", i)
        if rec.get("seq") != i or type(rec.get("seq")) is not int:
            return _fail(f"{where}: seq", i)
        if rec.get("prev") != prev:
            return _fail(f"{where}: prev does not chain", i)
        h = rec.pop("h", None)
        if not _is_hex64(h) or sha256_hex(canonical(rec).encode("ascii")) != h:
            return _fail(f"{where}: hash mismatch", i)
        if rec.get("type") not in RECORD_TYPES:
            return _fail(f"{where}: unknown record type", i)
        if i == 0 and rec["type"] != "run_start":
            return _fail(f"{where}: first record is not run_start", i)
        bad = check_values(rec)
        if bad:
            return _fail(f"{where}: non-integer number at {bad}", i)
        for ref in blob_refs(rec):
            if not _is_hex64(ref):
                return _fail(f"{where}: bad blob reference", i)
            refs.add(ref)
        prev = h
        last_type = rec["type"]
    n = len(lines)
    blob_dir = run_dir / "blobs"
    files = sorted(p for p in blob_dir.iterdir()) if blob_dir.is_dir() else []
    names = {p.name for p in files}
    for ref in sorted(refs):
        if ref not in names:
            return _fail(f"BROKEN blob {ref[:16]}: referenced but missing", n)
    for p in files:
        if not _is_hex64(p.name) or not p.is_file():
            return _fail(f"BROKEN blob {p.name[:16]}: unexpected file in blobs/", n)
        if sha256_hex(p.read_bytes()) != p.name:
            return _fail(f"BROKEN blob {p.name[:16]}: content does not match its SHA-256", n)
    complete = last_type == "run_end"
    msg = f"CHAIN OK: {n} records, {len(files)} blobs"
    if not complete:
        msg += " (run incomplete: no run_end)"
    return VerifyResult(True, msg, n, len(files), complete)
