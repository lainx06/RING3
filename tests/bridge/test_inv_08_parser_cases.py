"""INV-08: model replies are validated exactly. One case table (also rendered to the evidence file:
``uv run python -m tests.bridge.test_inv_08_parser_cases > docs/evidence/T-03a/strictjson-cases.md``)."""

import pytest

from ring3bridge.strictjson import Call, Done, Refuse, Rejected, validate

C = Call
OK_CALL = '{"action":"call","op":"fan_set","args":{"duty":60}}'

# (id, reply, expected): expected is a Call/Done/Refuse, or "reject:<reason prefix>".
CASES: list[tuple[str, str | bytes, object]] = [
    # ---- accepted: every op, both finals, schema edges -------------------------------------------------------
    ("ok_cell_status", '{"action":"call","op":"cell_status","args":{}}', C("cell_status")),
    ("ok_fan_set", OK_CALL, C("fan_set", {"duty": 60})),
    ("ok_fan_0", '{"action":"call","op":"fan_set","args":{"duty":0}}', C("fan_set", {"duty": 0})),
    ("ok_fan_100_schema_max", '{"action":"call","op":"fan_set","args":{"duty":100}}', C("fan_set", {"duty": 100})),
    ("ok_servo_set", '{"action":"call","op":"servo_set","args":{"deg":120}}', C("servo_set", {"deg": 120})),
    ("ok_servo_180", '{"action":"call","op":"servo_set","args":{"deg":180}}', C("servo_set", {"deg": 180})),
    ("ok_halt", '{"action":"call","op":"halt","args":{}}', C("halt")),
    ("ok_lcd_show", '{"action":"call","op":"lcd_show","args":{"line":1,"text":"SELF TEST OK"}}',
     C("lcd_show", {"line": 1, "text": "SELF TEST OK"})),
    ("ok_lcd_16_bytes", '{"action":"call","op":"lcd_show","args":{"line":0,"text":"0123456789ABCDEF"}}',
     C("lcd_show", {"line": 0, "text": "0123456789ABCDEF"})),
    ("ok_lcd_empty", '{"action":"call","op":"lcd_show","args":{"line":0,"text":""}}',
     C("lcd_show", {"line": 0, "text": ""})),
    ("ok_list_objects", '{"action":"call","op":"list_objects","args":{}}', C("list_objects")),
    ("ok_read_object", '{"action":"call","op":"read_object","args":{"name":"maint_notes"}}',
     C("read_object", {"name": "maint_notes"})),
    ("ok_write_object", '{"action":"call","op":"write_object","args":{"name":"scratch_0","text":"fan ok"}}',
     C("write_object", {"name": "scratch_0", "text": "fan ok"})),
    ("ok_write_64_bytes", '{"action":"call","op":"write_object","args":{"name":"scratch_3","text":"' + "x" * 64 + '"}}',
     C("write_object", {"name": "scratch_3", "text": "x" * 64})),
    ("ok_witness", '{"action":"call","op":"witness","args":{}}', C("witness")),
    ("ok_done", '{"action":"done","summary":"Sensor reads 290 mm; fan and gauge responded."}',
     Done("Sensor reads 290 mm; fan and gauge responded.")),
    ("ok_done_200_bytes", '{"action":"done","summary":"' + "a" * 200 + '"}', Done("a" * 200)),
    ("ok_refuse", '{"action":"refuse","reason":"Erasing the audit log is not part of maintenance."}',
     Refuse("Erasing the audit log is not part of maintenance.")),
    ("ok_key_order_free", '{"args":{"duty":60},"op":"fan_set","action":"call"}', C("fan_set", {"duty": 60})),
    ("ok_json_whitespace", ' \n{ "action" : "call" , "op" : "halt" , "args" : { } }\r\n\t', C("halt")),
    ("ok_escaped_quote", '{"action":"done","summary":"said \\"done\\""}', Done('said "done"')),
    ("ok_escape_printable", '{"action":"done","summary":"\\u0041"}', Done("A")),
    ("ok_bytes_input", OK_CALL.encode(), C("fan_set", {"duty": 60})),
    ("ok_out_of_device_limits", '{"action":"call","op":"servo_set","args":{"deg":0}}', C("servo_set", {"deg": 0})),
    # ---- numbers ------------------------------------------------------------------------------------------------
    ("float_60_0", '{"action":"call","op":"fan_set","args":{"duty":60.0}}', "reject:float"),
    ("float_60_5", '{"action":"call","op":"fan_set","args":{"duty":60.5}}', "reject:float"),
    ("exponent", '{"action":"call","op":"fan_set","args":{"duty":6e1}}', "reject:float"),
    ("exponent_upper", '{"action":"call","op":"fan_set","args":{"duty":6E1}}', "reject:float"),
    ("string_number", '{"action":"call","op":"fan_set","args":{"duty":"60"}}', "reject:not_int:args.duty"),
    ("bool_as_int", '{"action":"call","op":"fan_set","args":{"duty":true}}', "reject:not_int:args.duty"),
    ("null_value", '{"action":"call","op":"fan_set","args":{"duty":null}}', "reject:not_int:args.duty"),
    ("nan", '{"action":"call","op":"fan_set","args":{"duty":NaN}}', "reject:nan_or_infinity"),
    ("infinity", '{"action":"call","op":"fan_set","args":{"duty":Infinity}}', "reject:nan_or_infinity"),
    ("neg_infinity", '{"action":"call","op":"fan_set","args":{"duty":-Infinity}}', "reject:nan_or_infinity"),
    ("huge_int", '{"action":"call","op":"fan_set","args":{"duty":' + "9" * 5000 + "}}", "reject:"),
    ("hundred_digits", '{"action":"call","op":"fan_set","args":{"duty":' + "9" * 100 + "}}", "reject:int_too_large"),
    ("seven_digits", '{"action":"call","op":"fan_set","args":{"duty":1000000}}', "reject:int_too_large"),
    ("negative", '{"action":"call","op":"fan_set","args":{"duty":-1}}', "reject:out_of_schema:args.duty"),
    ("negative_zero", '{"action":"call","op":"fan_set","args":{"duty":-0}}', "reject:negative_zero"),
    ("leading_zero", '{"action":"call","op":"fan_set","args":{"duty":060}}', "reject:json_syntax"),
    ("hex_number", '{"action":"call","op":"fan_set","args":{"duty":0x3c}}', "reject:json_syntax"),
    ("plus_sign", '{"action":"call","op":"fan_set","args":{"duty":+60}}', "reject:json_syntax"),
    ("duty_101", '{"action":"call","op":"fan_set","args":{"duty":101}}', "reject:out_of_schema:args.duty"),
    ("deg_181", '{"action":"call","op":"servo_set","args":{"deg":181}}', "reject:out_of_schema:args.deg"),
    ("line_2", '{"action":"call","op":"lcd_show","args":{"line":2,"text":"x"}}', "reject:out_of_schema:args.line"),
    ("array_value", '{"action":"call","op":"fan_set","args":{"duty":[60]}}', "reject:not_int:args.duty"),
    # ---- keys and shape -------------------------------------------------------------------------------------------
    ("duplicate_key_top", '{"action":"call","action":"done","op":"halt","args":{}}', "reject:duplicate_key"),
    ("duplicate_key_args", '{"action":"call","op":"fan_set","args":{"duty":50,"duty":80}}', "reject:duplicate_key"),
    ("extra_key_top", '{"action":"call","op":"halt","args":{},"why":"x"}', "reject:extra_key:why"),
    ("extra_key_args", '{"action":"call","op":"fan_set","args":{"duty":60,"force":1}}', "reject:extra_key:args.force"),
    ("missing_args", '{"action":"call","op":"halt"}', "reject:missing_key:args"),
    ("missing_arg_key", '{"action":"call","op":"lcd_show","args":{"line":0}}', "reject:missing_key:args.text"),
    ("missing_action", '{"op":"halt","args":{}}', "reject:missing_key:action"),
    ("args_on_done", '{"action":"done","summary":"x","args":{}}', "reject:extra_key:args"),
    ("done_missing_summary", '{"action":"done"}', "reject:missing_key:summary"),
    ("refuse_with_summary", '{"action":"refuse","summary":"x"}', "reject:extra_key:summary"),
    ("unknown_action", '{"action":"think","text":"..."}', "reject:bad_action"),
    ("action_case", '{"action":"CALL","op":"halt","args":{}}', "reject:bad_action"),
    ("action_not_string", '{"action":1}', "reject:bad_action"),
    ("unknown_op", '{"action":"call","op":"reboot","args":{}}', "reject:unknown_op"),
    ("op_case", '{"action":"call","op":"Halt","args":{}}', "reject:unknown_op"),
    ("op_not_string", '{"action":"call","op":["halt"],"args":{}}', "reject:unknown_op"),
    ("syscall_name_as_op", '{"action":"call","op":"cell_fan","args":{"duty":60}}', "reject:unknown_op"),
    ("args_not_object", '{"action":"call","op":"halt","args":[]}', "reject:args_not_object"),
    ("args_null", '{"action":"call","op":"halt","args":null}', "reject:args_not_object"),
    ("nested_object_arg", '{"action":"call","op":"fan_set","args":{"duty":{"value":60}}}', "reject:not_int:args.duty"),
    ("nested_text_object", '{"action":"call","op":"write_object","args":{"name":"scratch_0","text":{"a":1}}}',
     "reject:not_string:args.text"),
    ("top_array", '[{"action":"call","op":"halt","args":{}}]', "reject:leading_text"),
    ("top_string", '"halt"', "reject:leading_text"),
    ("two_actions_array", '{"action":"call","op":"halt","args":{}},{"action":"done","summary":"x"}',
     "reject:trailing_text"),
    ("deep_nesting", '{"action":"call","op":"halt","args":' + "[" * 100_000 + "]" * 100_000 + "}", "reject:"),
    ("deep_nesting_in_cap", '{"action":"call","op":"halt","args":' + "[" * 2000 + "]" * 2000 + "}", "reject:"),
    # ---- strings -------------------------------------------------------------------------------------------------
    ("lcd_17_bytes", '{"action":"call","op":"lcd_show","args":{"line":0,"text":"0123456789ABCDEFG"}}',
     "reject:too_long:args.text"),
    ("obj_text_65", '{"action":"call","op":"write_object","args":{"name":"scratch_0","text":"' + "x" * 65 + '"}}',
     "reject:too_long:args.text"),
    ("summary_201", '{"action":"done","summary":"' + "a" * 201 + '"}', "reject:too_long:summary"),
    ("reason_201", '{"action":"refuse","reason":"' + "a" * 201 + '"}', "reject:too_long:reason"),
    ("unknown_object", '{"action":"call","op":"read_object","args":{"name":"passwd"}}', "reject:unknown_object"),
    ("object_case", '{"action":"call","op":"read_object","args":{"name":"Maint_Notes"}}', "reject:unknown_object"),
    ("object_path", '{"action":"call","op":"read_object","args":{"name":"../audit_log"}}', "reject:unknown_object"),
    ("object_trailing_space", '{"action":"call","op":"read_object","args":{"name":"audit_log "}}',
     "reject:unknown_object"),
    ("object_id_as_int", '{"action":"call","op":"read_object","args":{"name":6}}', "reject:not_string:args.name"),
    ("escaped_newline_in_text", '{"action":"call","op":"lcd_show","args":{"line":0,"text":"a\\nb"}}',
     "reject:not_printable:args.text"),
    ("escaped_nul", '{"action":"done","summary":"ok\\u0000"}', "reject:not_printable:summary"),
    ("escaped_rtl_override", '{"action":"done","summary":"ok\\u202e"}', "reject:not_printable:summary"),
    ("escaped_homoglyph", '{"action":"call","op":"read_object","args":{"name":"maint_n\\u043ates"}}',
     "reject:not_printable:args.name"),
    ("escaped_surrogate", '{"action":"done","summary":"\\ud800"}', "reject:not_printable:summary"),
    ("escaped_key_unicode", '{"\\u0430ction":"done","summary":"x"}', "reject:missing_key:action"),
    ("raw_utf8_in_string", '{"action":"done","summary":"café"}', "reject:non_ascii"),
    ("raw_utf8_bytes", '{"action":"done","summary":"café"}'.encode(), "reject:non_ascii"),
    ("raw_tab_in_string", '{"action":"done","summary":"a\tb"}', "reject:json_syntax"),
    ("raw_control_char", '{"action":"done","summary":"a\x07b"}', "reject:control_char"),
    ("zero_width_space", '{"action":"call","op":"halt​","args":{}}', "reject:non_ascii"),
    ("fullwidth_brace", "｛\"action\":\"done\",\"summary\":\"x\"｝", "reject:non_ascii"),
    # ---- framing of the reply -----------------------------------------------------------------------------------
    ("bom_str", "﻿" + OK_CALL, "reject:bom"),
    ("bom_bytes", b"\xef\xbb\xbf" + OK_CALL.encode(), "reject:bom"),
    ("code_fence", "```json\n" + OK_CALL + "\n```", "reject:code_fence"),
    ("code_fence_plain", "```\n" + OK_CALL + "\n```", "reject:code_fence"),
    ("fence_inside_string", '{"action":"done","summary":"```"}', "reject:code_fence"),
    ("leading_text", "Sure! " + OK_CALL, "reject:leading_text"),
    ("trailing_text", OK_CALL + " Let me know.", "reject:trailing_text"),
    ("two_values", OK_CALL + OK_CALL, "reject:trailing_text"),
    ("two_values_newline", OK_CALL + "\n" + '{"action":"done","summary":"x"}', "reject:trailing_text"),
    ("trailing_comma", '{"action":"call","op":"halt","args":{},}', "reject:json_syntax"),
    ("single_quotes", "{'action':'call','op':'halt','args':{}}", "reject:json_syntax"),
    ("unquoted_keys", '{action:"call",op:"halt",args:{}}', "reject:json_syntax"),
    ("js_comment", '{"action":"call","op":"halt","args":{}} // done', "reject:trailing_text"),
    ("block_comment", '{"action":"call",/* x */"op":"halt","args":{}}', "reject:json_syntax"),
    ("truncated", '{"action":"call","op":"fan_set","args":{"duty":60}', "reject:json_syntax"),
    ("empty", "", "reject:empty"),
    ("whitespace_only", " \n ", "reject:empty"),
    ("too_large", '{"action":"done","summary":"' + "a" * 5000 + '"}', "reject:too_large"),
    ("python_literal", "{'action': 'call', 'op': 'halt', 'args': {}, 'x': True}", "reject:json_syntax"),
    ("code_as_text", '{"action":"call","op":"write_object","args":{"name":"scratch_0","text":"__import__(\'os\')"}}',
     C("write_object", {"name": "scratch_0", "text": "__import__('os')"})),
]  # fmt: skip


def outcome(reply) -> str:
    r = validate(reply)
    if isinstance(r, Rejected):
        return "reject:" + r.reason
    return repr(r)


@pytest.mark.parametrize("cid,reply,expected", CASES, ids=[c[0] for c in CASES])
def test_inv_08_parser_cases(cid, reply, expected):
    got = validate(reply)
    if isinstance(expected, str):
        assert isinstance(got, Rejected), f"{cid}: accepted {got!r}"
        assert ("reject:" + got.reason).startswith(expected), f"{cid}: {got.reason}"
    else:
        assert got == expected, cid


def test_case_count_and_unique_ids():
    ids = [c[0] for c in CASES]
    assert len(ids) == len(set(ids))
    assert len(CASES) >= 60
    assert sum(isinstance(c[2], str) for c in CASES) >= 60  # at least 60 rejection cases


def test_never_raises_on_random_input():
    import random

    rng = random.Random(8)
    alphabet = '{}[]":,0123456789-.eE ntrufalsNI\\u"actiopdgsym_' + "\x00\x7fé﻿"
    for _ in range(5000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        r = validate(s)
        assert isinstance(r, Call | Done | Refuse | Rejected)
    for _ in range(500):
        b = rng.randbytes(rng.randint(0, 100))
        assert isinstance(validate(b), Call | Done | Refuse | Rejected)


def render_markdown() -> str:
    rows = ["# T-03a strict JSON cases (INV-08)", "",
            "Generated from `tests/bridge/test_inv_08_parser_cases.py` (`CASES`); each row is one test.", "",
            "| # | id | reply (first 70 chars, repr) | expected | result |", "|---|---|---|---|---|"]  # fmt: skip
    for i, (cid, reply, expected) in enumerate(CASES, 1):
        shown = repr(reply)
        shown = shown if len(shown) <= 70 else shown[:67] + "..."
        shown = shown.replace("|", "\\|")
        exp = expected if isinstance(expected, str) else "accept " + type(expected).__name__
        got = outcome(reply)
        ok = got.startswith(expected) if isinstance(expected, str) else validate(reply) == expected
        rows.append(f"| {i} | `{cid}` | `{shown}` | {exp} | {'PASS' if ok else 'FAIL'}: {got[:60]} |")
    n_rej = sum(isinstance(c[2], str) for c in CASES)
    rows += ["", f"{len(CASES)} cases: {len(CASES) - n_rej} accepted, {n_rej} rejected."]
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    print(render_markdown(), end="")
