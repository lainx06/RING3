"""T-19: what a transcript may become. Untrusted text -> one printable-ASCII request line, or nothing."""

import re

import pytest

from ring3bridge import voice
from tests.qemu import REPO


@pytest.mark.parametrize(
    "raw,want",
    [
        ("RING3, set the fan to 100 %", "set the fan to 100 %"),
        ("ring3 set the fan to 100 %", "set the fan to 100 %"),
        ("Ring three, set the fan to 100 %.", "set the fan to 100 %."),
        ("ring three: list the objects", "list the objects"),
        ("Ring 3. list the objects", "list the objects"),
        ("Ring-3, list the objects", "list the objects"),
        ("Hey RING3, list the objects", "list the objects"),
        ("set the fan to 60 %", "set the fan to 60 %"),
        ("ring 30 bells", "ring 30 bells"),  # not the wake word
        ("please ring three, then stop", "please ring three, then stop"),  # only a leading one
    ],
)
def test_wake_word(raw, want):
    c = voice.clean_transcript(raw)
    assert (c.text, c.refused) == (want, "")


@pytest.mark.parametrize(
    "raw",
    [
        "y", "Y", "n", "N", "yes", "YES", "Yes.", "no", "No!", "NO?", " y ", "y.", "\"yes\"", "(no)", "y e s",
        "yes, yes", "no no", "Y!!!", "RING3, yes.", "ring three, N", "yes\n", "\tno\r\n", "ye\x1bs", "yé",
    ],
)  # fmt: skip
def test_bare_confirmation_words_are_never_a_line(raw):
    c = voice.clean_transcript(raw)
    assert c.refused == voice.REFUSED_ANSWER, (raw, c)
    assert c.line is None


@pytest.mark.parametrize(
    "raw", ["yes please set the fan to 60", "no fan today", "nothing", "yesterday", "any"]
)
def test_a_request_that_merely_starts_like_an_answer_is_a_request(raw):
    assert voice.clean_transcript(raw).line == raw


@pytest.mark.parametrize(
    "raw,want",
    [
        ("list\nthe\tobjects", "list the objects"),
        ("list\r\nthe objects\r\n", "list the objects"),
        (
            "fan \x1b[31mred\x1b[0m now",
            "fan [31mred[0m now",
        ),  # ESC gone: nothing reaches the terminal as a code
        ("a\x00b\x07c\x7fd", "abcd"),
        ("café fan — 60  %", "caf fan 60 %"),  # non-ASCII dropped, Unicode space -> space
        ("  many    spaces  ", "many spaces"),
    ],
)
def test_sanitise_is_printable_ascii_on_one_line(raw, want):
    c = voice.clean_transcript(raw)
    assert c.text == want
    assert all(0x20 <= ord(ch) < 0x7F for ch in c.text)


def test_length_is_bounded_by_the_kernels_line():
    m = re.search(r"#define CONSOLE_LINE_MAX (\d+)u", (REPO / "kernel/include/console.h").read_text())
    assert m and int(m.group(1)) == voice.MAX_LINE  # beyond it the kernel drops characters silently
    assert voice.clean_transcript("x" * voice.MAX_LINE).line == "x" * voice.MAX_LINE
    c = voice.clean_transcript("x" * (voice.MAX_LINE + 1))
    assert c.refused == voice.REFUSED_LONG and c.line is None
    huge = voice.clean_transcript("x" * (1 << 20))
    assert huge.refused == voice.REFUSED_LONG and len(huge.text) <= voice.MAX_LINE


@pytest.mark.parametrize("raw", ["", "   ", "\x00\x01", "RING3,", "ring three.", "éé"])
def test_empty_is_nothing(raw):
    c = voice.clean_transcript(raw)
    assert c.refused == voice.REFUSED_EMPTY and c.line is None


@pytest.mark.parametrize(
    "raw",
    ["exit", "help", "status", "audit", "objects", "screen", "estop reset", "estop  reset", "run badptr",
     "RING3, exit", "ring three, estop reset", "tele 50 0", "panic"],
)  # fmt: skip
def test_a_console_command_is_not_a_request_line(raw):
    """The card: the transcript goes in as a request line. A kernel console word would be a command."""
    c = voice.clean_transcript(raw)
    assert c.refused == voice.REFUSED_COMMAND and c.line is None


@pytest.mark.parametrize("raw", ["Exit the room", "status of the fan?", "statuses", "estop", "estop the fan"])
def test_words_the_kernel_would_read_as_a_request_stay_requests(raw):
    """The kernel's word(): exact case, whole word ("status of ..." is read as the command, below)."""
    c = voice.clean_transcript(raw)
    if raw.startswith("status "):
        assert c.refused == voice.REFUSED_COMMAND  # the kernel would run `status` and ignore the rest
    else:
        assert c.line == raw


def test_the_command_list_matches_the_kernel():
    src = (REPO / "kernel/core/console.c").read_text()
    body = src[src.index("static void dispatch") : src.index("void console_run")]
    assert set(re.findall(r'word\(l, "([^"]+)", &rest\)', body)) == voice.CONSOLE_WORDS
