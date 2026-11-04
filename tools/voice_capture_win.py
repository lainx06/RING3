"""RING3 voice capture helper for **Windows** Python (T-19, D-104; the same pattern as the camera helper).

WSL2 has no webcam microphone, so this helper runs on the Windows side, records from the C922's built-in
microphone and answers through files in one shared folder (the console sees it as /mnt/c/...). It opens no
listener, talks to nothing and runs nothing it reads: a request is only a file name.

No hidden listening: the microphone is closed until a request arrives and closed again when the recording
ends, this window prints REC when it opens, and every recording stops at 15 s whatever else happens.
Requests already in the folder at start are deleted, never recorded.

Protocol (console side: bridge/ring3bridge/voice.py FolderRecorder), id = 32 lowercase hex digits:
  <id>.start   the console asks for a recording; the file holds the id
  <id>.rec     the microphone is open, the file holds the rate (the console shows REC only after this)
  <id>.stop    the console ends the recording
  <id>.cancel  the console drops it: no audio is written
  <id>.wav     the recording, written as <id>.tmp.wav, then renamed; 16-bit PCM, mono, 24 or 16 kHz (the docs)
  <id>.fail    the helper's error text, instead of a .wav

Setup (Windows, once):  py -m pip install sounddevice
Run (PowerShell):       $env:RING3_VOICE_DIR = "C:\\ring3-voice"; py <repo>\\tools\\voice_capture_win.py
Options:                --device NAME (substring, default C922, else the default input)  --list
                        --selftest (records 2 s, prints the level and the duration; no folder, nothing kept)
The folder comes from RING3_VOICE_DIR only; it is never committed.
"""

from __future__ import annotations

import argparse
import array
import math
import os
import re
import sys
import time
import wave
from pathlib import Path

ENV_DIR = "RING3_VOICE_DIR"
NONCE_RE = re.compile(r"[0-9a-f]{32}")
LEFTOVER_RE = re.compile(r"[0-9a-f]{32}\.(start|stop|cancel|rec|fail|wav|tmp|tmp\.wav)")
MAX_SECONDS = 15  # = models/transcribe.py MAX_SECONDS
WALL_MARGIN_S = 1.0  # a stream that delivers too slowly still ends: the cap plus this, on the clock
RATES = (24000, 16000)  # the documented rates, native first
SELFTEST_S = 2
POLL_S = 0.05


def chunk_frames(rate: int) -> int:
    return rate // 10  # 100 ms between looks at the folder


def write_wav(target, rate: int, pcm: bytes) -> None:  # noqa: ANN001 - a path or a file object
    with wave.open(target, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def level(pcm: bytes) -> tuple[int, int]:
    """(peak %, RMS %) of full scale, for 16-bit native-endian samples."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if not a:
        return 0, 0
    peak = max(abs(s) for s in a)
    rms = math.sqrt(sum(s * s for s in a) / len(a))
    return round(100 * peak / 32767), round(100 * rms / 32767)


def _unlink_all(folder: Path, nonce: str, suffixes: tuple[str, ...]) -> None:
    for s in suffixes:
        (folder / f"{nonce}.{s}").unlink(missing_ok=True)


def record_one(folder: Path, nonce: str, open_stream, log=print, on_chunk=None, clock=time.monotonic) -> int:  # noqa: ANN001
    """One recording. Returns 1 if a .wav was written. The microphone is open only inside this function."""
    stop, cancel = folder / f"{nonce}.stop", folder / f"{nonce}.cancel"
    tag = nonce[:8]
    try:
        stream = open_stream()
    except Exception as e:  # noqa: BLE001 - the console must hear about it
        (folder / f"{nonce}.fail").write_text(f"{type(e).__name__}: {e}"[:200], "ascii", "replace")
        log(f"{tag}: microphone did not open: {type(e).__name__}: {e}")
        return 0
    pcm = bytearray()
    try:
        rate = stream.rate
        (folder / f"{nonce}.rec").write_text(str(rate), "ascii")
        log(f"{tag}: REC started ({rate} Hz, cap {MAX_SECONDS} s)")
        cap, chunk = MAX_SECONDS * rate, chunk_frames(rate)
        t0 = clock()
        got = 0
        while got < cap and clock() - t0 < MAX_SECONDS + WALL_MARGIN_S:
            n = min(chunk, cap - got)
            data = stream.read(n)
            pcm += data
            got += len(data) // 2
            if on_chunk is not None:
                on_chunk(n)
            if cancel.exists():
                _unlink_all(folder, nonce, ("cancel", "stop", "rec"))
                log(f"{tag}: REC cancelled; nothing written")
                return 0
            if stop.exists():
                break
    except Exception as e:  # noqa: BLE001
        (folder / f"{nonce}.fail").write_text(f"{type(e).__name__}: {e}"[:200], "ascii", "replace")
        log(f"{tag}: REC failed: {type(e).__name__}: {e}")
        return 0
    finally:
        stream.close()
    tmp, out = folder / f"{nonce}.tmp.wav", folder / f"{nonce}.wav"
    write_wav(str(tmp), rate, bytes(pcm))
    os.replace(tmp, out)
    stop.unlink(missing_ok=True)
    peak, rms = level(bytes(pcm))
    log(f"{tag}: REC ended: {len(pcm) / 2 / rate:.2f} s, peak {peak} %, RMS {rms} %; microphone closed")
    return 1


def serve_once(folder: Path, open_stream, log=print, on_chunk=None) -> int:  # noqa: ANN001
    """Answer every pending request once. Returns the number of recordings written."""
    written = 0
    for req in folder.glob("*.cancel"):  # a cancel that came after its recording ended: nothing may stay
        if NONCE_RE.fullmatch(req.stem):
            _unlink_all(folder, req.stem, ("wav", "stop", "rec", "cancel"))
    for req in sorted(folder.glob("*.start")):
        nonce = req.stem
        if not NONCE_RE.fullmatch(nonce):
            continue
        try:
            body = req.read_bytes()[:64]
        except FileNotFoundError:
            continue
        req.unlink(missing_ok=True)
        if body.strip() != nonce.encode("ascii"):
            log(f"request {nonce[:8]}: ignored: body is not its id")
            continue
        written += record_one(folder, nonce, open_stream, log, on_chunk)
    return written


def drop_leftovers(folder: Path, log=print) -> int:  # noqa: ANN001
    """Whatever is in the folder at start belongs to no waiting console: delete it, recordings included."""
    n = 0
    for p in list(folder.iterdir()):
        if LEFTOVER_RE.fullmatch(p.name):
            p.unlink(missing_ok=True)
            n += 1
    if n:
        log(f"deleted {n} file(s) left from before the helper started")
    return n


def selftest(open_stream, log=print, name: str = "", seconds: int = SELFTEST_S) -> int:  # noqa: ANN001
    """``--selftest``: records for 2 s, prints the level and the duration, keeps nothing. 0 = the microphone
    gave the frames asked for and they are not silent."""
    stream = open_stream()
    try:
        rate = stream.rate
        log(f"selftest: REC {seconds} s from {name or 'the input device'} at {rate} Hz")
        t0 = time.monotonic()
        pcm = bytearray()
        for _ in range(seconds * 10):
            pcm += stream.read(chunk_frames(rate))
        wall = time.monotonic() - t0
    finally:
        stream.close()
    peak, rms = level(bytes(pcm))
    dur = len(pcm) / 2 / rate
    log(
        f"selftest: {dur:.2f} s recorded in {wall:.2f} s, peak {peak} %, RMS {rms} %; "
        "microphone closed, nothing kept"
    )
    if peak == 0:
        log("selftest: the input is silent: check the Windows microphone permission and the selected device")
        return 1
    return 0


def choose_device(devices, want: str) -> tuple[int | None, str]:  # noqa: ANN001
    """The first input device whose name holds ``want``, else the default input (index None)."""
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and want.lower() in d["name"].lower():
            return i, d["name"]
    return None, "default input"


class _SdStream:
    def __init__(self, sd, device: int | None) -> None:  # noqa: ANN001
        for rate in RATES:
            try:
                sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=rate)
            except Exception:  # noqa: BLE001 - try the other documented rate
                continue
            self.rate = rate
            self._s = sd.RawInputStream(samplerate=rate, device=device, channels=1, dtype="int16")
            self._s.start()
            return
        raise OSError(f"the device takes neither documented rate {RATES}")

    def read(self, frames: int) -> bytes:
        data, _overflowed = self._s.read(frames)
        return bytes(data)

    def close(self) -> None:
        self._s.stop()
        self._s.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RING3 voice capture helper (Windows)")
    ap.add_argument("--device", default="C922", help="substring of the input device name (default C922)")
    ap.add_argument("--list", action="store_true", help="list the input devices and exit")
    ap.add_argument(
        "--selftest", action="store_true", help="record 2 s, print the level and duration, keep nothing"
    )
    a = ap.parse_args(argv)
    import sounddevice as sd  # Windows-side; imported here so the protocol functions load without it

    devices = sd.query_devices()
    if a.list:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                print(i, d["name"])
        return 0
    index, name = choose_device(devices, a.device)

    def open_stream() -> _SdStream:
        return _SdStream(sd, index)

    if a.selftest:
        return selftest(open_stream, name=name)
    folder = Path(os.environ.get(ENV_DIR, ""))
    if not os.environ.get(ENV_DIR) or not folder.is_dir():
        print(f"set {ENV_DIR} to an existing folder", file=sys.stderr)
        return 2
    drop_leftovers(folder)
    print(f"input: {name}. The microphone stays closed until a request; Ctrl+C stops the helper.")
    try:
        while True:
            serve_once(folder, open_stream)
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
