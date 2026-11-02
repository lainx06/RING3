"""A1: abi/link_abi.h and bridge/ring3bridge/abi.py agree, and both agree with the kernel's policy tables.

The C side is not regex-evaluated: every ``#define LINK_*`` name is collected from the header, then a probe
program that includes the header prints each value, compiled with the host C compiler. The header also has to
compile freestanding for i686 with -Werror. Kernel sources are only read (compiled into a temp dir).
"""

import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from ring3bridge import abi

REPO = Path(__file__).resolve().parents[2]
HEADER = REPO / "abi" / "link_abi.h"
POLICY_DIR = REPO / "kernel" / "policy"
HOSTCC = shutil.which("cc") or shutil.which("clang")

DEFINE = re.compile(r"^\s*#\s*define\s+(LINK_\w+)\b(?!\()", re.M)


def c_define_names() -> list[str]:
    return DEFINE.findall(HEADER.read_text())


def _run_probe(tmp: Path, source: str, extra: list[str]) -> str:
    c = tmp / "probe.c"
    exe = tmp / "probe"
    c.write_text(source)
    cmd = [HOSTCC, "-std=c11", "-Wall", "-Wextra", "-Werror", *extra, str(c), "-o", str(exe)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def c_values(tmp_path_factory) -> dict[str, int]:
    assert HOSTCC, "no host C compiler"
    names = c_define_names()
    lines = "\n".join(f'    printf("{n} %lld\\n", (long long)({n}));' for n in names)
    src = f'#include <stdio.h>\n#include "link_abi.h"\nint main(void)\n{{\n{lines}\n    return 0;\n}}\n'
    out = _run_probe(tmp_path_factory.mktemp("abi"), src, [f"-I{HEADER.parent}"])
    vals = {}
    for line in out.splitlines():
        k, v = line.split()
        vals[k] = int(v)
    return vals


def test_header_includes_only_stdint():
    includes = re.findall(r"^\s*#\s*include\s+(\S+)", HEADER.read_text(), re.M)
    assert includes == ["<stdint.h>"]


def test_header_compiles_freestanding_i686(tmp_path):
    clang = shutil.which("clang")
    if not clang:
        pytest.skip("clang not installed")
    src = tmp_path / "t.c"
    src.write_text('#include "link_abi.h"\nint link_abi_probe(void) { return LINK_ABI_VERSION; }\n')
    flags = ["--target=i686-unknown-none-elf", "-ffreestanding", "-nostdlib", "-std=c11"]
    flags += ["-Wall", "-Wextra", "-Wpedantic", "-Werror", f"-I{HEADER.parent}"]
    cmd = [clang, *flags, "-c", str(src), "-o", str(tmp_path / "t.o")]
    subprocess.run(cmd, check=True)


def test_every_c_constant_matches_abi_py(c_values):
    py = abi.c_constants()
    assert c_values, "no LINK_* defines parsed"
    missing_in_py = sorted(set(c_values) - set(py))
    missing_in_c = sorted(set(py) - set(c_values))
    differ = {k: (c_values[k], py[k]) for k in c_values.keys() & py.keys() if c_values[k] != py[k]}
    assert not missing_in_py, f"in link_abi.h but not abi.py: {missing_in_py}"
    assert not missing_in_c, f"in abi.py but not link_abi.h: {missing_in_c}"
    assert not differ, f"C vs Python differ: {differ}"


def test_frame_sizes_are_consistent():
    assert abi.LINK_MIN_DECODED == abi.LINK_HDR_LEN + abi.LINK_CRC_LEN
    assert abi.LINK_MAX_BODY == max(hi for _, hi in abi.BODY_BOUNDS.values())
    assert abi.LINK_MAX_DECODED == abi.LINK_MIN_DECODED + abi.LINK_MAX_BODY
    n = abi.LINK_MAX_DECODED
    assert abi.LINK_MAX_ENCODED == n + -(-n // 254)  # canonical COBS worst case
    assert abi.LINK_MAX_WIRE == abi.LINK_MAX_ENCODED + 1
    assert set(abi.BODY_BOUNDS) == abi.K2B_TYPES | abi.B2K_TYPES == set(abi.TYPE_NAMES)
    assert not abi.K2B_TYPES & abi.B2K_TYPES
    # disjoint from the COM3 cell link types (firmware/ring3cell/protocol.h)
    proto = (REPO / "firmware" / "ring3cell" / "protocol.h").read_text()
    cell = {int(v, 16) for v in re.findall(r"#define CELL_T_\w+ (0x[0-9A-Fa-f]+)", proto)}
    assert cell and not cell & set(abi.TYPE_NAMES)


def test_fixed_layouts():
    assert abi.LINK_ACT_OFF_RSV + abi.LINK_ACT_RSV_LEN == abi.LINK_ACTION_LEN == struct.calcsize("<BBhhIB5x")
    assert struct.calcsize("<IIBBBBIiBB2x") == abi.LINK_AUDIT_LEN
    assert struct.calcsize("<BBBxII4i") == abi.LINK_EVENT_LEN
    assert abi.LINK_EV_OFF_A0 + 4 * abi.LINK_EV_NARGS == abi.LINK_EVENT_LEN
    assert struct.calcsize("<IIB3x") == abi.LINK_HEARTBEAT_LEN
    assert struct.calcsize("<IiBB2x") == abi.LINK_OBS_HDR_LEN
    assert abi.LINK_HELLO_OFF_POLICY + abi.LINK_HELLO_POLICY_LEN == abi.LINK_HELLO_LEN
    assert abi.LINK_HELLO_OFF_BUILD + abi.LINK_HELLO_BUILD_LEN == abi.LINK_HELLO_OFF_POLICY


def test_op_table_is_the_complete_allowlist():
    assert list(abi.OPS) == [
        "cell_status", "fan_set", "servo_set", "halt", "lcd_show",
        "list_objects", "read_object", "write_object", "witness",
    ]  # fmt: skip
    assert sorted(abi.OPS.values()) == list(range(abi.LINK_OP_MIN, abi.LINK_OP_MAX + 1))


POLICY_PROBE = r"""
#include <stdio.h>
#include "policy.h"
int main(void)
{
    printf("OBJ_COUNT %d\n", OBJ_COUNT);
    for (unsigned i = 0; i < OBJ_COUNT; i++) printf("OBJ %u %s\n", i, policy_object(i)->name);
    printf("OBJ_CELL_SPEC %d\nOBJ_MAINT_NOTES %d\nOBJ_SCRATCH_0 %d\nOBJ_SCRATCH_1 %d\n", OBJ_CELL_SPEC,
           OBJ_MAINT_NOTES, OBJ_SCRATCH_0, OBJ_SCRATCH_1);
    printf("OBJ_SCRATCH_2 %d\nOBJ_SCRATCH_3 %d\n", OBJ_SCRATCH_2, OBJ_SCRATCH_3);
    printf("OBJ_AUDIT_LOG %d\n", OBJ_AUDIT_LOG);
    for (unsigned i = 0; i < DEC_COUNT; i++) printf("DEC %u %s\n", i, policy_code_name((uint8_t)i));
    for (unsigned i = 0; i < R_COUNT; i++) printf("R %u %s\n", i, policy_reason_name((uint8_t)i));
    for (unsigned i = 0; i < SYS_COUNT; i++) printf("SYS %u %s\n", i, policy_syscall_name(i));
    for (unsigned i = 0; i < EV_COUNT; i++) printf("EV %u %s\n", i, policy_event_name((uint8_t)i));
    printf("EV_SYSCALL %d\n", EV_SYSCALL);
    printf("EPERM %d\nENOENT %d\nEACCES %d\nEFAULT %d\nEINVAL %d\n", EPERM, ENOENT, EACCES, EFAULT, EINVAL);
    printf("ENOSYS %d\nETIMEDOUT %d\nECANCELED %d\nESAFETY %d\n", ENOSYS, ETIMEDOUT, ECANCELED, ESAFETY);
    printf("LCD_TEXT_MAX %u\nOBJ_DATA_MAX %u\n", LCD_TEXT_MAX, OBJ_DATA_MAX);
    return 0;
}
"""


@pytest.fixture(scope="module")
def policy_out(tmp_path_factory) -> list[str]:
    tmp = tmp_path_factory.mktemp("policy")
    c = tmp / "probe.c"
    exe = tmp / "probe"
    c.write_text(POLICY_PROBE)
    flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", f"-I{POLICY_DIR}"]
    flags.append('-DPOLICY_VARIANT_HEADER="variant_default.h"')
    cmd = [HOSTCC, *flags, str(c), str(POLICY_DIR / "policy.c"), "-o", str(exe)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.splitlines()


def _table(lines: list[str], tag: str) -> tuple[str, ...]:
    rows = [line.split() for line in lines if line.startswith(tag + " ")]
    assert [int(r[1]) for r in rows] == list(range(len(rows)))
    return tuple(r[2] for r in rows)


def _scalars(lines: list[str]) -> dict[str, int]:
    out = {}
    for line in lines:
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = int(parts[1])
    return out


def test_object_table_matches_kernel(policy_out):
    """D-52: object IDs read from the kernel's table (policy.h enum + policy.c names, used by objstore.c)."""
    sc = _scalars(policy_out)
    assert abi.OBJECT_NAMES == _table(policy_out, "OBJ")
    assert abi.LINK_OBJ_COUNT == sc["OBJ_COUNT"] == len(abi.OBJECT_NAMES)
    for name in abi.OBJECT_NAMES:
        assert getattr(abi, "LINK_OBJ_" + name.upper()) == sc["OBJ_" + name.upper()] == abi.OBJECT_IDS[name]
    objstore = (REPO / "kernel" / "core" / "objstore.c").read_text()
    assert "policy_object(id)" in objstore  # objstore lists names from the same policy table


def test_policy_name_tables_match_kernel(policy_out):
    assert abi.POLICY_CODE_NAMES == _table(policy_out, "DEC")
    assert abi.POLICY_REASON_NAMES == _table(policy_out, "R")
    assert abi.POLICY_SYSCALL_NAMES == _table(policy_out, "SYS")
    assert abi.POLICY_EVENT_NAMES == _table(policy_out, "EV")
    sc = _scalars(policy_out)
    assert abi.EV_SYSCALL == sc["EV_SYSCALL"]
    for k, v in abi.ERRNO.items():
        assert sc[k] == v, k
    assert abi.LINK_LCD_TEXT_MAX == sc["LCD_TEXT_MAX"]
    assert abi.LINK_OBJ_TEXT_MAX <= sc["OBJ_DATA_MAX"]


def test_action_roundtrip_and_zero_rules():
    a = abi.Action(op=abi.LINK_OP_WRITE_OBJECT, p1=abi.LINK_OBJ_SCRATCH_1, nonce=0xDEADBEEF, text_id=7)
    b = abi.encode_action(a)
    assert len(b) == 16
    assert b == bytes([8, 0, 3, 0, 0, 0, 0xEF, 0xBE, 0xAD, 0xDE, 7, 0, 0, 0, 0, 0])
    assert abi.decode_body(abi.LINK_T_ACTION, b) == a
    for off in [abi.LINK_ACT_OFF_FLAGS, abi.LINK_ACT_OFF_P2, abi.LINK_ACT_OFF_P2 + 1, *range(11, 16)]:
        bad = bytearray(b)
        bad[off] = 1
        assert isinstance(abi.decode_body(abi.LINK_T_ACTION, bytes(bad)), abi.BodyError), off


def test_action_field_rules():
    ok = [
        abi.Action(op=abi.LINK_OP_CELL_STATUS),
        abi.Action(op=abi.LINK_OP_FAN_SET, p1=100),
        abi.Action(op=abi.LINK_OP_SERVO_SET, p1=180),
        abi.Action(op=abi.LINK_OP_LCD_SHOW, p1=1, text_id=255),
        abi.Action(op=abi.LINK_OP_READ_OBJECT, p1=6),
        abi.Action(op=abi.LINK_OP_WITNESS),
    ]
    bad = [
        abi.Action(op=0),
        abi.Action(op=10),
        abi.Action(op=abi.LINK_OP_HALT, p1=1),
        abi.Action(op=abi.LINK_OP_HALT, text_id=1),
        abi.Action(op=abi.LINK_OP_FAN_SET, p1=101),
        abi.Action(op=abi.LINK_OP_FAN_SET, p1=-1),
        abi.Action(op=abi.LINK_OP_SERVO_SET, p1=181),
        abi.Action(op=abi.LINK_OP_LCD_SHOW, p1=2, text_id=1),
        abi.Action(op=abi.LINK_OP_LCD_SHOW, p1=0),
        abi.Action(op=abi.LINK_OP_READ_OBJECT, p1=7),
        abi.Action(op=abi.LINK_OP_WRITE_OBJECT, p1=1),
        abi.Action(op=abi.LINK_OP_CELL_STATUS, flags=1),
        abi.Action(op=abi.LINK_OP_CELL_STATUS, p2=1),
    ]
    for a in ok:
        assert abi.check_action(a) is None, a
    for a in bad:
        assert abi.check_action(a) is not None, a
        with pytest.raises(ValueError):
            abi.encode_action(a)


def test_k2b_bodies_roundtrip_and_reserved_bytes():
    cases = [
        (abi.LINK_T_HELLO, abi.encode_hello(abi.Hello(1, 0, b"b" * 12, b"\x01" * 16)), [3]),
        (
            abi.LINK_T_OBSERVATION,
            abi.encode_observation(abi.Observation(9, -13, 7, True, b"x\n\x00")),
            [10, 11],
        ),
        (abi.LINK_T_AUDIT, abi.encode_audit(abi.Audit(1, 2, 3, 6, 4, 10, 40, -22, 1, False)), [22, 23]),
        (abi.LINK_T_EVENT, abi.encode_event(abi.Event(3, 0, 1, 500, 6, (60, 0, 0, -1))), [3]),
        (abi.LINK_T_HEARTBEAT, abi.encode_heartbeat(abi.Heartbeat(100, 0, 1)), [9, 10, 11]),
    ]
    for t, body, rsv in cases:
        d = abi.decode_body(t, body)
        assert not isinstance(d, abi.BodyError), (t, d)
        for off in rsv:
            bad = bytearray(body)
            bad[off] = 0x80
            assert isinstance(abi.decode_body(t, bytes(bad)), abi.BodyError), (t, off)


def test_b2k_text_final_stop_rules():
    assert abi.decode_body(abi.LINK_T_TEXT, b"\x05hi") == abi.Text(5, "hi")
    assert isinstance(abi.decode_body(abi.LINK_T_TEXT, b"\x00hi"), abi.BodyError)
    assert isinstance(abi.decode_body(abi.LINK_T_TEXT, b"\x05h\x07"), abi.BodyError)
    assert abi.decode_body(abi.LINK_T_FINAL, b"\x01ok") == abi.Final(1, "ok")
    assert isinstance(abi.decode_body(abi.LINK_T_FINAL, b"\x03ok"), abi.BodyError)
    assert abi.decode_body(abi.LINK_T_STOP, b"\x02") == abi.Stop(2)
    assert isinstance(abi.decode_body(abi.LINK_T_STOP, b"\x00"), abi.BodyError)
    assert isinstance(abi.decode_body(abi.LINK_T_STOP, b"\x07"), abi.BodyError)
    assert isinstance(abi.decode_body(0x42, b""), abi.BodyError)
    for t, (lo, hi) in abi.BODY_BOUNDS.items():
        assert isinstance(abi.decode_body(t, b"\x01" * (hi + 1)), abi.BodyError)
        if lo:
            assert isinstance(abi.decode_body(t, b"\x01" * (lo - 1)), abi.BodyError)


def test_cell_status_record_decodes_for_the_model():
    """T-04: the 16-byte cell_status output (LINK_CELLST_*) becomes key=value text; no banned words."""
    rec = bytes(
        [0x38, 0x01, 60, 120, 0, 0x22, 1, 0]  # 312 mm, fan 60, servo 120, released, CLAMP|LINK_LOST, fresh
        + list((40).to_bytes(4, "little"))
        + list((1234).to_bytes(2, "little"))
        + [60, 120]
    )
    d = abi.decode_cell_status(rec)
    assert d == {
        "distance_mm": 312, "fan": 60, "servo": 120, "estop": 0, "flags": 0x22, "fresh": 1,
        "estop_latched": 0, "age_ms": 40, "seq": 1234, "cmd_fan": 60, "cmd_servo": 120,
    }  # fmt: skip
    text = abi.cell_status_text(rec)
    assert text == (
        "distance_mm=312 fan_pct=60 servo_deg=120 estop_pressed=0 device_flags=CLAMP,LINK_LOST "
        "telemetry_fresh=1 estop_latched=0 age_ms=40 seq=1234 commanded_fan_pct=60 commanded_servo_deg=120"
    )
    none = bytes([0xFF, 0xFF, 0, 0, 1, 0x08, 0, 1]) + b"\xff\xff\xff\xff" + bytes(4)
    assert "distance_mm=no_echo" in abi.cell_status_text(none) and "age_ms=none" in abi.cell_status_text(none)
    assert "ESTOP_LATCHED" in abi.cell_status_text(none)
    with pytest.raises(ValueError):
        abi.decode_cell_status(rec[:-1])
    # the run loop renders it only for a cell_status observation of exactly 16 bytes
    from ring3bridge.run import observation_text

    assert "fan_pct=60" in observation_text("cell_status", 16, rec, False)
    assert "fan_pct" not in observation_text("read_object", 16, rec, False)
    assert "fan_pct" not in observation_text("cell_status", -38, b"", False)
