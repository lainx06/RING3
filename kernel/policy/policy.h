/* RING3 policy engine: a pure function (D-28, D-45). Freestanding C11, no kernel headers, no globals, no clock:
 * every input arrives inside the event (the tick included). The same source is compiled into the kernel and,
 * unchanged, into build/host/policy_test (tests + replay). All allow/deny logic of the kernel lives here.
 *
 *   decide(const state, const event, next, decision)
 *
 * Contract: `next` is a full copy of `state` with the changes this event causes; a silent event that changes
 * nothing leaves `next` byte-identical to `state` (R2, R8). `decision.audit` says whether an audit record is
 * produced; `decision.action` tells the kernel what to do (execute the call, prompt, kill, halt). */
#ifndef RING3_POLICY_H
#define RING3_POLICY_H
#include <stdint.h>

#define POLICY_HZ 100u                        /* ticks per second; the kernel's PIT rate (prov.) */

/* ---- capabilities (ARCHITECTURE §3), default deny ---- */
#define CAP_CONSOLE    0x01u
#define CAP_OBJ_READ   0x02u
#define CAP_DEV_READ   0x04u
#define CAP_DEV_MOVE   0x08u
#define CAP_DEV_HMI    0x10u
#define CAP_AGENT_LINK 0x20u
#define CAP_ALL        0x3Fu

/* ---- syscall numbers (ARCHITECTURE §3) ---- */
enum pol_syscall {
    SYS_EXIT = 0, SYS_CONS_WRITE = 1, SYS_OBJ_LIST = 2, SYS_OBJ_READ = 3, SYS_OBJ_WRITE = 4,
    SYS_CELL_STATUS = 5, SYS_CELL_FAN = 6, SYS_CELL_SERVO = 7, SYS_CELL_HALT = 8, SYS_CELL_LCD = 9,
    SYS_UPTIME = 10, SYS_AGENT_SEND = 11, SYS_AGENT_RECV = 12, SYS_COUNT = 13
};

/* ---- error codes returned to ring 3 (negated) ---- */
#define EPERM      1
#define ENOENT     2
#define EACCES     13
#define EFAULT     14
#define EINVAL     22
#define ENOSYS     38
#define ETIMEDOUT  110
#define ECANCELED  125
#define ESAFETY    200     /* safety policy: limit, interlock, stale, E-stop (RING3-specific) */

/* ---- programs (capability profiles fixed at executor creation, INV-04) ---- */
enum pol_prog {
    PROG_NONE = 0,
    PROG_AGENTSH = 1,        /* the executor (T-03b): every capability */
    PROG_TEST_CONSOLE = 2,   /* fault programs, fuzzer: console only */
    PROG_TEST_NOCAP = 3,     /* empty capability set */
    PROG_TEST_FULL = 4,      /* confirmation / taint / device test programs */
    PROG_TEST_OBJ = 5,       /* console + objects */
    PROG_COUNT = 6
};
uint32_t policy_caps_for_prog(uint8_t prog);

/* ---- object store rules (ARCHITECTURE §3 object table) ---- */
enum pol_object { OBJ_CELL_SPEC = 0, OBJ_MAINT_NOTES = 1, OBJ_SCRATCH_0 = 2, OBJ_SCRATCH_1 = 3, OBJ_SCRATCH_2 = 4,
                  OBJ_SCRATCH_3 = 5, OBJ_AUDIT_LOG = 6, OBJ_COUNT = 7 };
#define OBJ_SLOTS     16u
#define OBJ_NAME_MAX  23u
#define OBJ_DATA_MAX  2048u
enum pol_rule { RULE_DENY = 0, RULE_ALLOW = 1, RULE_CONFIRM = 2 };
struct pol_object_rule {
    const char *name;
    uint8_t trusted;         /* 0 = untrusted: reading it taints the executor (D-27) */
    uint8_t read;            /* RULE_* */
    uint8_t write;           /* RULE_* */
};
const struct pol_object_rule *policy_object(uint32_t id);   /* 0 when id >= OBJ_COUNT */

/* ---- device limits (ARCHITECTURE §3a, D-21, D-43, D-47; all provisional) ---- */
#define SERVO_MIN_DEG        30
#define SERVO_MAX_DEG        150
#define SERVO_STEP_MAX_DEG   30
#define SERVO_MIN_GAP_TICKS  (200u * POLICY_HZ / 1000u)      /* 200 ms */
#define SERVO_PARK_DEG       90                               /* firmware park angle: the initial baseline */
#define FAN_MIN_SPIN         50                               /* D-47: measured minimum spinning duty */
#define FAN_MAX_DUTY         80
#define FAN_STEP_MAX         20                               /* points per command */
#define FAN_FIRST_STEP_MAX   60                               /* first step from 0 may go to 50..60 */
#define FAN_MIN_GAP_TICKS    (1u * POLICY_HZ)                 /* 1 s between increases */
#define PRESENCE_MM          100u                             /* closer than this = not clear */
#define DIST_NO_ECHO         0xFFFFu                          /* no echo = not clear (D-43) */
#define STALE_TICKS          (500u * POLICY_HZ / 1000u)       /* 500 ms */
#define LCD_TEXT_MAX         16u

/* ---- agent bounds (INV-07, D-53, D-58): ACTION frames per request (the 9th ends the executor with LIMIT);
 * the per-action tick quota counts ring-3 ticks only (paused in agent_recv and in a confirmation), the
 * per-request quota is wall clock. */
#define ACTIONS_PER_REQUEST  8u

/* ---- events ---- */
enum pol_event_kind {
    EV_NONE = 0,
    EV_EXEC_START = 1,   /* sub = prog, nr = caps, a0 = action quota ticks, a1 = request quota ticks,
                            a2 = confirmation timeout ticks */
    EV_EXEC_END = 2,     /* sub = END_*, a0 = exit code / fault vector / ticks used */
    EV_SYSCALL = 3,      /* nr = syscall number, a0..a3 = arguments, flags = EVF_* */
    EV_CONSOLE = 4,      /* sub = CONSOLE_* */
    EV_CONFIRM = 5,      /* sub = ANSWER_* (a timeout is a tick event, not an answer) */
    EV_TICK = 6,         /* timer tick */
    EV_TELEMETRY = 7,    /* flags = EVF_TELE_VALID, a0 = distance_mm, a1 = estop pressed */
    EV_ACTION_BOUNDARY = 8, /* agent_recv returned to the executor: the action quota restarts (R2) */
    EV_ACTION = 9,       /* a valid ACTION frame arrived on the link (D-53): counted per request, quota
                            restarts; a0 = link frame seq (header, not body: the kernel never parses bodies) */
    EV_LINK_DROP = 10,   /* a COM2 frame was dropped: sub = LINKDROP_*, a0 = raw length or frame type */
    EV_COUNT = 11
};
enum { END_EXIT = 0, END_FAULT = 1, END_LIMIT = 2 };
enum {
    LINKDROP_COBS = 1, LINKDROP_LEN = 2, LINKDROP_CRC = 3, LINKDROP_TYPE = 4, LINKDROP_BODY_LEN = 5,
    LINKDROP_OVERSIZE = 6, LINKDROP_OVERFLOW = 7, LINKDROP_QUEUE_FULL = 8, LINKDROP_FLUSHED = 9
};
enum { CONSOLE_OTHER = 0, CONSOLE_ESTOP_RESET = 1 };
enum { ANSWER_NO = 0, ANSWER_YES = 1 };
#define EVF_UACCESS_OK  0x01u   /* syscall: every user pointer passed uaccess (R3) */
#define EVF_STR_BAD     0x02u   /* syscall: a NUL-terminated input was unterminated or not printable (R6) */
#define EVF_TELE_VALID  0x01u   /* telemetry: frame decoded and CRC-checked */

struct pol_event {
    uint8_t kind, sub, flags, pad;
    uint32_t tick;
    uint32_t nr;
    int32_t a[4];
};

/* ---- state (small, plain, byte-comparable) ---- */
struct pol_state {
    uint32_t seq;                      /* audit sequence, kept across requests (R7) */
    /* request */
    uint8_t in_request, prog, tainted, fan_confirmed;
    uint8_t killing;                   /* LIMIT decided, EXEC_END not yet seen */
    uint8_t actions;                   /* ACTION frames accepted this request (D-53 cap) */
    uint8_t waiting;                   /* the executor is inside agent_recv: the action quota is paused (D-58) */
    uint8_t pad0;
    uint32_t caps;
    uint32_t req_start, req_limit;     /* ticks (R2: start ticks, not counters) */
    uint32_t act_start, act_limit;
    uint32_t confirm_limit;
    /* pending confirmation (R1) */
    uint8_t pending, pad1[3];
    uint32_t pend_nr;
    int32_t pend_a[4];
    uint32_t pend_since;
    /* commanded device state (R4 baselines: fan 0, servo 90) */
    uint8_t fan_duty, servo_deg, servo_moved, fan_changed;
    uint32_t fan_change_tick, servo_tick;
    /* latest telemetry + E-stop latch (kept across requests) */
    uint8_t tele_valid, estop_pressed, estop_latched, pad2;
    uint32_t tele_tick;
    uint16_t distance_mm, pad3;
};

/* ---- decisions ---- */
enum pol_code {
    DEC_NONE = 0, DEC_ALLOW, DEC_DENY_CAP, DEC_DENY_RULE, DEC_DENY_ARG, DEC_DENY_FAULT, DEC_DENY_LIMIT,
    DEC_DENY_INTERLOCK, DEC_DENY_STALE, DEC_DENY_ESTOP, DEC_CONFIRM_PENDING, DEC_CONFIRM_YES, DEC_CONFIRM_NO,
    DEC_FRAME_DROP, DEC_PROC_FAULT, DEC_LIMIT, DEC_AUTO_HALT, DEC_COUNT
};
enum pol_reason {
    R_NONE = 0, R_EXEC_START, R_EXEC_END, R_ARG, R_UACCESS, R_CAP, R_OBJ_RULE, R_OBJ_CONFIRM, R_TAINT,
    R_FIRST_FAN, R_FAN_RANGE, R_FAN_STEP, R_FAN_RATE, R_SERVO_RANGE, R_SERVO_STEP, R_SERVO_RATE,
    R_NO_TELEMETRY, R_STALE, R_PRESENCE, R_NO_ECHO, R_ESTOP, R_ESTOP_BUTTON, R_ESTOP_RESET, R_NOT_LATCHED,
    R_ANSWER, R_TIMEOUT, R_NO_PENDING, R_PENDING, R_ACTION_QUOTA, R_REQUEST_QUOTA, R_NOSYS, R_NO_REQUEST,
    R_HALT, R_BAD_FRAME, R_PERMISSIVE, R_ACTION_CAP, R_FLUSHED, R_COUNT
};
enum pol_action { ACT_NONE = 0, ACT_EXEC = 1, ACT_PROMPT = 2, ACT_KILL = 3, ACT_HALT = 4 };

struct pol_decision {
    uint8_t code;        /* DEC_* */
    uint8_t reason;      /* R_* */
    uint8_t audit;       /* 1 = produce an audit record (R8) */
    uint8_t action;      /* ACT_* */
    int32_t result;      /* -errno for a deny; 0 otherwise (the kernel fills the real result on ALLOW) */
    uint32_t target;     /* object id, duty, degrees, prog, vector: the audit record's target field */
};

void policy_state_init(struct pol_state *s);
void decide(const struct pol_state *s, const struct pol_event *ev, struct pol_state *next,
            struct pol_decision *out);

/* Names for audit records (plain ASCII, no banned words). */
const char *policy_code_name(uint8_t code);
const char *policy_reason_name(uint8_t reason);
const char *policy_syscall_name(uint32_t nr);
const char *policy_event_name(uint8_t kind);
const char *policy_variant_name(void);      /* "default" or "PERMISSIVE (EVAL ONLY)" */
int policy_is_permissive(void);
#endif
