"""Link ABI, bridge side: a mirror of ``abi/link_abi.h`` (COM2, kernel <-> bridge).

Every ``LINK_*`` constant here has the same name and value as the ``#define`` in the C header;
``tests/bridge/test_abi.py`` compiles the header and fails on any difference in either direction. The policy
name tables and object table mirror ``kernel/policy/policy.h`` / ``policy.c`` (same test).
Prose: ``docs/LINK_ABI.md``.

Body codecs live here too. ``decode_body`` never raises on bad input: it returns a ``BodyError``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ---- frame layout (decoded payload) ------------------------------------------------------------------------
LINK_ABI_VERSION = 1
LINK_OFF_TYPE = 0
LINK_OFF_SEQ = 1
LINK_OFF_LEN = 3
LINK_OFF_BODY = 5
LINK_HDR_LEN = 5
LINK_CRC_LEN = 2
LINK_MIN_DECODED = 7
LINK_CRC_POLY = 0x1021
LINK_CRC_INIT = 0xFFFF
LINK_CRC_CHECK = 0x29B1
LINK_DELIM = 0x00

# ---- frame types -------------------------------------------------------------------------------------------
LINK_T_HELLO = 0x10
LINK_T_REQUEST = 0x11
LINK_T_OBSERVATION = 0x12
LINK_T_AUDIT = 0x13
LINK_T_EVENT = 0x14
LINK_T_HEARTBEAT = 0x15
LINK_T_ACTION = 0x90
LINK_T_TEXT = 0x91
LINK_T_FINAL = 0x92
LINK_T_STOP = 0x93

# ---- body-length bounds ------------------------------------------------------------------------------------
LINK_HELLO_LEN = 32
LINK_REQUEST_MIN = 1
LINK_REQUEST_MAX = 512
LINK_OBS_HDR_LEN = 12
LINK_OBS_OUT_MAX = 512
LINK_OBS_MIN = LINK_OBS_HDR_LEN
LINK_OBS_MAX = LINK_OBS_HDR_LEN + LINK_OBS_OUT_MAX
LINK_AUDIT_LEN = 24
LINK_EVENT_LEN = 28
LINK_HEARTBEAT_LEN = 12
LINK_ACTION_LEN = 16
LINK_TEXT_DATA_MAX = 64
LINK_TEXT_MIN = 1
LINK_TEXT_MAX = 1 + LINK_TEXT_DATA_MAX
LINK_FINAL_TEXT_MAX = 200
LINK_FINAL_MIN = 1
LINK_FINAL_MAX = 1 + LINK_FINAL_TEXT_MAX
LINK_STOP_LEN = 1

LINK_MAX_BODY = LINK_OBS_MAX
LINK_MAX_DECODED = LINK_MIN_DECODED + LINK_MAX_BODY
LINK_MAX_ENCODED = 534
LINK_MAX_WIRE = LINK_MAX_ENCODED + 1

# ---- HELLO -------------------------------------------------------------------------------------------------
LINK_HELLO_OFF_ABI = 0
LINK_HELLO_OFF_VARIANT = 2
LINK_HELLO_OFF_RSV = 3
LINK_HELLO_OFF_BUILD = 4
LINK_HELLO_BUILD_LEN = 12
LINK_HELLO_OFF_POLICY = 16
LINK_HELLO_POLICY_LEN = 16
LINK_VARIANT_DEFAULT = 0
LINK_VARIANT_PERMISSIVE = 1

# ---- OBSERVATION -------------------------------------------------------------------------------------------
LINK_OBS_OFF_NONCE = 0
LINK_OBS_OFF_RESULT = 4
LINK_OBS_OFF_OP = 8
LINK_OBS_OFF_FLAGS = 9
LINK_OBS_OFF_RSV = 10
LINK_OBS_OFF_OUT = 12
LINK_OBSF_TRUNCATED = 0x01
LINK_OBSF_MASK = 0x01

# ---- AUDIT -------------------------------------------------------------------------------------------------
LINK_AUD_OFF_SEQ = 0
LINK_AUD_OFF_TICK = 4
LINK_AUD_OFF_KIND = 8
LINK_AUD_OFF_NR = 9
LINK_AUD_OFF_CODE = 10
LINK_AUD_OFF_REASON = 11
LINK_AUD_OFF_TARGET = 12
LINK_AUD_OFF_RESULT = 16
LINK_AUD_OFF_TAINTED = 20
LINK_AUD_OFF_FLAGS = 21
LINK_AUD_OFF_RSV = 22
LINK_AUDF_PERMISSIVE = 0x01
LINK_AUDF_MASK = 0x01

# ---- EVENT -------------------------------------------------------------------------------------------------
LINK_EV_OFF_KIND = 0
LINK_EV_OFF_SUB = 1
LINK_EV_OFF_FLAGS = 2
LINK_EV_OFF_RSV = 3
LINK_EV_OFF_TICK = 4
LINK_EV_OFF_NR = 8
LINK_EV_OFF_A0 = 12
LINK_EV_NARGS = 4

# ---- HEARTBEAT ---------------------------------------------------------------------------------------------
LINK_HB_OFF_TICK = 0
LINK_HB_OFF_DROPPED = 4
LINK_HB_OFF_PROCS = 8
LINK_HB_OFF_RSV = 9

# ---- ACTION ------------------------------------------------------------------------------------------------
LINK_ACT_OFF_OP = 0
LINK_ACT_OFF_FLAGS = 1
LINK_ACT_OFF_P1 = 2
LINK_ACT_OFF_P2 = 4
LINK_ACT_OFF_NONCE = 6
LINK_ACT_OFF_TEXT_ID = 10
LINK_ACT_OFF_RSV = 11
LINK_ACT_RSV_LEN = 5

LINK_OP_CELL_STATUS = 1
LINK_OP_FAN_SET = 2
LINK_OP_SERVO_SET = 3
LINK_OP_HALT = 4
LINK_OP_LCD_SHOW = 5
LINK_OP_LIST_OBJECTS = 6
LINK_OP_READ_OBJECT = 7
LINK_OP_WRITE_OBJECT = 8
LINK_OP_WITNESS = 9
LINK_OP_MIN = 1
LINK_OP_MAX = 9

LINK_DUTY_MAX = 100
LINK_DEG_MAX = 180
LINK_LCD_LINE_MAX = 1
LINK_LCD_TEXT_MAX = 16
LINK_OBJ_TEXT_MAX = 64
LINK_TEXT_CHAR_MIN = 0x20
LINK_TEXT_CHAR_MAX = 0x7E

# ---- object table ------------------------------------------------------------------------------------------
LINK_OBJ_CELL_SPEC = 0
LINK_OBJ_MAINT_NOTES = 1
LINK_OBJ_SCRATCH_0 = 2
LINK_OBJ_SCRATCH_1 = 3
LINK_OBJ_SCRATCH_2 = 4
LINK_OBJ_SCRATCH_3 = 5
LINK_OBJ_AUDIT_LOG = 6
LINK_OBJ_COUNT = 7

# ---- TEXT / FINAL / STOP -----------------------------------------------------------------------------------
LINK_TEXT_OFF_ID = 0
LINK_TEXT_OFF_DATA = 1
LINK_FINAL_OFF_KIND = 0
LINK_FINAL_OFF_TEXT = 1
LINK_FINAL_DONE = 1
LINK_FINAL_REFUSE = 2
LINK_STOP_OFF_REASON = 0
LINK_STOP_INVALID_REPLIES = 1
LINK_STOP_BUDGET = 2
LINK_STOP_TIMEOUT = 3
LINK_STOP_MODEL_AUTH = 4
LINK_STOP_TRANSPORT = 5
LINK_STOP_OPERATOR = 6
LINK_STOP_MIN = 1
LINK_STOP_MAX = 6

# ---- derived tables (not C constants) ----------------------------------------------------------------------
K2B_TYPES = frozenset(
    {LINK_T_HELLO, LINK_T_REQUEST, LINK_T_OBSERVATION, LINK_T_AUDIT, LINK_T_EVENT, LINK_T_HEARTBEAT}
)
B2K_TYPES = frozenset({LINK_T_ACTION, LINK_T_TEXT, LINK_T_FINAL, LINK_T_STOP})

TYPE_NAMES: dict[int, str] = {
    LINK_T_HELLO: "HELLO",
    LINK_T_REQUEST: "REQUEST",
    LINK_T_OBSERVATION: "OBSERVATION",
    LINK_T_AUDIT: "AUDIT",
    LINK_T_EVENT: "EVENT",
    LINK_T_HEARTBEAT: "HEARTBEAT",
    LINK_T_ACTION: "ACTION",
    LINK_T_TEXT: "TEXT",
    LINK_T_FINAL: "FINAL",
    LINK_T_STOP: "STOP",
}

#: type -> (min body length, max body length), inclusive.
BODY_BOUNDS: dict[int, tuple[int, int]] = {
    LINK_T_HELLO: (LINK_HELLO_LEN, LINK_HELLO_LEN),
    LINK_T_REQUEST: (LINK_REQUEST_MIN, LINK_REQUEST_MAX),
    LINK_T_OBSERVATION: (LINK_OBS_MIN, LINK_OBS_MAX),
    LINK_T_AUDIT: (LINK_AUDIT_LEN, LINK_AUDIT_LEN),
    LINK_T_EVENT: (LINK_EVENT_LEN, LINK_EVENT_LEN),
    LINK_T_HEARTBEAT: (LINK_HEARTBEAT_LEN, LINK_HEARTBEAT_LEN),
    LINK_T_ACTION: (LINK_ACTION_LEN, LINK_ACTION_LEN),
    LINK_T_TEXT: (LINK_TEXT_MIN, LINK_TEXT_MAX),
    LINK_T_FINAL: (LINK_FINAL_MIN, LINK_FINAL_MAX),
    LINK_T_STOP: (LINK_STOP_LEN, LINK_STOP_LEN),
}

#: op name (the JSON ``op`` value) -> op number. The complete allowlist (ARCHITECTURE §3a, INV-01).
OPS: dict[str, int] = {
    "cell_status": LINK_OP_CELL_STATUS,
    "fan_set": LINK_OP_FAN_SET,
    "servo_set": LINK_OP_SERVO_SET,
    "halt": LINK_OP_HALT,
    "lcd_show": LINK_OP_LCD_SHOW,
    "list_objects": LINK_OP_LIST_OBJECTS,
    "read_object": LINK_OP_READ_OBJECT,
    "write_object": LINK_OP_WRITE_OBJECT,
    "witness": LINK_OP_WITNESS,
}
OP_NAMES: dict[int, str] = {v: k for k, v in OPS.items()}
#: ops whose ACTION references a TEXT frame
TEXT_OPS = frozenset({LINK_OP_LCD_SHOW, LINK_OP_WRITE_OBJECT})

#: object ID -> name; mirrors ``enum pol_object`` and ``objects[]`` in kernel/policy/policy.c.
OBJECT_NAMES: tuple[str, ...] = (
    "cell_spec",
    "maint_notes",
    "scratch_0",
    "scratch_1",
    "scratch_2",
    "scratch_3",
    "audit_log",
)
OBJECT_IDS: dict[str, int] = {n: i for i, n in enumerate(OBJECT_NAMES)}

FINAL_KINDS: dict[str, int] = {"done": LINK_FINAL_DONE, "refuse": LINK_FINAL_REFUSE}

STOP_NAMES: dict[int, str] = {
    LINK_STOP_INVALID_REPLIES: "invalid_replies",
    LINK_STOP_BUDGET: "budget",
    LINK_STOP_TIMEOUT: "timeout",
    LINK_STOP_MODEL_AUTH: "model_auth",
    LINK_STOP_TRANSPORT: "transport",
    LINK_STOP_OPERATOR: "operator",
}

# ---- policy mirrors (kernel/policy/policy.h, policy.c): names for decoded audit/event records --------------
POLICY_CODE_NAMES: tuple[str, ...] = (
    "NONE", "ALLOW", "DENY_CAP", "DENY_RULE", "DENY_ARG", "DENY_FAULT", "DENY_LIMIT", "DENY_INTERLOCK",
    "DENY_STALE", "DENY_ESTOP", "CONFIRM_PENDING", "CONFIRM_YES", "CONFIRM_NO", "FRAME_DROP", "PROC_FAULT",
    "LIMIT", "AUTO_HALT",
)  # fmt: skip
POLICY_REASON_NAMES: tuple[str, ...] = (
    "none", "exec_start", "exec_end", "arg_range", "uaccess", "no_cap", "obj_rule", "obj_confirm", "taint",
    "first_fan", "fan_range", "fan_step", "fan_rate", "servo_range", "servo_step", "servo_rate",
    "no_telemetry", "stale", "presence", "no_echo", "estop", "estop_button", "estop_reset", "not_latched",
    "answer", "timeout", "no_pending", "pending", "action_quota", "request_quota", "nosys", "no_request",
    "halt", "bad_frame", "permissive",
)  # fmt: skip
POLICY_SYSCALL_NAMES: tuple[str, ...] = (
    "exit", "cons_write", "obj_list", "obj_read", "obj_write", "cell_status", "cell_fan", "cell_servo",
    "cell_halt", "cell_lcd", "uptime", "agent_send", "agent_recv",
)  # fmt: skip
POLICY_EVENT_NAMES: tuple[str, ...] = (
    "none", "exec_start", "exec_end", "syscall", "console", "confirm", "tick", "telemetry", "action_boundary",
)  # fmt: skip
EV_SYSCALL = 3
#: errno values the kernel returns (negated) in OBSERVATION.result; mirrors policy.h.
ERRNO: dict[str, int] = {
    "EPERM": 1,
    "ENOENT": 2,
    "EACCES": 13,
    "EFAULT": 14,
    "EINVAL": 22,
    "ENOSYS": 38,
    "ETIMEDOUT": 110,
    "ECANCELED": 125,
    "ESAFETY": 200,
}


def c_constants() -> dict[str, int]:
    """Every ``LINK_*`` constant of this module (compared with the C header by the ABI test)."""
    return {k: v for k, v in globals().items() if k.startswith("LINK_") and isinstance(v, int)}


# ---- body codecs -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class BodyError:
    """A body that has the right length for its type but violates a field rule."""

    type: int
    reason: str


@dataclass(frozen=True)
class Hello:
    abi_version: int
    variant: int
    build_id: bytes
    policy_prefix: bytes


@dataclass(frozen=True)
class Request:
    text: str


@dataclass(frozen=True)
class Observation:
    nonce: int
    result: int
    op: int
    truncated: bool
    output: bytes


@dataclass(frozen=True)
class Audit:
    seq: int
    tick: int
    kind: int
    nr: int
    code: int
    reason: int
    target: int
    result: int
    tainted: int
    permissive: bool


@dataclass(frozen=True)
class Event:
    kind: int
    sub: int
    flags: int
    tick: int
    nr: int
    args: tuple[int, int, int, int]


@dataclass(frozen=True)
class Heartbeat:
    tick: int
    dropped: int
    procs: int


@dataclass(frozen=True)
class Action:
    op: int
    p1: int = 0
    p2: int = 0
    nonce: int = 0
    text_id: int = 0
    flags: int = 0


@dataclass(frozen=True)
class Text:
    text_id: int
    text: str


@dataclass(frozen=True)
class Final:
    kind: int
    text: str


@dataclass(frozen=True)
class Stop:
    reason: int


Body = Hello | Request | Observation | Audit | Event | Heartbeat | Action | Text | Final | Stop


def is_printable_ascii(b: bytes) -> bool:
    return all(LINK_TEXT_CHAR_MIN <= c <= LINK_TEXT_CHAR_MAX for c in b)


def _text(t: int, b: bytes) -> str | BodyError:
    if not is_printable_ascii(b):
        return BodyError(t, "text_not_printable")
    return b.decode("ascii")


def check_action(a: Action) -> str | None:
    """Field rules for an ACTION (what agentsh enforces). Returns the violated rule or None."""
    if a.flags != 0:
        return "flags_nonzero"
    if a.p2 != 0:
        return "p2_nonzero"
    if not 0 <= a.nonce <= 0xFFFFFFFF:
        return "nonce_range"
    if not 0 <= a.text_id <= 255:
        return "text_id_range"
    op = a.op
    if not LINK_OP_MIN <= op <= LINK_OP_MAX:
        return "unknown_op"
    if op in TEXT_OPS:
        if a.text_id == 0:
            return "text_id_missing"
    elif a.text_id != 0:
        return "text_id_unused"
    if op == LINK_OP_FAN_SET:
        ok = 0 <= a.p1 <= LINK_DUTY_MAX
    elif op == LINK_OP_SERVO_SET:
        ok = 0 <= a.p1 <= LINK_DEG_MAX
    elif op == LINK_OP_LCD_SHOW:
        ok = 0 <= a.p1 <= LINK_LCD_LINE_MAX
    elif op in (LINK_OP_READ_OBJECT, LINK_OP_WRITE_OBJECT):
        ok = 0 <= a.p1 < LINK_OBJ_COUNT
    else:
        ok = a.p1 == 0
    return None if ok else "p1_range"


def encode_action(a: Action) -> bytes:
    """Packs an ACTION; raises ValueError if it breaks a field rule (the bridge never sends such a frame)."""
    err = check_action(a)
    if err:
        raise ValueError(f"ACTION rule violated: {err}")
    return struct.pack("<BBhhIB5x", a.op, a.flags, a.p1, a.p2, a.nonce, a.text_id)


def encode_text(text_id: int, text: str) -> bytes:
    b = text.encode("ascii")
    if not 1 <= text_id <= 255 or len(b) > LINK_TEXT_DATA_MAX or not is_printable_ascii(b):
        raise ValueError("TEXT rule violated")
    return bytes([text_id]) + b


def encode_final(kind: int, text: str) -> bytes:
    b = text.encode("ascii")
    if kind not in (LINK_FINAL_DONE, LINK_FINAL_REFUSE) or len(b) > LINK_FINAL_TEXT_MAX:
        raise ValueError("FINAL rule violated")
    if not is_printable_ascii(b):
        raise ValueError("FINAL rule violated")
    return bytes([kind]) + b


def encode_stop(reason: int) -> bytes:
    if not LINK_STOP_MIN <= reason <= LINK_STOP_MAX:
        raise ValueError("STOP rule violated")
    return bytes([reason])


def encode_hello(h: Hello) -> bytes:
    if len(h.build_id) != LINK_HELLO_BUILD_LEN or len(h.policy_prefix) != LINK_HELLO_POLICY_LEN:
        raise ValueError("HELLO rule violated")
    return struct.pack("<HBx", h.abi_version, h.variant) + h.build_id + h.policy_prefix


def encode_observation(o: Observation) -> bytes:
    if len(o.output) > LINK_OBS_OUT_MAX:
        raise ValueError("OBSERVATION rule violated")
    flags = LINK_OBSF_TRUNCATED if o.truncated else 0
    return struct.pack("<IiBB2x", o.nonce, o.result, o.op, flags) + o.output


def encode_audit(a: Audit) -> bytes:
    flags = LINK_AUDF_PERMISSIVE if a.permissive else 0
    return struct.pack(
        "<IIBBBBIiBB2x", a.seq, a.tick, a.kind, a.nr, a.code, a.reason, a.target, a.result, a.tainted, flags
    )


def encode_event(e: Event) -> bytes:
    return struct.pack("<BBBxII4i", e.kind, e.sub, e.flags, e.tick, e.nr, *e.args)


def encode_heartbeat(h: Heartbeat) -> bytes:
    return struct.pack("<IIB3x", h.tick, h.dropped, h.procs)


def _zero(b: bytes) -> bool:
    return not any(b)


def decode_body(t: int, body: bytes) -> Body | BodyError:
    """Decodes a body whose length is already inside ``BODY_BOUNDS[t]``. Never raises."""
    bounds = BODY_BOUNDS.get(t)
    if bounds is None:
        return BodyError(t, "unknown_type")
    if not bounds[0] <= len(body) <= bounds[1]:
        return BodyError(t, "body_len")
    if t == LINK_T_HELLO:
        ver, variant, rsv = struct.unpack_from("<HBB", body)
        if rsv:
            return BodyError(t, "rsv_nonzero")
        if variant not in (LINK_VARIANT_DEFAULT, LINK_VARIANT_PERMISSIVE):
            return BodyError(t, "variant")
        return Hello(ver, variant, bytes(body[4:16]), bytes(body[16:32]))
    if t == LINK_T_REQUEST:
        s = _text(t, body)
        return s if isinstance(s, BodyError) else Request(s)
    if t == LINK_T_OBSERVATION:
        nonce, result, op, flags = struct.unpack_from("<IiBB", body)
        if not _zero(body[LINK_OBS_OFF_RSV:LINK_OBS_OFF_OUT]):
            return BodyError(t, "rsv_nonzero")
        if flags & ~LINK_OBSF_MASK:
            return BodyError(t, "flags")
        return Observation(
            nonce, result, op, bool(flags & LINK_OBSF_TRUNCATED), bytes(body[LINK_OBS_OFF_OUT:])
        )
    if t == LINK_T_AUDIT:
        seq, tick, kind, nr, code, reason, target, result, tainted, flags = struct.unpack_from(
            "<IIBBBBIiBB", body
        )
        if not _zero(body[LINK_AUD_OFF_RSV:]):
            return BodyError(t, "rsv_nonzero")
        if flags & ~LINK_AUDF_MASK:
            return BodyError(t, "flags")
        if tainted > 1:
            return BodyError(t, "tainted")
        return Audit(seq, tick, kind, nr, code, reason, target, result, tainted, bool(flags))
    if t == LINK_T_EVENT:
        kind, sub, flags, rsv, tick, nr, a0, a1, a2, a3 = struct.unpack_from("<BBBBII4i", body)
        if rsv:
            return BodyError(t, "rsv_nonzero")
        return Event(kind, sub, flags, tick, nr, (a0, a1, a2, a3))
    if t == LINK_T_HEARTBEAT:
        tick, dropped, procs = struct.unpack_from("<IIB", body)
        if not _zero(body[LINK_HB_OFF_RSV:]):
            return BodyError(t, "rsv_nonzero")
        return Heartbeat(tick, dropped, procs)
    if t == LINK_T_ACTION:
        op, flags, p1, p2, nonce, text_id = struct.unpack_from("<BBhhIB", body)
        if not _zero(body[LINK_ACT_OFF_RSV:]):
            return BodyError(t, "rsv_nonzero")
        a = Action(op=op, p1=p1, p2=p2, nonce=nonce, text_id=text_id, flags=flags)
        err = check_action(a)
        return BodyError(t, err) if err else a
    if t == LINK_T_TEXT:
        if body[0] == 0:
            return BodyError(t, "text_id_zero")
        s = _text(t, body[1:])
        return s if isinstance(s, BodyError) else Text(body[0], s)
    if t == LINK_T_FINAL:
        if body[0] not in (LINK_FINAL_DONE, LINK_FINAL_REFUSE):
            return BodyError(t, "final_kind")
        s = _text(t, body[1:])
        return s if isinstance(s, BodyError) else Final(body[0], s)
    # LINK_T_STOP
    if not LINK_STOP_MIN <= body[0] <= LINK_STOP_MAX:
        return BodyError(t, "stop_reason")
    return Stop(body[0])
