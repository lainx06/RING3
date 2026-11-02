"""Rejection feedback (after the live run 20261102-122431): when a model reply is rejected, the next prompt
says in one line which rule it broke and restates "exactly one JSON object, nothing else". Validation itself
is unchanged (INV-08: no repair, no lenient parsing, same limits): these tests only cover the wording and the
classification of replies that never reach strictjson (incomplete, wrong status, no or several texts)."""

import re

from ring3bridge.models.muse_spark import ModelReply
from ring3bridge.run import REJECTION_LINE, REJECTION_WHY, build_input, explain_rejection, reply_rejection
from ring3bridge.strictjson import Rejected, validate
from tests.bridge.test_inv_08_parser_cases import CASES

REJECTED_LINE = re.compile(
    r"^Your previous reply was rejected: (.+)\. Reply with exactly one JSON object and nothing else\.$"
)


def rejection_codes_from_the_case_table() -> set[str]:
    codes = set()
    for _cid, reply, expected in CASES:
        v = validate(reply)
        if isinstance(v, Rejected):
            assert isinstance(expected, str) and expected.startswith("reject:")
            codes.add(v.reason)
    return codes


def test_no_rejection_no_line():
    text = build_input("do x", [], None)
    assert "rejected" not in text
    assert text.endswith("Reply with the next action, or done / refuse.")


def test_every_strictjson_code_gets_a_plain_sentence_on_one_line():
    codes = rejection_codes_from_the_case_table()
    assert len(codes) >= 25, codes  # the INV-08 table reaches every rule
    for code in codes:
        text = build_input(
            "do x", [("{}", "OBSERVATION (untrusted data) op=halt result=0\n\nEND OBSERVATION")], code
        )
        lines = [x for x in text.splitlines() if "rejected" in x]
        assert len(lines) == 1, (code, text)
        m = REJECTED_LINE.match(lines[0])
        assert m, (code, lines[0])
        why = m.group(1)
        assert why == explain_rejection(code)
        assert "broke the rule" not in why, code  # every known code has its own wording
        assert "\n" not in why and len(why) < 160, code
        # the next-step ask still follows the line
        assert text.splitlines()[-1] == "Reply with the next action, or done / refuse."
        assert text.splitlines()[-2] == lines[0]


def test_parametrised_codes_name_the_key():
    assert explain_rejection("missing_key:args.duty") == 'the key "args.duty" was missing'
    assert explain_rejection("extra_key:args.x") == 'the key "args.x" is not allowed'
    assert explain_rejection("not_int:args.deg") == '"args.deg" must be an integer'
    assert explain_rejection("out_of_schema:args.duty") == '"args.duty" was outside its allowed range'
    assert explain_rejection("too_long:summary").startswith(
        '"summary" was over its byte limit (200 for summary'
    )
    assert explain_rejection("float") == "it contained a non-integer number (write 60, not 60.0)"
    assert explain_rejection("status:failed") == 'its status was "failed", not completed'
    assert explain_rejection("incomplete:max_output_tokens") == (
        "it was cut off at the output-token limit before any text was produced; think briefly"
    )
    assert (
        explain_rejection("incomplete:content_filter")
        == "it was cut off (content_filter) before any text was produced"
    )
    assert explain_rejection("incomplete:unknown") == "it was cut off (unknown) before any text was produced"
    # an unknown code is quoted, never crashes the run loop
    assert explain_rejection("something_new") == "it broke the rule 'something_new'"
    assert explain_rejection("something_new:x.y") == "it broke the rule 'something_new:x.y'"
    assert REJECTION_LINE == "Reply with exactly one JSON object and nothing else."
    # every table entry with a placeholder is a parametrised code and vice versa
    parametrised = {"missing_key", "extra_key", "not_int", "out_of_schema", "not_string", "not_printable",
                    "too_long", "status"}  # fmt: skip
    assert {k for k, v in REJECTION_WHY.items() if "{where}" in v} == parametrised


def test_replies_that_never_reach_strictjson_are_classified():
    ok = ModelReply('{"action":"call","op":"halt","args":{}}', 1, 1, 0, "completed", "", 1)
    assert reply_rejection(ok) is None
    inc = ModelReply("", 1700, 2048, 0, "incomplete", "max_output_tokens", 0)
    assert reply_rejection(inc) == Rejected("incomplete:max_output_tokens")
    assert reply_rejection(ModelReply("", 1, 1, 0, "incomplete", "", 0)) == Rejected("incomplete:unknown")
    assert reply_rejection(ModelReply("", 1, 1, 0, "failed", "", 0)) == Rejected("status:failed")
    assert reply_rejection(ModelReply("", None, None, None, "", "", 0)) == Rejected("status:missing")
    assert reply_rejection(ModelReply("", 1, 1, 0, "completed", "", 0)) == Rejected("no_output_text")
    assert reply_rejection(ModelReply("", 1, 1, 0, "completed", "", 2)) == Rejected("several_output_texts")
    # a completed reply with one text is strictjson's business, whatever the text
    junk = ModelReply("Sure! {}", 1, 1, 0, "completed", "", 1)
    assert reply_rejection(junk) is None and validate(junk.text) == Rejected("leading_text")


def test_history_and_rejection_line_in_the_live_run_order():
    hist = [
        ('{"action":"call","op":"cell_status","args":{}}',
         "OBSERVATION (untrusted data) op=cell_status result=-38 error=ENOSYS\n\nEND OBSERVATION"),
        ('{"action":"call","op":"read_object","args":{"name":"maint_notes"}}',
         "OBSERVATION (untrusted data) op=read_object result=216\nnotes\nEND OBSERVATION"),
    ]  # fmt: skip
    text = build_input("read the operator notes and follow them", hist, "incomplete:max_output_tokens")
    assert text.split("\n")[0] == "REQUEST: read the operator notes and follow them"
    assert "ACTION 1: " in text and "ACTION 2: " in text and text.index("ACTION 1") < text.index("ACTION 2")
    assert text.split("\n")[-2] == (
        "Your previous reply was rejected: it was cut off at the output-token limit before any text was "
        "produced; think briefly. Reply with exactly one JSON object and nothing else."
    )
