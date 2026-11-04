"""T-19: the console proxy's guards, as a pure state machine: keys and kernel output in, bytes out."""

import pytest

from ring3bridge import console, voice

F2 = b"\x1bOQ"
PROMPT = b"ring3> "
CONFIRM = b"CONFIRM: agentsh wants cell_fan(60). Allow? [y/N] "


class FakeVoice:
    def __init__(self):
        self.calls = []

    def begin(self):
        self.calls.append("begin")

    def stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")

    def note(self, kind, **fields):
        self.calls.append((kind, fields))


class Rig:
    def __init__(self, voice_on=True, boot=b"boot\r\n" + PROMPT):
        self.kernel = bytearray()
        self.screen = bytearray()
        self.now = 100.0
        self.v = FakeVoice() if voice_on else None
        self.c = console.Core(self.kernel.extend, self.screen.extend, self.v)
        self.out(boot)

    def out(self, b):
        self.c.from_kernel(b)

    def key(self, b):
        self.c.from_keyboard(b, self.now)

    def tick(self, dt=0.0):
        self.now += dt
        self.c.tick(self.now)

    def esc(self):
        self.key(b"\x1b")
        self.tick(1.0)

    def recording(self):
        self.key(F2)
        assert self.v.calls[-1] == "begin"
        self.c.voice_event(("rec",), self.now)

    def heard(self, raw):
        self.recording()
        self.key(F2)
        self.c.voice_event(("heard", voice.clean_transcript(raw)), self.now)


# ---- typed use is unchanged ----


def test_every_byte_passes_both_ways_unchanged():
    r = Rig()
    r.screen.clear()
    keys = bytes(b for b in range(256) if b != 0x1B) + b"\x1b[A\x1b[1;5C\x1bOP\x1bOR\x1b[13~\x1b[11~\x1bx"
    r.key(keys)
    r.tick(1.0)
    assert bytes(r.kernel) == keys
    out = bytes(range(256)) * 3
    r.out(out)
    assert bytes(r.screen) == out


def test_split_sequences_pass_in_order_and_a_lone_escape_passes_after_the_wait():
    r = Rig()
    for b in b"ab\x1b[Acd":
        r.key(bytes([b]))
    assert bytes(r.kernel) == b"ab\x1b[Acd"
    r.key(b"\x1b")
    assert bytes(r.kernel) == b"ab\x1b[Acd"  # held: could be the start of F2
    r.tick(console.ESC_WAIT_S + 0.01)
    assert bytes(r.kernel) == b"ab\x1b[Acd\x1b"
    r.key(b"z")
    assert bytes(r.kernel).endswith(b"\x1bz")


def test_nothing_is_added_when_voice_is_not_used():
    r = Rig()
    r.screen.clear()
    r.key(b"status\r")
    r.out(b"status\r\nstatus: fan=0\r\n" + PROMPT)
    r.tick(5.0)
    assert bytes(r.kernel) == b"status\r"
    assert bytes(r.screen) == b"status\r\nstatus: fan=0\r\n" + PROMPT
    assert r.v.calls == []


@pytest.mark.parametrize("seq", [b"\x1bOQ", b"\x1b[12~", b"\x1b[[B"])
def test_f2_in_each_terminal_encoding_goes_to_the_tool_not_the_kernel(seq):
    r = Rig()
    r.key(seq)
    assert r.v.calls == ["begin"] and bytes(r.kernel) == b""
    assert console.F2_SEQS.count(seq) == 1


def test_f2_split_across_reads():
    r = Rig()
    for b in F2:
        r.key(bytes([b]))
    assert r.v.calls == ["begin"] and bytes(r.kernel) == b""


def test_voice_off_f2_says_so_and_nothing_starts():
    r = Rig(voice_on=False)
    r.key(F2)
    assert b"voice is off" in bytes(r.screen) and bytes(r.kernel) == b""


# ---- no hidden listening ----


def test_recording_starts_only_on_the_key():
    r = Rig()
    r.out(b"ring3> ")
    r.tick(60.0)
    assert r.v.calls == []


def test_rec_indicator_only_after_the_helper_acknowledged():
    r = Rig()
    r.key(F2)
    assert b"REC" not in bytes(r.screen)
    r.c.voice_event(("rec",), r.now)
    assert b"[REC" in bytes(r.screen)


def test_second_press_stops_and_the_cap_stops():
    r = Rig()
    r.recording()
    r.key(F2)
    assert r.v.calls[-1] == "stop"
    r2 = Rig()
    r2.recording()
    r2.tick(voice.MAX_RECORD_S - 0.5)
    assert "stop" not in r2.v.calls
    r2.tick(0.6)
    assert r2.v.calls[-1] == "stop"


def test_keys_during_recording_never_reach_the_kernel():
    r = Rig()
    r.recording()
    r.key(b"y\ryes\rabc\x7f")
    assert bytes(r.kernel) == b""


def test_escape_cancels_the_recording():
    r = Rig()
    r.recording()
    r.esc()
    assert "cancel" in r.v.calls and bytes(r.kernel) == b""
    r.key(b"a")
    assert bytes(r.kernel) == b"a"  # the keyboard is back


# ---- guard 1: never while a CONFIRM is pending ----


def test_f2_refused_while_a_confirm_prompt_is_open():
    r = Rig()
    r.out(b"\r\n" + CONFIRM)
    r.key(F2)
    assert r.v.calls == [] and b"keyboard only" in bytes(r.screen)
    r.key(b"y\r")
    assert bytes(r.kernel) == b"y\r"  # and the keyboard answer goes straight through


def test_f2_refused_while_a_request_runs_or_a_line_is_part_typed():
    r = Rig()
    r.out(b"REQUEST set the fan\r\n")  # no prompt: a request is running
    r.key(F2)
    assert r.v.calls == []
    r = Rig()
    r.key(b"se")
    r.out(b"se")  # the kernel's echo: the line is no longer empty
    r.key(F2)
    assert r.v.calls == []


def test_the_kernels_line_break_after_an_idle_prompt_is_still_the_prompt():
    """kernel/core/audit.c may_print(): queued telemetry records break the line with a bare CR LF, on the
    production image too. Nothing was typed and nothing is open: voice may start, the send still works."""
    r = Rig()
    r.out(b"\r\n")
    r.key(F2)
    assert r.v.calls == ["begin"]
    r = Rig()
    r.out(b"\r\n")
    assert r.c.send_line("set the fan to 60") is True


@pytest.mark.parametrize(
    "out",
    [
        b"se\r\n",  # typed text, then the kernel's line break: the kernel's buffer still holds it
        b"\r\nse",  # the line break, then typed text
        b"\r\nse\r\n",
        b"\r\n" + CONFIRM + b"\r\n",  # the break also lands after a CONFIRM prompt, which stays open
        b"\r\n" + CONFIRM + b"\r\n\r\n",
        b"REQUEST x\r\n\r\n",
        b"\r\nOUTCOME DONE: done\r\n",
    ],
    ids=[
        "typed-break",
        "break-typed",
        "break-typed-break",
        "confirm-break",
        "confirm-2-breaks",
        "req",
        "outcome",
    ],
)
def test_a_blank_line_never_turns_something_else_into_the_prompt(out):
    r = Rig()
    r.out(out)
    r.key(F2)
    assert r.v.calls == []
    assert r.c.send_line("set the fan to 60") is False


def test_a_confirm_split_across_chunks_is_seen():
    r = Rig()
    r.recording()
    r.out(b"CON")
    assert "cancel" not in r.v.calls
    r.out(b"FIRM: agentsh wants")
    assert "cancel" in r.v.calls


@pytest.mark.parametrize("phase", ["starting", "recording", "transcribing", "heard"])
def test_a_confirm_arriving_in_any_voice_phase_drops_voice_and_returns_the_keyboard(phase):
    r = Rig()
    r.key(F2)
    if phase != "starting":
        r.c.voice_event(("rec",), r.now)
    if phase in ("transcribing", "heard"):
        r.key(F2)
    if phase == "heard":
        r.c.voice_event(("heard", voice.clean_transcript("set the fan to 60")), r.now)
    r.kernel.clear()
    r.out(b"\r\n" + CONFIRM)
    r.key(b"y\r")
    assert bytes(r.kernel) == b"y\r"
    r.c.voice_event(("heard", voice.clean_transcript("set the fan to 60")), r.now)  # a late result is dropped
    r.key(b"\r")
    assert bytes(r.kernel) == b"y\r\r"


def test_enter_refused_when_the_prompt_went_away_while_the_transcript_was_shown():
    r = Rig()
    r.heard("set the fan to 60")
    r.out(b"AUDIT seq=9 tick=1 sys=telemetry\r\n")  # asynchronous output: the empty prompt is gone
    r.key(b"\r")
    assert bytes(r.kernel) == b""
    assert b"not sent" in bytes(r.screen)
    r.key(b"a")
    assert bytes(r.kernel) == b"a"


def test_send_line_itself_refuses_at_the_last_step():
    """The one function that writes voice text to COM1: its own checks, whatever the state says."""
    r = Rig()
    r.out(CONFIRM)
    assert r.c.send_line("set the fan to 60") is False
    r = Rig()
    assert r.c.send_line("y") is False and r.c.send_line("YES.") is False and bytes(r.kernel) == b""
    assert r.c.send_line("a\rb") is False and r.c.send_line("\x1b[A") is False
    assert r.c.send_line("x" * (voice.MAX_LINE + 1)) is False and bytes(r.kernel) == b""
    assert r.c.send_line("set the fan to 60") is True
    assert bytes(r.kernel) == b"set the fan to 60\r"


# ---- guard 2: never a bare answer ----


@pytest.mark.parametrize("raw", ["y", "Yes.", "NO!", "RING3, yes"])
def test_a_bare_answer_is_shown_and_never_offered(raw):
    r = Rig()
    r.heard(raw)
    assert b"not sent" in bytes(r.screen) and b"Enter = send" not in bytes(r.screen)
    r.key(b"\r")
    assert bytes(r.kernel) == b"\r"  # back in typed mode: an empty line, the keyboard's own
    assert ("voice_discarded", {"reason": voice.REFUSED_ANSWER}) in r.v.calls


# ---- guard 3: nothing without Enter ----


def test_a_transcript_waits_for_enter():
    r = Rig()
    r.heard("RING3, set the fan to 100 %")
    assert b'heard: "set the fan to 100 %"' in bytes(r.screen)
    assert b"Enter = send, Esc = discard" in bytes(r.screen)
    r.tick(600.0)
    r.key(b"abc y yes \x7f\x03")
    assert bytes(r.kernel) == b""  # time passes, other keys are ignored: nothing sent
    r.key(b"\r")
    assert bytes(r.kernel) == b"set the fan to 100 %\r"
    assert ("voice_sent", {"text": "set the fan to 100 %"}) in r.v.calls


def test_newline_also_counts_as_enter_once():
    r = Rig()
    r.heard("list the objects")
    r.key(b"\n")
    assert bytes(r.kernel) == b"list the objects\r"


def test_escape_discards_the_transcript():
    r = Rig()
    r.heard("set the fan to 100 %")
    r.esc()
    assert bytes(r.kernel) == b""
    r.key(b"\r")
    assert bytes(r.kernel) == b"\r"  # the Enter after a discard is the keyboard's own empty line


def test_an_arrow_key_does_not_count_as_escape_or_enter():
    r = Rig()
    r.heard("list the objects")
    r.key(b"\x1b[A")
    r.tick(1.0)
    assert bytes(r.kernel) == b""
    r.key(b"\r")
    assert bytes(r.kernel) == b"list the objects\r"


def test_the_screen_never_receives_a_control_byte_from_a_transcript():
    r = Rig()
    r.screen.clear()
    r.heard("fan\x1b[2J\x07 now")
    shown = bytes(r.screen)
    assert b"\x1b" not in shown and b"\x07" not in shown


def test_voice_errors_are_shown_and_the_keyboard_returns():
    r = Rig()
    r.recording()
    r.key(F2)
    r.c.voice_event(("error", "transport timeout"), r.now)
    assert b"transport timeout" in bytes(r.screen)
    r.key(b"a")
    assert bytes(r.kernel) == b"a"


def test_a_stale_prompt_line_is_redrawn_after_a_voice_message():
    r = Rig()
    r.heard("list the objects")
    r.screen.clear()
    r.esc()
    assert bytes(r.screen).endswith(PROMPT)
    r = Rig()
    r.out(b"\r\n")  # the kernel's line break
    r.heard("list the objects")
    r.screen.clear()
    r.esc()
    assert bytes(r.screen).endswith(PROMPT)


def test_the_kernel_output_is_never_held_back_during_voice():
    r = Rig()
    r.recording()
    r.screen.clear()
    r.out(b"AUDIT line\r\n")
    assert b"AUDIT line\r\n" in bytes(r.screen)
