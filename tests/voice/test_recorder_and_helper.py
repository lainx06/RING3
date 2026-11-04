"""T-19: the WSL recorder and the Windows helper, talking through a shared folder (no microphone)."""

import io
import struct
import threading
import time
import wave

import pytest
import voice_capture_win as helper

from ring3bridge import console, voice
from ring3bridge.eventlog import EventLog
from ring3bridge.models.transcribe import MAX_SECONDS, check_wav

N = "a" * 32


class FakeStream:
    """A microphone: hands out 16-bit samples; the helper must close it."""

    def __init__(self, rate=24000, sample=1000, fail_after=None):
        self.rate = rate
        self.sample = sample
        self.frames = 0
        self.closed = False
        self.fail_after = fail_after

    def read(self, frames):
        if self.fail_after is not None and self.frames >= self.fail_after:
            raise OSError("device lost")
        self.frames += frames
        return struct.pack("<h", self.sample) * frames

    def close(self):
        self.closed = True


class Mic:
    def __init__(self, **kw):
        self.kw = kw
        self.opened = []

    def __call__(self):
        s = FakeStream(**self.kw)
        self.opened.append(s)
        return s


def start_file(folder, nonce=N, body=None):
    (folder / f"{nonce}.start").write_text(nonce if body is None else body)


# ---- helper ----


def test_no_request_no_microphone(tmp_path):
    mic = Mic()
    for _ in range(3):
        assert helper.serve_once(tmp_path, mic) == 0
    assert mic.opened == []


def test_requests_present_at_helper_start_are_deleted_not_recorded(tmp_path):
    start_file(tmp_path)
    (tmp_path / f"{N}.wav").write_bytes(b"old voice")
    mic = Mic()
    helper.drop_leftovers(tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert helper.serve_once(tmp_path, mic) == 0 and mic.opened == []


def test_stop_file_ends_the_recording_and_the_mic_closes(tmp_path):
    start_file(tmp_path)
    mic = Mic()
    chunks = []

    def on_chunk(frames):
        chunks.append(frames)
        if len(chunks) == 5:
            (tmp_path / f"{N}.stop").write_text("")

    assert helper.serve_once(tmp_path, mic, on_chunk=on_chunk) == 1
    assert len(mic.opened) == 1 and mic.opened[0].closed
    assert (tmp_path / f"{N}.rec").exists()
    with wave.open(str(tmp_path / f"{N}.wav")) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 24000
        assert w.getnframes() == 5 * helper.chunk_frames(24000)
    assert not (tmp_path / f"{N}.start").exists() and not (tmp_path / f"{N}.stop").exists()


def test_the_helper_caps_at_15_s_without_a_stop(tmp_path):
    start_file(tmp_path)
    mic = Mic()
    assert helper.serve_once(tmp_path, mic) == 1
    with wave.open(str(tmp_path / f"{N}.wav")) as w:
        assert w.getnframes() == helper.MAX_SECONDS * 24000
    assert helper.MAX_SECONDS == MAX_SECONDS
    check_wav((tmp_path / f"{N}.wav").read_bytes())  # the bridge accepts what the helper writes at the cap


def test_cancel_writes_no_audio(tmp_path):
    start_file(tmp_path)
    mic = Mic()
    n = []

    def on_chunk(_f):
        n.append(1)
        (tmp_path / f"{N}.cancel").write_text("")

    assert helper.serve_once(tmp_path, mic, on_chunk=on_chunk) == 0
    assert not list(tmp_path.glob("*.wav")) and mic.opened[0].closed and len(n) == 1


def test_bad_request_names_and_bodies_are_ignored(tmp_path):
    (tmp_path / "..evil.start").write_text("x")
    (tmp_path / "ABC.start").write_text("ABC")
    start_file(tmp_path, body="another body")
    mic = Mic()
    assert helper.serve_once(tmp_path, mic) == 0 and mic.opened == []


def test_microphone_failure_is_reported_in_a_file_and_closes(tmp_path):
    start_file(tmp_path)
    mic = Mic(fail_after=0)
    assert helper.serve_once(tmp_path, mic) == 0
    assert "device lost" in (tmp_path / f"{N}.fail").read_text()
    assert mic.opened[0].closed and not list(tmp_path.glob("*.wav"))


def test_open_failure_is_reported(tmp_path):
    def nope():
        raise OSError("no input device")

    start_file(tmp_path)
    assert helper.serve_once(tmp_path, nope) == 0
    assert "no input device" in (tmp_path / f"{N}.fail").read_text()


def test_selftest_records_2_s_and_prints_level_and_duration():
    lines = []
    assert helper.selftest(Mic(sample=3276), log=lines.append, name="C922") == 0
    text = "\n".join(lines)
    assert "REC" in text and "2.00 s" in text and "peak 10 %" in text and "C922" in text


def test_selftest_on_a_silent_microphone_says_so():
    lines = []
    assert helper.selftest(Mic(sample=0), log=lines.append) == 1
    assert "silent" in "\n".join(lines)


def test_device_choice_prefers_the_c922_then_the_default():
    devs = [
        {"name": "Speakers", "max_input_channels": 0},
        {"name": "Microphone (Realtek)", "max_input_channels": 2},
        {"name": "Microphone (C922 Pro Stream Web", "max_input_channels": 2},
    ]
    assert helper.choose_device(devs, "C922") == (2, "Microphone (C922 Pro Stream Web")
    assert helper.choose_device(devs[:2], "C922") == (None, "default input")


def test_helper_source_has_no_network_and_runs_nothing():
    src = helper.__doc__ + open(helper.__file__).read()
    for word in ("socket", "subprocess", "urllib", "http", "eval(", "exec("):
        assert word not in src.replace("no network", "").replace("https", ""), word


# ---- recorder <-> helper, both real, two threads ----


class HelperThread:
    def __init__(self, folder, mic=None):
        self.folder, self.mic = folder, mic or Mic()
        self.done = threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self.done.is_set():
            helper.serve_once(self.folder, self.mic, on_chunk=lambda _f: time.sleep(0.01))
            time.sleep(0.005)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.done.set()
        self.t.join(5)


def test_round_trip_and_nothing_left_in_the_folder(tmp_path):
    with HelperThread(tmp_path):
        r = voice.FolderRecorder(tmp_path)
        r.begin()
        time.sleep(0.05)
        r.stop()
        wav = r.wait_wav()
    info = check_wav(wav)
    assert info.rate == 24000 and info.frames > 0
    assert list(tmp_path.iterdir()) == []  # no recording stays behind


def test_cancel_round_trip(tmp_path):
    with HelperThread(tmp_path):
        r = voice.FolderRecorder(tmp_path)
        r.begin()
        r.cancel()
        time.sleep(0.2)
    assert list(tmp_path.iterdir()) == []


def test_a_cancel_after_the_recording_ended_leaves_no_audio(tmp_path):
    (tmp_path / f"{N}.wav").write_bytes(b"voice")
    (tmp_path / f"{N}.cancel").write_text("")
    assert helper.serve_once(tmp_path, Mic()) == 0
    assert list(tmp_path.iterdir()) == []


def test_no_helper_is_a_clear_error_and_leaves_no_request(tmp_path):
    r = voice.FolderRecorder(tmp_path, ack_s=0.1)
    with pytest.raises(voice.RecorderError, match="helper"):
        r.begin()
    assert not list(tmp_path.glob("*.start"))  # a helper started later must not act on it


def test_helper_failure_comes_back_as_a_recorder_error(tmp_path):
    with HelperThread(tmp_path, Mic(fail_after=0)):
        r = voice.FolderRecorder(tmp_path)
        with pytest.raises(voice.RecorderError, match="device lost"):
            r.begin()
            r.stop()
            r.wait_wav()
    assert list(tmp_path.iterdir()) == []


def test_helper_writes_nothing_is_a_timeout(tmp_path):
    r = voice.FolderRecorder(tmp_path, ack_s=0.2, wav_s=0.1)

    # a helper that only acknowledges
    def ack():
        while not list(tmp_path.glob("*.start")):
            time.sleep(0.005)
        s = next(tmp_path.glob("*.start"))
        (tmp_path / (s.stem + ".rec")).write_text("24000")

    threading.Thread(target=ack, daemon=True).start()
    r.begin()
    r.stop()
    with pytest.raises(voice.RecorderError, match="no recording"):
        r.wait_wav()


# ---- the worker: order of events, reservation after a real file, spoken text to a cleaned line ----


class ScriptedClient:
    def __init__(self, result):
        self.result = result
        self.wavs = []

    def transcribe(self, wav):
        self.wavs.append(wav)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRecorder:
    def __init__(self, wav=b"WAV", fail=None):
        self.wav, self.fail = wav, fail
        self.log = []

    def begin(self):
        self.log.append("begin")
        if self.fail:
            raise voice.RecorderError(self.fail)

    def stop(self):
        self.log.append("stop")

    def cancel(self):
        self.log.append("cancel")

    def wait_wav(self):
        self.log.append("wait")
        return self.wav


def wait_event(q, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if q:
            return q.pop(0)
        time.sleep(0.005)
    raise AssertionError("no event")


def service(tmp_path, rec, client):
    log = EventLog(tmp_path / "run")
    log.append("run_start", run_id="r", kind="voice_console")
    events = []
    return voice.VoiceService(rec, client, log, events.append), events, log


def test_worker_heard(tmp_path):
    from ring3bridge.models.transcribe import Heard

    rec = FakeRecorder()
    client = ScriptedClient(Heard("RING3, set the fan to 100 %", 2000))
    s, ev, _log = service(tmp_path, rec, client)
    s.begin()
    assert wait_event(ev) == ("rec",)
    s.stop()
    kind, cleaned = wait_event(ev)
    assert kind == "heard" and cleaned.line == "set the fan to 100 %"
    assert rec.log == ["begin", "stop", "wait"] and client.wavs == [b"WAV"]


def test_worker_cancel_makes_no_call(tmp_path):
    from ring3bridge.models.transcribe import Heard

    rec = FakeRecorder()
    client = ScriptedClient(Heard("x", 1))
    s, ev, _log = service(tmp_path, rec, client)
    s.begin()
    assert wait_event(ev) == ("rec",)
    s.cancel()
    s.join(5)
    assert rec.log == ["begin", "cancel"] and client.wavs == [] and ev == []


@pytest.mark.parametrize("recfail,clientres", [("no helper", None), (None, "stop")])
def test_worker_errors(tmp_path, recfail, clientres):
    from ring3bridge.models.transcribe import VoiceStop

    rec = FakeRecorder(fail=recfail)
    client = ScriptedClient(VoiceStop("HTTP 500"))
    s, ev, _log = service(tmp_path, rec, client)
    s.begin()
    if not recfail:
        assert wait_event(ev) == ("rec",)
        s.stop()
    kind, msg = wait_event(ev)
    assert kind == "error" and msg


def test_helper_wav_is_a_plain_header():
    b = io.BytesIO()
    helper.write_wav(b, 24000, b"\x01\x02" * 10)
    assert b.getvalue().startswith(b"RIFF") and b"WAVE" in b.getvalue()[:16]


def test_cap_constants_agree():
    assert voice.MAX_RECORD_S == MAX_SECONDS == helper.MAX_SECONDS
    assert console.F2_SEQS
