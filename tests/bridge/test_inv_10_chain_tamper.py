"""INV-10 (chain part, T-03a): ``ring3 verify`` detects a one-byte edit anywhere in the log or in any blob."""

import json
import shutil

import pytest

from ring3bridge.eventlog import EventLog, canonical, verify

from .sample_run import write_sample_run


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "runs" / "r-sample"
    write_sample_run(d)
    return d


def test_sample_run_verifies(run):
    r = verify(run)
    assert r.ok and r.complete, r.message
    assert r.message.startswith("CHAIN OK: 13 records, 6 blobs")


def test_inv_10_chain_tamper_every_log_byte(run, tmp_path):
    """Every single byte of events.jsonl, replaced by another value, breaks verification."""
    path = run / "events.jsonl"
    orig = path.read_bytes()
    for i in range(len(orig)):
        for new in {orig[i] ^ 0x01, orig[i] ^ 0x20, ord("\n") if orig[i] != 10 else ord(" ")}:
            b = bytearray(orig)
            b[i] = new
            path.write_bytes(bytes(b))
            r = verify(run)
            assert not r.ok, f"byte {i}: {orig[i]:#x} -> {new:#x} not detected"
            assert r.message.startswith("BROKEN")
    path.write_bytes(orig)
    assert verify(run).ok


def test_inv_10_chain_tamper_every_blob_byte(run):
    blobs = sorted((run / "blobs").iterdir())
    assert len(blobs) == 6
    for p in blobs:
        orig = p.read_bytes()
        for i in range(len(orig)):
            b = bytearray(orig)
            b[i] ^= 0x01
            p.write_bytes(bytes(b))
            r = verify(run)
            assert not r.ok and "BROKEN blob" in r.message and p.name[:16] in r.message, (p.name, i)
        p.write_bytes(orig)
    assert verify(run).ok


def test_missing_blob_and_extra_file(run):
    victim = sorted((run / "blobs").iterdir())[0]
    data = victim.read_bytes()
    victim.unlink()
    assert "missing" in verify(run).message
    victim.write_bytes(data)
    (run / "blobs" / "notes.txt").write_text("x")
    assert "unexpected file" in verify(run).message


def test_deleted_reordered_and_truncated_records(run):
    path = run / "events.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:5] + lines[6:]))  # delete one record
    assert not verify(run).ok
    path.write_bytes(b"".join(lines[:5] + [lines[6], lines[5]] + lines[7:]))  # swap two
    assert not verify(run).ok
    path.write_bytes(b"".join(lines[:-1]))  # drop run_end: still a valid chain, but flagged incomplete
    r = verify(run)
    assert r.ok and not r.complete and "incomplete" in r.message
    path.write_bytes(b"".join(lines)[:-1])  # cut the final newline
    assert not verify(run).ok
    path.write_bytes(b"".join(lines[1:]))  # drop run_start
    assert not verify(run).ok


def test_rewritten_record_with_recomputed_hash_breaks_the_next_link(run):
    import hashlib

    path = run / "events.jsonl"
    lines = path.read_text().splitlines()
    rec = json.loads(lines[4])
    rec["status"] = 500
    del rec["h"]
    rec["h"] = hashlib.sha256(canonical(rec).encode()).hexdigest()
    lines[4] = canonical(rec)
    path.write_text("\n".join(lines) + "\n")
    r = verify(run)
    assert not r.ok and "#5" in r.message and "prev" in r.message


def test_writer_refuses_floats_bools_unknown_types_and_dangling_blobs(tmp_path):
    log = EventLog(tmp_path / "r")
    with pytest.raises(ValueError):
        log.append("run_start", x=1.5)
    with pytest.raises(ValueError):
        log.append("run_start", x=True)
    with pytest.raises(ValueError):
        log.append("run_start", nested={"a": [1, 2.0]})
    with pytest.raises(ValueError):
        log.append("chat", x=1)
    with pytest.raises(ValueError):
        log.append("run_start", blob="0" * 64)
    with pytest.raises(ValueError):
        log.append("run_start", h="x")
    log.append("run_start", x=1)
    with pytest.raises(FileExistsError):
        EventLog(tmp_path / "r")


def test_non_integer_number_in_a_rehashed_log_is_rejected(tmp_path):
    """Even with a correctly recomputed chain, a float in a record is refused by verify (D-15)."""
    import hashlib

    d = tmp_path / "r"
    (d / "blobs").mkdir(parents=True)
    rec = {"type": "run_start", "seq": 0, "prev": "0" * 64, "cost": 1.5}
    rec["h"] = hashlib.sha256(canonical(rec).encode()).hexdigest()
    (d / "events.jsonl").write_text(canonical(rec) + "\n")
    r = verify(d)
    assert not r.ok and "non-integer" in r.message


def test_copy_of_run_verifies_identically(run, tmp_path):
    dst = tmp_path / "copy"
    shutil.copytree(run, dst)
    assert verify(dst).message == verify(run).message
