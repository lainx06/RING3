"""Voice requests, host side (T-19, D-104): what a transcript may become, the recorder's half of the
shared-folder protocol (the other half: tools/voice_capture_win.py) and the worker that goes from a key press
to a transcript.

A transcript is untrusted text. It only ever becomes one printable-ASCII line on the kernel's console, after
the operator's Enter (console.py), or nothing. It never reaches a shell or the bridge, and a line that is only
an answer to a confirmation (y / n / yes / no) or a kernel console command is never offered.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ring3bridge.models.transcribe import MAX_SECONDS, AudioRefused, VoiceStop

MAX_LINE = 240  # kernel/include/console.h CONSOLE_LINE_MAX: the kernel drops characters past it, silently
MAX_RAW = 4096
MAX_RECORD_S = MAX_SECONDS

REFUSED_EMPTY = "empty"
REFUSED_LONG = "too_long"
REFUSED_ANSWER = "bare_answer"
REFUSED_COMMAND = "console_command"
REFUSED_TEXT = {
    REFUSED_EMPTY: "nothing to send",
    REFUSED_LONG: f"longer than the console's {MAX_LINE} characters",
    REFUSED_ANSWER: "only a y/n word: confirmations are keyboard-only",
    REFUSED_COMMAND: "a console command, not a request: type it if you mean it",
}

# kernel/core/console.c dispatch(): a line starting with one of these (exact case, whole word) is no request.
CONSOLE_WORDS = frozenset(
    {"help", "status", "screen", "audit", "objects", "estop", "exit", "run", "cellmute", "tele", "tape",
     "teletick", "confirmrace", "confirmgap", "panic", "agent-test", "agent-nocap"}
)  # fmt: skip
_ANSWERS = frozenset({"y", "n", "yes", "no"})
_WAKE = re.compile(r"^(?:hey\s+)?ring[\s-]*(?:3|three)\b[\s,.:;!?-]*", re.IGNORECASE)


def sanitise(raw: str) -> str:
    """Printable ASCII on one line: whitespace -> one space, everything else outside 0x20..0x7E dropped."""
    out = "".join(" " if c.isspace() else c for c in raw if c.isspace() or 0x20 <= ord(c) < 0x7F)
    return " ".join(out.split())


def strip_wake_word(text: str) -> str:
    return _WAKE.sub("", text, count=1)


def is_bare_answer(text: str) -> bool:
    """Only y / n / yes / no, in any case or punctuation, spaced out or repeated."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return bool(words) and ("".join(words) in _ANSWERS or all(w in _ANSWERS for w in words))


def is_console_command(text: str) -> bool:
    """The same reading as the kernel's word(): exact case, the word followed by a space or the line's end.
    ``estop`` alone is a request; ``estop reset`` is the command."""
    first, _, rest = text.partition(" ")
    if first == "estop":
        return rest.split(" ")[0] == "reset"
    return first in CONSOLE_WORDS


@dataclass(frozen=True)
class Cleaned:
    text: str  # sanitised, wake word gone, at most MAX_LINE characters: fit for the screen
    refused: str = ""  # "" or one of REFUSED_*

    @property
    def line(self) -> str | None:
        return None if self.refused else self.text


def clean_transcript(raw: str) -> Cleaned:
    if len(raw) > MAX_RAW:
        return Cleaned(sanitise(raw[:MAX_RAW])[:MAX_LINE], REFUSED_LONG)
    text = strip_wake_word(sanitise(raw))
    if not text:
        return Cleaned("", REFUSED_EMPTY)
    if len(text) > MAX_LINE:
        return Cleaned(text[:MAX_LINE], REFUSED_LONG)
    if is_bare_answer(text):
        return Cleaned(text, REFUSED_ANSWER)
    if is_console_command(text):
        return Cleaned(text, REFUSED_COMMAND)
    return Cleaned(text)


# ---- the recorder: requests through a shared folder, answered by the Windows helper ----


class RecorderError(Exception):
    pass


class FolderRecorder:
    """``<id>.start`` -> the helper opens the microphone and answers ``<id>.rec`` -> ``<id>.stop`` ->
    ``<id>.wav``. ``<id>.cancel`` drops the recording; ``<id>.fail`` is the helper's error text. Everything
    with this id is deleted once it has been read: no recording stays in the folder."""

    def __init__(self, folder: Path, ack_s: float = 5.0, wav_s: float = 5.0) -> None:
        self.folder = Path(folder)
        self.ack_s = ack_s
        self.wav_s = wav_s
        self.nonce = ""

    def _p(self, suffix: str) -> Path:
        return self.folder / f"{self.nonce}.{suffix}"

    def _clean(self) -> None:
        for suffix in ("start", "stop", "rec", "fail", "wav"):
            self._p(suffix).unlink(missing_ok=True)

    def _failure(self) -> str:
        try:
            return self._p("fail").read_text("ascii", "replace")[:200].strip() or "no detail"
        except OSError:
            return ""

    def begin(self) -> None:
        """Returns once the helper reports the microphone open; the REC indicator waits for this."""
        self.nonce = secrets.token_hex(16)
        tmp = self._p("tmp")
        tmp.write_text(self.nonce, "ascii")
        os.replace(tmp, self._p("start"))
        end = time.monotonic() + self.ack_s
        while time.monotonic() < end:
            if self._p("rec").exists():
                return
            fail = self._failure()
            if fail:
                self._clean()
                raise RecorderError(f"microphone: {fail}")
            time.sleep(0.02)
        self._clean()  # a helper started later must not act on it
        raise RecorderError(
            f"no answer from the voice helper in {self.ack_s:g} s: is tools/voice_capture_win.py running?"
        )

    def stop(self) -> None:
        self._p("stop").write_text("", "ascii")

    def cancel(self) -> None:
        """Leaves ``.cancel`` for the helper (it deletes it, or removes it when it next starts)."""
        self._p("start").unlink(missing_ok=True)
        self._p("wav").unlink(missing_ok=True)
        self._p("cancel").write_text("", "ascii")

    def wait_wav(self) -> bytes:
        end = time.monotonic() + self.wav_s
        while time.monotonic() < end:
            fail = self._failure()
            if fail:
                self._clean()
                raise RecorderError(f"microphone: {fail}")
            if self._p("wav").exists():
                wav = self._p("wav").read_bytes()
                self._clean()
                return wav
            time.sleep(0.02)
        self.cancel()
        self._clean()
        raise RecorderError(f"no recording from the voice helper in {self.wav_s:g} s")


class LockedLog:
    """The worker thread and the console loop share one run log."""

    def __init__(self, log) -> None:  # noqa: ANN001
        self._log = log
        self._lock = threading.Lock()

    def append(self, rtype: str, **fields: object) -> dict:
        with self._lock:
            return self._log.append(rtype, **fields)

    def blob(self, data: bytes) -> str:
        with self._lock:
            return self._log.blob(data)


class VoiceService:
    """One recording at a time, on a worker thread. ``post`` delivers ``("rec",)``, ``("heard", Cleaned)`` or
    ``("error", text)`` to the console loop; after a ``cancel`` nothing more is delivered for that recording.
    A recording is cancelled before the call when it can be; a call already out finishes and is logged,
    unused."""

    def __init__(self, recorder, client, log, post: Callable[[tuple], None]) -> None:  # noqa: ANN001
        self.recorder = recorder
        self.client = client
        self.log = log
        self.post = post
        self._thread: threading.Thread | None = None
        self._release = threading.Event()
        self._cancelled = threading.Event()

    def begin(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self.post(("error", "the last recording is still finishing; press F2 again in a moment"))
            return
        self._release = threading.Event()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(target=self._work, args=(self._release, self._cancelled), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._release.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._release.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def note(self, kind: str, **fields: object) -> None:
        self.log.append(kind, **fields)

    def _work(self, release: threading.Event, cancelled: threading.Event) -> None:
        def deliver(event: tuple) -> None:
            if not cancelled.is_set():
                self.post(event)

        try:
            self.recorder.begin()
            deliver(("rec",))
            release.wait()
            if cancelled.is_set():
                self.recorder.cancel()
                return
            self.recorder.stop()
            wav = self.recorder.wait_wav()
            if cancelled.is_set():
                return
            heard = self.client.transcribe(wav)
            c = clean_transcript(heard.text)
            self.log.append("voice_heard", text=c.text, refused=c.refused, audio_ms=heard.audio_ms)
            deliver(("heard", c))
        except (RecorderError, AudioRefused, VoiceStop, OSError) as e:
            deliver(("error", sanitise(str(e))[:200]))
        except Exception as e:  # noqa: BLE001 - the keyboard must come back; only the type name goes on screen
            deliver(("error", f"internal error: {type(e).__name__}"))
