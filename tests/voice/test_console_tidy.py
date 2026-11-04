"""T-19 option `ring3 console --tidy-echo`: the kernel's empty line breaks during typing are not drawn.
kernel/core/audit.c may_print() breaks the input line for every queued telemetry record, with or without the
proxy, so typed characters land one per line. The flag only ever drops a CR LF at an open input line: no text,
and off by default (`test_console_core.py` holds the default to byte for byte)."""

import random

import pytest

from ring3bridge import console

PROMPT = b"ring3> "
BREAK = b"\r\n"
CONFIRM = b"CONFIRM: agentsh wants cell_fan(60). Allow? [y/N] "


class Voice:
    def __init__(self):
        self.calls = []

    def begin(self):
        self.calls.append("begin")

    def stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")

    def note(self, kind, **fields):
        self.calls.append(kind)


class Rig:
    def __init__(self, tidy=True):
        self.kernel = bytearray()
        self.screen = bytearray()
        self.v = Voice()
        self.c = console.Core(self.kernel.extend, self.screen.extend, self.v, tidy=tidy)
        self.c.from_kernel(b"boot\r\n" + PROMPT)
        self.screen.clear()

    def type(self, text):
        """Each key, its echo, then the kernel's line break, as on the real console."""
        for ch in text:
            self.c.from_keyboard(ch.encode(), 0.0)
            self.c.from_kernel(ch.encode())
            self.c.from_kernel(BREAK)


def test_typed_characters_stay_on_one_line():
    r = Rig()
    r.c.from_kernel(BREAK)  # a break before the first key too
    r.type("list")
    assert bytes(r.screen) == b"list"
    assert bytes(r.kernel) == b"list"


def test_the_flag_is_what_changes_it():
    r = Rig(tidy=False)
    r.type("ab")
    assert bytes(r.screen) == b"a\r\nb\r\n"


def test_enter_and_what_follows_are_untouched():
    r = Rig()
    r.type("status")
    r.screen.clear()
    r.c.from_keyboard(b"\r", 0.0)
    r.c.from_kernel(b"\r\nstatus: fan=0\r\n\r\n" + PROMPT)
    assert bytes(r.screen) == b"\r\nstatus: fan=0\r\n\r\n" + PROMPT


def test_foreign_output_gets_its_own_line_and_nothing_is_lost():
    r = Rig()
    r.type("ab")
    r.c.from_kernel(b"AUDIT seq=1\r\nAUDIT seq=2\r\n")
    assert bytes(r.screen) == b"ab\r\nAUDIT seq=1\r\nAUDIT seq=2\r\n"


def test_a_break_split_across_chunks_is_dropped_and_a_lone_cr_is_kept():
    r = Rig()
    r.c.from_kernel(b"\r")
    r.c.from_kernel(b"\n")
    assert bytes(r.screen) == b""
    r = Rig()
    r.c.from_kernel(b"\r")
    r.c.from_kernel(b"X")
    assert bytes(r.screen) == b"\rX"


def test_a_cr_held_when_enter_is_pressed_is_not_lost():
    r = Rig()
    r.c.from_kernel(b"\r")
    r.c.from_keyboard(b"\r", 0.0)
    r.c.from_kernel(b"\n")
    assert bytes(r.screen) == b"\r\n"


def test_a_confirm_prompt_is_shown_whole_and_its_line_is_not_touched():
    r = Rig()
    r.c.from_keyboard(b"x\r", 0.0)
    r.c.from_kernel(b"x\r\nREQUEST x\r\n" + CONFIRM + BREAK + b"y\r\n")
    assert bytes(r.screen) == b"x\r\nREQUEST x\r\n" + CONFIRM + BREAK + b"y\r\n"


def test_a_confirm_during_typing_ends_tidying_and_stays_visible():
    r = Rig()
    r.type("ab")
    r.c.from_kernel(CONFIRM)
    r.c.from_kernel(BREAK)
    assert bytes(r.screen) == b"ab\r\n" + CONFIRM + BREAK


def test_the_guards_read_the_raw_output_not_the_tidied_screen():
    r = Rig()
    r.c.from_kernel(BREAK)
    r.c.from_keyboard(b"\x1bOQ", 0.0)  # idle prompt, with a dropped break: voice may start
    assert r.v.calls == ["begin"]
    r = Rig()
    r.type("se")
    r.c.from_keyboard(b"\x1bOQ", 0.0)  # part-typed: refused, the line is redrawn whole under the message
    assert r.v.calls == []
    assert bytes(r.screen).endswith(b"\r\n" + PROMPT + b"se")
    assert r.c.send_line("set the fan to 60") is False
    r = Rig()
    r.c.from_kernel(CONFIRM + BREAK)
    assert r.c.send_line("set the fan to 60") is False


def test_a_voice_line_ends_the_input_line():
    r = Rig()
    r.c.from_kernel(BREAK)
    assert r.c.send_line("list the objects") is True
    r.screen.clear()
    r.c.from_kernel(b"list the objects\r\nREQUEST list the objects\r\n")
    assert bytes(r.screen) == b"list the objects\r\nREQUEST list the objects\r\n"


@pytest.mark.parametrize("seed", range(20))
def test_only_cr_lf_ever_goes_missing(seed):
    """Any kernel output, any chunking: without CR and LF, the screen holds exactly what the kernel sent."""
    rng = random.Random(seed)
    r = Rig()
    sent = bytearray()
    pieces = [b"\r\n", b"\r", b"\n", PROMPT, b"\r\n" + PROMPT, b"abc", CONFIRM, b"AUDIT x", b"\x1b\x07"]
    pieces.append(bytes([rng.randrange(256)]))
    for _ in range(300):
        p = rng.choice(pieces)
        if rng.random() < 0.3:
            r.c.from_keyboard(rng.choice([b"a", b"b", b"\r", b"zz"]), 0.0)
        sent += p
        r.c.from_kernel(p)
    strip = bytes.maketrans(b"", b"")
    want = bytes(sent).translate(strip, b"\r\n")
    assert bytes(r.screen).translate(strip, b"\r\n") == want
