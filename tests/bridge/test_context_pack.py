"""Step 8: context pack v1 states the real allowlist, shapes, limits and object table, and nothing else."""

import re
from pathlib import Path

from ring3bridge import abi, context_pack
from ring3bridge.strictjson import Call, validate

REPO = Path(__file__).resolve().parents[2]
POLICY_H = (REPO / "kernel" / "policy" / "policy.h").read_text()


def policy_define(name: str) -> int:
    m = re.search(rf"#define {name}\s+(\d+)", POLICY_H)
    assert m, name
    return int(m.group(1))


def test_pack_loads_ascii_with_hash():
    p = context_pack.load()
    assert p.name == "agent-v1" and len(p.sha256) == 64
    assert p.sha256 == context_pack.load("agent-v1").sha256


def test_every_example_reply_is_valid_and_every_op_is_shown():
    text = context_pack.load().text
    examples = re.findall(r"`(\{\"action\":\"call\".*?\})`", text)
    ops = set()
    for ex in examples:
        r = validate(ex)
        assert isinstance(r, Call), (ex, r)
        ops.add(r.op)
    assert ops == set(abi.OPS)
    table_ops = set(re.findall(r"^\| `(\w+)` \| `\{", text, re.M))
    assert table_ops == set(abi.OPS)


def test_objects_and_limits_match_the_kernel():
    text = " ".join(context_pack.load().text.split())
    for name in ("cell_spec", "maint_notes", "audit_log", "scratch_0", "scratch_3"):
        assert f"`{name}`" in text
    fan_min, fan_max = policy_define("FAN_MIN_SPIN"), policy_define("FAN_MAX_DUTY")
    assert f"0 or {fan_min}-{fan_max} %" in text
    assert f"first step may go to {fan_min}-{policy_define('FAN_FIRST_STEP_MAX')} %" in text
    assert f"at most {policy_define('FAN_STEP_MAX')} points" in text
    assert f"{policy_define('SERVO_MIN_DEG')}-{policy_define('SERVO_MAX_DEG')} degrees" in text
    assert f"at most {policy_define('SERVO_STEP_MAX_DEG')} degrees per command" in text
    assert f"closer than {policy_define('PRESENCE_MM')} mm" in text
    assert f"at most {policy_define('LCD_TEXT_MAX')} characters" in text


def test_no_banned_words():
    text = context_pack.load().text.lower()
    for w in ("success", "secure", "safe", "real-time", "realtime"):
        assert not re.search(rf"\b{w}", text), w
