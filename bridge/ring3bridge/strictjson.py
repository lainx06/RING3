"""Exact validation of model replies (INV-08, ARCHITECTURE §6). No repair, no lenient parsing.

A reply is exactly one JSON object, one of:
  {"action":"call","op":<op>,"args":{...}}   exact arg keys per op (below)
  {"action":"done","summary":<str>}           {"action":"refuse","reason":<str>}

Integers only (no bools, floats, exponents, NaN/Infinity, -0); strings printable ASCII after unescaping,
with byte caps; ``name`` from the object table. Duplicate keys, extra or missing keys, a BOM, code fences,
leading or trailing text and more than one value are rejected. Ranges here are the schema's (duty 0-100,
deg 0-180, line 0-1); the kernel applies the device limits.

``validate()`` never raises and never evaluates anything: the result is a ``Call``/``Done``/``Refuse`` or a
``Rejected`` with a reason code (logged in ``reply_rejected``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ring3bridge import abi

MAX_REPLY_BYTES = 4096  # a valid reply is far smaller; anything larger is rejected before parsing
MAX_INT_DIGITS = 6  # the largest valid value is 180; bounded before int() sees the text
JSON_WS = " \t\n\r"

#: op -> {arg key: kind}; kind is "duty", "deg", "line", "lcd_text", "obj_text", "name"
ARGS: dict[str, dict[str, str]] = {
    "cell_status": {},
    "fan_set": {"duty": "duty"},
    "servo_set": {"deg": "deg"},
    "halt": {},
    "lcd_show": {"line": "line", "text": "lcd_text"},
    "list_objects": {},
    "read_object": {"name": "name"},
    "write_object": {"name": "name", "text": "obj_text"},
    "witness": {},
}
assert set(ARGS) == set(abi.OPS)

INT_RANGE = {"duty": (0, abi.LINK_DUTY_MAX), "deg": (0, abi.LINK_DEG_MAX), "line": (0, abi.LINK_LCD_LINE_MAX)}
STR_MAX = {"lcd_text": abi.LINK_LCD_TEXT_MAX, "obj_text": abi.LINK_OBJ_TEXT_MAX}
SUMMARY_MAX = abi.LINK_FINAL_TEXT_MAX


@dataclass(frozen=True)
class Call:
    op: str
    args: dict[str, int | str] = field(default_factory=dict)


@dataclass(frozen=True)
class Done:
    summary: str


@dataclass(frozen=True)
class Refuse:
    reason: str


@dataclass(frozen=True)
class Rejected:
    reason: str  # stable code, e.g. "duplicate_key", "float", "extra_key:args.x"


Reply = Call | Done | Refuse


class _Reject(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _no_dupes(pairs: list[tuple[str, object]]) -> dict[str, object]:
    d: dict[str, object] = {}
    for k, v in pairs:
        if k in d:
            raise _Reject("duplicate_key")
        d[k] = v
    return d


def _no_float(_s: str) -> None:
    raise _Reject("float")


def _no_constant(_s: str) -> None:
    raise _Reject("nan_or_infinity")


def _int(s: str) -> int:
    digits = s.lstrip("-")
    if len(digits) > MAX_INT_DIGITS:
        raise _Reject("int_too_large")
    if s == "-0":
        raise _Reject("negative_zero")
    return int(s)


def _printable(s: str) -> bool:
    return all(0x20 <= ord(c) <= 0x7E for c in s)


def _exact_keys(obj: dict, want: set[str], where: str) -> None:
    extra = sorted(set(obj) - want)
    if extra:
        raise _Reject(f"extra_key:{where}{extra[0]}")
    missing = sorted(want - set(obj))
    if missing:
        raise _Reject(f"missing_key:{where}{missing[0]}")


def _string(v: object, cap: int, where: str) -> str:
    if type(v) is not str:
        raise _Reject(f"not_string:{where}")
    if not _printable(v):
        raise _Reject(f"not_printable:{where}")
    if len(v.encode("ascii")) > cap:
        raise _Reject(f"too_long:{where}")
    return v


def _check(obj: object) -> Reply:
    if type(obj) is not dict:
        raise _Reject("not_object")
    action = obj.get("action")
    if type(action) is not str:
        raise _Reject("bad_action" if "action" in obj else "missing_key:action")
    if action == "done":
        _exact_keys(obj, {"action", "summary"}, "")
        return Done(_string(obj["summary"], SUMMARY_MAX, "summary"))
    if action == "refuse":
        _exact_keys(obj, {"action", "reason"}, "")
        return Refuse(_string(obj["reason"], SUMMARY_MAX, "reason"))
    if action != "call":
        raise _Reject("bad_action")
    _exact_keys(obj, {"action", "op", "args"}, "")
    op = obj["op"]
    if type(op) is not str or op not in ARGS:
        raise _Reject("unknown_op")
    args = obj["args"]
    if type(args) is not dict:
        raise _Reject("args_not_object")
    spec = ARGS[op]
    _exact_keys(args, set(spec), "args.")
    out: dict[str, int | str] = {}
    for key, kind in spec.items():
        v = args[key]
        if kind in INT_RANGE:
            if type(v) is not int:  # bool is a subclass of int: excluded by the exact type check
                raise _Reject(f"not_int:args.{key}")
            lo, hi = INT_RANGE[kind]
            if not lo <= v <= hi:
                raise _Reject(f"out_of_schema:args.{key}")
            out[key] = v
        elif kind == "name":
            name = _string(v, abi.LINK_OBJ_TEXT_MAX, f"args.{key}")
            if name not in abi.OBJECT_IDS:
                raise _Reject("unknown_object")
            out[key] = name
        else:
            out[key] = _string(v, STR_MAX[kind], f"args.{key}")
    return Call(op, out)


def validate(text: str | bytes) -> Reply | Rejected:
    """Validates one model reply. Never raises."""
    try:
        if isinstance(text, bytes):
            if len(text) > MAX_REPLY_BYTES:
                return Rejected("too_large")
            if text.startswith(b"\xef\xbb\xbf"):
                return Rejected("bom")
            try:
                text = text.decode("ascii")
            except UnicodeDecodeError:
                return Rejected("non_ascii")
        if not isinstance(text, str):
            return Rejected("not_text")
        if len(text) > MAX_REPLY_BYTES:
            return Rejected("too_large")
        if text.startswith("﻿"):
            return Rejected("bom")
        if "```" in text:
            return Rejected("code_fence")
        for c in text:
            if not (0x20 <= ord(c) <= 0x7E or c in JSON_WS):
                return Rejected("non_ascii" if ord(c) > 0x7E else "control_char")
        stripped = text.strip(JSON_WS)
        if not stripped.startswith("{"):
            return Rejected("leading_text" if stripped else "empty")
        obj = json.loads(
            stripped,
            object_pairs_hook=_no_dupes,
            parse_float=_no_float,
            parse_int=_int,
            parse_constant=_no_constant,
        )
        return _check(obj)
    except _Reject as r:
        return Rejected(r.reason)
    except json.JSONDecodeError as e:
        return Rejected("trailing_text" if e.msg == "Extra data" else "json_syntax")
    except (RecursionError, ValueError, TypeError, MemoryError):
        return Rejected("json_syntax")
