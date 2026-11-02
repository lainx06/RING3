/* Host build of the RING3 policy (A5, D-28): unit tests over decide() plus `--replay`, which feeds TAPE lines
 * from stdin through the same source and prints the AUDIT lines the kernel would print (A7). */
#include "policy.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int checks, fails;
static const char *cur;

#define CHECK(c)                                                                                       \
    do {                                                                                               \
        checks++;                                                                                      \
        if (!(c)) {                                                                                    \
            fails++;                                                                                   \
            printf("FAIL %s: %s (line %d)\n", cur, #c, __LINE__);                                    \
        }                                                                                              \
    } while (0)

static void begin(const char *name) { cur = name; }
static void ok(void) { printf("ok %s\n", cur); }

/* ---- helpers ---- */
static struct pol_event ev_tick(uint32_t t)
{
    struct pol_event e = { EV_TICK, 0, 0, 0, t, 0, { 0, 0, 0, 0 } };
    return e;
}
static struct pol_event ev_start(uint32_t t, uint8_t prog, uint32_t caps, int32_t act, int32_t req, int32_t conf)
{
    struct pol_event e = { EV_EXEC_START, prog, 0, 0, t, caps, { act, req, conf, 0 } };
    return e;
}
static struct pol_event ev_end(uint32_t t, uint8_t kind, int32_t v)
{
    struct pol_event e = { EV_EXEC_END, kind, 0, 0, t, 0, { v, 0, 0, 0 } };
    return e;
}
static struct pol_event ev_call(uint32_t t, uint32_t nr, int32_t a0, int32_t a1, int32_t a2, uint8_t flags)
{
    struct pol_event e = { EV_SYSCALL, 0, flags, 0, t, nr, { a0, a1, a2, 0 } };
    return e;
}
static struct pol_event ev_sys(uint32_t t, uint32_t nr, int32_t a0, int32_t a1, int32_t a2)
{
    return ev_call(t, nr, a0, a1, a2, EVF_UACCESS_OK);
}
static struct pol_event ev_tele(uint32_t t, int32_t mm, int32_t estop, int valid)
{
    struct pol_event e = { EV_TELEMETRY, 0, valid ? EVF_TELE_VALID : 0, 0, t, 0, { mm, estop, 0, 0 } };
    return e;
}
static struct pol_event ev_answer(uint32_t t, int yes)
{
    struct pol_event e = { EV_CONFIRM, yes ? ANSWER_YES : ANSWER_NO, 0, 0, t, 0, { 0, 0, 0, 0 } };
    return e;
}
static struct pol_event ev_console(uint32_t t, uint8_t sub)
{
    struct pol_event e = { EV_CONSOLE, sub, 0, 0, t, 0, { 0, 0, 0, 0 } };
    return e;
}

/* Applies the event to *s (state := next) and returns the decision. */
static struct pol_decision step(struct pol_state *s, struct pol_event e)
{
    struct pol_state n;
    struct pol_decision d;
    decide(s, &e, &n, &d);
    if (d.audit)
        n.seq = s->seq + 1;
    *s = n;
    return d;
}

/* A fresh state inside a request with the given caps, telemetry clear and fresh at tick 1000. */
static struct pol_state fresh(uint32_t caps, int with_tele)
{
    struct pol_state s;
    policy_state_init(&s);
    step(&s, ev_start(1000, PROG_TEST_FULL, caps, 500, 6000, 3000));
    if (with_tele)
        step(&s, ev_tele(1000, 300, 0, 1));
    return s;
}

#define T 1000u

/* ---- tests ---- */
static void t_object_table(void)
{
    begin("object_table (ARCHITECTURE §3, one check per row and direction)");
    const struct { uint32_t id; const char *name; int trusted; int read, write; } rows[] = {
        { OBJ_CELL_SPEC, "cell_spec", 1, RULE_ALLOW, RULE_DENY },
        { OBJ_MAINT_NOTES, "maint_notes", 0, RULE_ALLOW, RULE_CONFIRM },
        { OBJ_SCRATCH_0, "scratch_0", 0, RULE_ALLOW, RULE_ALLOW },
        { OBJ_SCRATCH_1, "scratch_1", 0, RULE_ALLOW, RULE_ALLOW },
        { OBJ_SCRATCH_2, "scratch_2", 0, RULE_ALLOW, RULE_ALLOW },
        { OBJ_SCRATCH_3, "scratch_3", 0, RULE_ALLOW, RULE_ALLOW },
        { OBJ_AUDIT_LOG, "audit_log", 1, RULE_ALLOW, RULE_DENY },
    };
    CHECK(OBJ_COUNT == 7 && policy_object(7) == 0 && policy_object(0xFFFFFFFFu) == 0);
    for (unsigned i = 0; i < 7; i++) {
        const struct pol_object_rule *o = policy_object(rows[i].id);
        CHECK(o && strcmp(o->name, rows[i].name) == 0 && strlen(o->name) <= OBJ_NAME_MAX);
        CHECK(o->trusted == rows[i].trusted && o->read == rows[i].read && o->write == rows[i].write);
        /* read */
        struct pol_state s = fresh(CAP_OBJ_READ, 0);
        struct pol_decision d = step(&s, ev_sys(T, SYS_OBJ_READ, (int32_t)rows[i].id, 0x40001000, 64));
        CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && d.audit);
        CHECK(s.tainted == (rows[i].trusted ? 0 : 1));                       /* D-27 */
        /* write, untainted */
        s = fresh(CAP_OBJ_READ, 0);
        d = step(&s, ev_sys(T, SYS_OBJ_WRITE, (int32_t)rows[i].id, 0x40001000, 10));
        if (policy_is_permissive())
            CHECK(d.code == DEC_ALLOW);                                      /* §7: every object rule allows */
        else if (rows[i].write == RULE_DENY)
            CHECK(d.code == DEC_DENY_RULE && d.result == -EACCES && d.action == ACT_NONE);
        else if (rows[i].write == RULE_CONFIRM)
            CHECK(d.code == DEC_CONFIRM_PENDING && d.reason == R_OBJ_CONFIRM);
        else
            CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC);
    }
    ok();
}

static void t_syscall_caps(void)
{
    begin("syscall_caps (ARCHITECTURE §3 syscall table, one check per row)");
    const struct { uint32_t nr; uint32_t cap; } rows[] = {
        { SYS_CONS_WRITE, CAP_CONSOLE }, { SYS_OBJ_LIST, CAP_OBJ_READ }, { SYS_OBJ_READ, CAP_OBJ_READ },
        { SYS_OBJ_WRITE, CAP_OBJ_READ }, { SYS_CELL_STATUS, CAP_DEV_READ }, { SYS_CELL_FAN, CAP_DEV_MOVE },
        { SYS_CELL_SERVO, CAP_DEV_MOVE }, { SYS_CELL_LCD, CAP_DEV_HMI }, { SYS_AGENT_SEND, CAP_AGENT_LINK },
        { SYS_AGENT_RECV, CAP_AGENT_LINK },
    };
    for (unsigned i = 0; i < sizeof rows / sizeof rows[0]; i++) {
        /* cell_fan is probed with a non-zero duty: fan(0) is energy-reducing and needs no capability (D-52) */
        int32_t a0 = rows[i].nr == SYS_CELL_FAN ? 60 : rows[i].nr == SYS_CELL_LCD ? 0
                     : rows[i].nr == SYS_CELL_SERVO ? 90 : 0x40001000;
        int32_t a1 = rows[i].nr == SYS_OBJ_READ || rows[i].nr == SYS_OBJ_WRITE ? 0x40001000 : 8;
        if (rows[i].nr == SYS_OBJ_READ || rows[i].nr == SYS_OBJ_WRITE)
            a0 = OBJ_SCRATCH_0;
        /* every other capability but the needed one: denied */
        struct pol_state s = fresh(CAP_ALL & ~rows[i].cap, 1);
        struct pol_decision d = step(&s, ev_sys(T, rows[i].nr, a0, a1, 8));
        if (policy_is_permissive())
            CHECK(d.code != DEC_DENY_CAP);
        else
            CHECK(d.code == DEC_DENY_CAP && d.result == -EPERM && d.action == ACT_NONE && d.reason == R_CAP);
        /* only the needed one: not a capability denial */
        s = fresh(rows[i].cap, 1);
        d = step(&s, ev_sys(T, rows[i].nr, a0, a1, 8));
        CHECK(d.code != DEC_DENY_CAP);
    }
    /* always-allowed rows */
    struct pol_state s = fresh(0, 0);
    CHECK(step(&s, ev_sys(T, SYS_UPTIME, 0, 0, 0)).code == DEC_ALLOW);
    CHECK(step(&s, ev_sys(T, SYS_CELL_HALT, 0, 0, 0)).code == DEC_ALLOW);
    CHECK(step(&s, ev_sys(T, SYS_EXIT, 0, 0, 0)).code == DEC_ALLOW);
    ok();
}

static void t_default_deny(void)
{
    begin("default_deny (INV-04: empty capability set, unknown program, unknown syscall)");
    CHECK(policy_caps_for_prog(PROG_TEST_NOCAP) == (policy_is_permissive() ? CAP_ALL : 0));
    CHECK(policy_caps_for_prog(99) == (policy_is_permissive() ? CAP_ALL : 0));
    CHECK(policy_caps_for_prog(PROG_AGENTSH) == CAP_ALL);
    CHECK(policy_caps_for_prog(PROG_TEST_CONSOLE) == (policy_is_permissive() ? CAP_ALL : CAP_CONSOLE));
    struct pol_state s = fresh(0, 1);
    for (uint32_t nr = 0; nr < SYS_COUNT; nr++) {
        if (nr == SYS_EXIT || nr == SYS_UPTIME || nr == SYS_CELL_HALT)
            continue;
        int32_t a0 = nr == SYS_CELL_FAN ? 60 : nr == SYS_CELL_SERVO ? 100 : nr == SYS_CELL_LCD ? 0
                     : nr == SYS_OBJ_READ || nr == SYS_OBJ_WRITE ? 2 : 0x40001000;
        struct pol_decision d = step(&s, ev_sys(T, nr, a0, 0x40001000, 8));
        if (!policy_is_permissive())
            CHECK(d.code == DEC_DENY_CAP);
    }
    struct pol_decision d = step(&s, ev_sys(T, 13, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.result == -ENOSYS && d.reason == R_NOSYS);
    d = step(&s, ev_sys(T, 0xFFFFFFFFu, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.result == -ENOSYS);
    /* no request running: everything but nothing executes */
    struct pol_state idle;
    policy_state_init(&idle);
    d = step(&idle, ev_sys(T, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_NO_REQUEST);
    ok();
}

static void t_no_escalation(void)
{
    begin("no_escalation (INV-04: no event adds a capability after EXEC_START)");
    struct pol_state s = fresh(CAP_CONSOLE, 1);
    uint32_t caps = s.caps;
    struct pol_event evs[] = {
        ev_sys(T, SYS_CELL_FAN, 60, 0, 0), ev_sys(T, SYS_OBJ_READ, 1, 0x40001000, 8),
        ev_sys(T, 0xFFFFFFFFu, 0x3F, 0x3F, 0x3F), ev_answer(T, 1), ev_console(T, CONSOLE_ESTOP_RESET),
        ev_console(T, CONSOLE_OTHER), ev_tick(T + 1), ev_tele(T + 1, 300, 0, 1),
        ev_start(T + 2, PROG_AGENTSH, CAP_ALL, 500, 6000, 3000),           /* a second start while running */
        { EV_ACTION_BOUNDARY, 0, 0, 0, T + 2, CAP_ALL, { 0x3F, 0x3F, 0x3F, 0x3F } },
        { 200, 0, 0, 0, T + 2, CAP_ALL, { 0, 0, 0, 0 } },                    /* unknown event kind */
    };
    for (unsigned i = 0; i < sizeof evs / sizeof evs[0]; i++) {
        struct pol_decision d = step(&s, evs[i]);
        CHECK(s.caps == caps);
        if (evs[i].kind == EV_EXEC_START)
            CHECK(d.code == DEC_DENY_RULE && s.in_request == 1 && s.prog == PROG_TEST_FULL);
    }
    /* taint and the E-stop latch only ever remove authority */
    step(&s, ev_tele(T + 3, 300, 1, 1));
    CHECK(s.estop_latched == 1 && s.caps == caps);
    ok();
}

static void t_taint(void)
{
    begin("taint_forces_confirm (INV-15, D-27: every write and motion while tainted)");
    struct pol_state s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));                           /* first fan: pending */
    if (!policy_is_permissive())
        step(&s, ev_answer(T, 1));
    CHECK(s.fan_duty == 60 && s.fan_confirmed && !s.tainted);
    struct pol_decision d = step(&s, ev_sys(T + 200, SYS_OBJ_WRITE, OBJ_SCRATCH_0, 0x40001000, 4));
    CHECK(d.code == DEC_ALLOW);                                             /* untainted scratch write: no prompt */
    d = step(&s, ev_sys(T + 200, SYS_OBJ_READ, OBJ_MAINT_NOTES, 0x40001000, 64));
    CHECK(d.code == DEC_ALLOW && s.tainted == 1);
    uint8_t want = policy_is_permissive() ? DEC_ALLOW : DEC_CONFIRM_PENDING;
    d = step(&s, ev_sys(T + 200, SYS_OBJ_WRITE, OBJ_SCRATCH_0, 0x40001000, 4));
    CHECK(d.code == want && (policy_is_permissive() || d.reason == R_TAINT));
    if (!policy_is_permissive())
        step(&s, ev_answer(T + 201, 0));
    step(&s, ev_tele(T + 300, 300, 0, 1));
    d = step(&s, ev_sys(T + 300, SYS_CELL_FAN, 70, 0, 0));                  /* fan already confirmed: taint prompts */
    CHECK(d.code == want);
    if (!policy_is_permissive())
        step(&s, ev_answer(T + 301, 0));
    step(&s, ev_tele(T + 400, 300, 0, 1));
    d = step(&s, ev_sys(T + 400, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == want);
    if (!policy_is_permissive())
        step(&s, ev_answer(T + 401, 0));
    /* never: halt and fan(0) */
    d = step(&s, ev_sys(T + 500, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0);
    d = step(&s, ev_sys(T + 500, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.reason == R_HALT);
    /* audit_log still denied while tainted (the planted instruction) */
    d = step(&s, ev_sys(T + 500, SYS_OBJ_WRITE, OBJ_AUDIT_LOG, 0x40001000, 4));
    CHECK(d.code == (policy_is_permissive() ? DEC_ALLOW : DEC_DENY_RULE));
    /* hard limits still apply on top of taint: fan 100 denied before any prompt */
    step(&s, ev_tele(T + 600, 300, 0, 1));
    d = step(&s, ev_sys(T + 600, SYS_CELL_FAN, 100, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_RANGE && d.result == -ESAFETY);
    ok();

    begin("taint_not_clearable (INV-15: no event clears taint inside a request)");
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_OBJ_READ, OBJ_SCRATCH_2, 0x40001000, 8));
    CHECK(s.tainted);
    struct pol_event evs[] = {
        ev_sys(T, SYS_OBJ_READ, OBJ_CELL_SPEC, 0x40001000, 8), ev_sys(T, SYS_CELL_HALT, 0, 0, 0),
        ev_sys(T, SYS_UPTIME, 0, 0, 0), ev_sys(T, 0xFFFF, 0, 0, 0), ev_answer(T, 1), ev_answer(T, 0),
        ev_console(T, CONSOLE_ESTOP_RESET), ev_console(T, CONSOLE_OTHER), ev_tick(T + 1000), ev_tele(T + 1000, 300, 0, 1),
        ev_start(T + 1000, PROG_AGENTSH, CAP_ALL, 500, 6000, 3000),
        { EV_ACTION_BOUNDARY, 0, 0, 0, T + 1000, 0, { 0, 0, 0, 0 } },
    };
    for (unsigned i = 0; i < sizeof evs / sizeof evs[0]; i++) {
        step(&s, evs[i]);
        CHECK(s.tainted == 1);
    }
    step(&s, ev_end(T + 1001, END_EXIT, 0));
    step(&s, ev_start(T + 1002, PROG_AGENTSH, CAP_ALL, 500, 6000, 3000));
    CHECK(s.tainted == 0);                                                  /* only a new request is clean (R7) */
    ok();
}

static void t_halt_always(void)
{
    begin("halt_always (INV-16: cell_halt tainted, under E-stop, without capabilities)");
    struct pol_state s = fresh(0, 0);
    step(&s, ev_sys(T, SYS_OBJ_READ, OBJ_MAINT_NOTES, 0x40001000, 8));  /* DENY_CAP; still untainted */
    step(&s, ev_tele(T, 300, 1, 1));                                      /* E-stop latched */
    s.tainted = 1;                                                        /* force the worst state */
    s.fan_duty = 60;
    struct pol_decision d = step(&s, ev_sys(T, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && d.reason == R_HALT && s.fan_duty == 0);
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code != DEC_ALLOW);                                           /* adding energy never bypasses */
    /* no telemetry at all: halt still allowed, motion never */
    s = fresh(CAP_ALL, 0);
    CHECK(step(&s, ev_sys(T, SYS_CELL_HALT, 0, 0, 0)).code == DEC_ALLOW);
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_STALE && d.reason == R_NO_TELEMETRY && d.result == -ESAFETY);
    ok();
}

static struct pol_decision fan(struct pol_state *s, uint32_t t, int duty)
{
    struct pol_decision d = step(s, ev_sys(t, SYS_CELL_FAN, duty, 0, 0));
    if (d.code == DEC_CONFIRM_PENDING)
        d = step(s, ev_answer(t, 1));
    return d;
}

static void t_fan_rules(void)
{
    begin("fan_rules (D-47: 0 or 50-80, +<=20 per command, >=1 s apart, first step 0->50..60, decreases free)");
    struct pol_state s = fresh(CAP_ALL, 1);
    struct pol_decision d;
    const int bad_arg[] = { -1, -100, 1, 30, 49, 101, 1000 };
    for (unsigned i = 0; i < sizeof bad_arg / sizeof bad_arg[0]; i++) {
        d = fan(&s, T, bad_arg[i]);
        CHECK(d.code == DEC_DENY_ARG && d.result == -EINVAL && d.reason == R_ARG && s.fan_duty == 0);
    }
    for (int duty = 81; duty <= 100; duty++) {
        d = fan(&s, T, duty);
        CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_RANGE && d.result == -ESAFETY);
    }
    d = fan(&s, T, 70);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_STEP);           /* first step must be 50..60 */
    d = fan(&s, T, 80);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_STEP);           /* 0->80 after a long idle: denied */
    d = fan(&s, T, 60);
    CHECK((d.code == DEC_CONFIRM_YES || d.code == DEC_ALLOW) && s.fan_duty == 60 && s.fan_confirmed);
    d = fan(&s, T, 80);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_RATE);           /* same tick: rate */
    step(&s, ev_tele(T + 99, 300, 0, 1));
    d = fan(&s, T + 99, 80);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_RATE);           /* 990 ms: still too soon */
    step(&s, ev_tele(T + 100, 300, 0, 1));
    d = fan(&s, T + 100, 81);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_RANGE);
    d = fan(&s, T + 100, 80);
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 80 && s.fan_change_tick == T + 100);
    s.distance_mm = 50;                   /* a hand, before the lease has run (D-52: a non-zero decrease keeps the
                                             presence gate; only fan(0) and halt are unconditional) */
    d = fan(&s, T + 100, 50);
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_PRESENCE && s.fan_duty == 80);
    s.distance_mm = 300;
    d = fan(&s, T + 100, 0);
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0);
    d = fan(&s, T + 100, 60);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_RATE);           /* 0->60 but changed this tick */
    step(&s, ev_tele(T + 200, 300, 0, 1));
    d = fan(&s, T + 200, 60);
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 60);                      /* fan already confirmed: no prompt */
    step(&s, ev_tele(T + 300, 300, 0, 1));
    d = fan(&s, T + 300, 50);
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 50);                      /* decrease */
    step(&s, ev_tele(T + 400, 300, 0, 1));
    d = fan(&s, T + 400, 71);
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_FAN_STEP);           /* +21 */
    d = fan(&s, T + 400, 70);
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 70);                      /* +20 */
    /* a clean request: the first fan > 0 needs confirmation again, fan 0 never */
    s = fresh(CAP_ALL, 1);
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW);
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 50, 0, 0));
    CHECK(d.code == (policy_is_permissive() ? DEC_ALLOW : DEC_CONFIRM_PENDING) &&
          (policy_is_permissive() || d.reason == R_FIRST_FAN));
    ok();
}

static void t_servo_rules(void)
{
    begin("servo_rules (30-150, <=30 per command, >=200 ms apart, baseline 90)");
    struct pol_state s = fresh(CAP_ALL, 1);
    struct pol_decision d;
    const int bad_arg[] = { -1, 181, 1000, -90 };
    for (unsigned i = 0; i < 4; i++) {
        d = step(&s, ev_sys(T, SYS_CELL_SERVO, bad_arg[i], 0, 0));
        CHECK(d.code == DEC_DENY_ARG && d.result == -EINVAL);
    }
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 29, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_SERVO_RANGE && d.result == -ESAFETY);
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 151, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_SERVO_RANGE);
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 121, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_SERVO_STEP);        /* 90 -> 121 = 31 (R4 baseline) */
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 59, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_SERVO_STEP);
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 120, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.servo_deg == 120 && s.servo_tick == T);
    d = step(&s, ev_sys(T + 19, SYS_CELL_SERVO, 130, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_SERVO_RATE);        /* 190 ms */
    d = step(&s, ev_sys(T + 20, SYS_CELL_SERVO, 150, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.servo_deg == 150);
    d = step(&s, ev_sys(T + 40, SYS_CELL_SERVO, 150, 0, 0));
    CHECK(d.code == DEC_ALLOW);                                          /* same angle: a move command, fine */
    d = step(&s, ev_sys(T + 60, SYS_CELL_SERVO, 119, 0, 0));
    CHECK(d.code == DEC_DENY_LIMIT && d.reason == R_SERVO_STEP);
    ok();
}

static void t_interlock(void)
{
    begin("interlock (presence < 100 mm and no echo 0xFFFF block motion, D-43)");
    struct pol_state s = fresh(CAP_ALL, 1);
    struct pol_decision d;
    step(&s, ev_tele(T, 99, 0, 1));
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_PRESENCE && d.result == -ESAFETY);
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_PRESENCE);
    step(&s, ev_tele(T, 0xFFFF, 0, 1));
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_NO_ECHO);
    step(&s, ev_tele(T, 0, 0, 1));
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_PRESENCE);
    step(&s, ev_tele(T, 100, 0, 1));                                     /* exactly 100 mm: clear */
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == DEC_ALLOW);
    /* fan 0 and halt go through under presence */
    step(&s, ev_tele(T, 50, 0, 1));
    CHECK(step(&s, ev_sys(T, SYS_CELL_FAN, 0, 0, 0)).code == DEC_ALLOW);
    CHECK(step(&s, ev_sys(T, SYS_CELL_HALT, 0, 0, 0)).code == DEC_ALLOW);
    ok();
}

static void t_stale(void)
{
    begin("stale (telemetry older than 500 ms or never seen blocks motion; bad frames dropped)");
    struct pol_state s = fresh(CAP_ALL, 0);
    struct pol_decision d = step(&s, ev_sys(T, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == DEC_DENY_STALE && d.reason == R_NO_TELEMETRY);
    d = step(&s, ev_tele(T, 300, 0, 0));                                  /* CRC failure */
    CHECK(d.code == DEC_FRAME_DROP && d.audit && s.tele_valid == 0);
    step(&s, ev_tele(T, 300, 0, 1));
    d = step(&s, ev_sys(T + 50, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == DEC_ALLOW);                                           /* exactly 500 ms old: fresh */
    d = step(&s, ev_sys(T + 71, SYS_CELL_SERVO, 110, 0, 0));
    CHECK(d.code == DEC_DENY_STALE && d.reason == R_STALE && d.result == -ESAFETY);
    d = step(&s, ev_sys(T + 51, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_STALE);
    ok();
}

static void t_estop(void)
{
    begin("estop (latch on the button, DENY_ESTOP, reset only on fresh telemetry with the button released)");
    struct pol_state s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    CHECK(s.fan_duty == 60);
    struct pol_decision d = step(&s, ev_tele(T + 10, 300, 1, 1));
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_ESTOP && d.audit && d.action == ACT_HALT);
    CHECK(s.estop_latched && s.fan_duty == 0);
    d = step(&s, ev_tele(T + 15, 300, 1, 1));
    CHECK(d.audit == 0);                                                  /* still pressed: silent */
    d = step(&s, ev_sys(T + 20, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_ESTOP && d.reason == R_ESTOP && d.result == -ESAFETY);
    d = step(&s, ev_sys(T + 20, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == DEC_DENY_ESTOP);
    CHECK(step(&s, ev_sys(T + 20, SYS_CELL_HALT, 0, 0, 0)).code == DEC_ALLOW);
    /* reset attempts */
    d = step(&s, ev_console(T + 30, CONSOLE_ESTOP_RESET));
    CHECK(d.code == DEC_DENY_ESTOP && d.reason == R_ESTOP_BUTTON && s.estop_latched);   /* still pressed */
    step(&s, ev_tele(T + 40, 300, 0, 1));                                 /* released */
    CHECK(s.estop_latched);                                               /* release alone never clears */
    d = step(&s, ev_console(T + 100, CONSOLE_ESTOP_RESET));
    CHECK(d.code == DEC_DENY_ESTOP && d.reason == R_STALE && s.estop_latched);           /* 600 ms old */
    step(&s, ev_tele(T + 100, 300, 0, 1));
    d = step(&s, ev_console(T + 101, CONSOLE_ESTOP_RESET));
    CHECK(d.code == DEC_ALLOW && d.reason == R_ESTOP_RESET && !s.estop_latched);
    d = step(&s, ev_console(T + 102, CONSOLE_ESTOP_RESET));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_NOT_LATCHED);
    d = step(&s, ev_sys(T + 2000, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_STALE);                                      /* telemetry aged meanwhile */
    step(&s, ev_tele(T + 2000, 300, 0, 1));
    d = step(&s, ev_sys(T + 2000, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 60);                       /* motion again after the reset */
    /* no telemetry at all: reset refused */
    s = fresh(CAP_ALL, 0);
    s.estop_latched = 1;
    d = step(&s, ev_console(T, CONSOLE_ESTOP_RESET));
    CHECK(d.code == DEC_DENY_ESTOP && d.reason == R_NO_TELEMETRY && s.estop_latched);
    /* the latch outlives the request (R7) */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_tele(T, 300, 1, 1));
    step(&s, ev_end(T + 1, END_EXIT, 0));
    step(&s, ev_start(T + 2, PROG_AGENTSH, CAP_ALL, 500, 6000, 3000));
    CHECK(s.estop_latched);
    ok();
}

static void t_tick_autohalt(void)
{
    begin("tick_autohalt (INV-16, D-30: lease on every tick; stale, presence, no echo while moving)");
    struct pol_state s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    struct pol_decision d = step(&s, ev_tick(T + 50));
    CHECK(d.audit == 0 && s.fan_duty == 60);                              /* 500 ms: fresh */
    d = step(&s, ev_tick(T + 51));
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_STALE && d.action == ACT_HALT && d.target == 60);
    CHECK(s.fan_duty == 0 && s.fan_change_tick == T + 51);
    /* presence arriving in a telemetry sample */
    step(&s, ev_tele(T + 200, 300, 0, 1));
    fan(&s, T + 200, 60);
    CHECK(s.fan_duty == 60);
    d = step(&s, ev_tele(T + 210, 80, 0, 1));
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_PRESENCE && s.fan_duty == 0);
    /* no echo */
    step(&s, ev_tele(T + 400, 300, 0, 1));
    fan(&s, T + 400, 60);
    d = step(&s, ev_tele(T + 410, 0xFFFF, 0, 1));
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_NO_ECHO && s.fan_duty == 0);
    /* nothing moving: a hazard is not audited as a halt */
    struct pol_event b = { EV_ACTION_BOUNDARY, 0, 0, 0, T + 900, 0, { 0, 0, 0, 0 } };
    step(&s, b);
    d = step(&s, ev_tick(T + 1000));
    CHECK(d.audit == 0);
    ok();
}

static void t_R1(void)
{
    begin("R1_two_phase (hazards appearing during a pending confirmation deny the call even after y)");
    struct pol_state s = fresh(CAP_ALL, 1);
    if (policy_is_permissive()) { ok(); return; }
    struct pol_decision d = step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_CONFIRM_PENDING && d.action == ACT_PROMPT && s.pending && s.pend_nr == SYS_CELL_FAN);
    CHECK(s.fan_duty == 0 && !s.fan_confirmed);                           /* nothing executed */
    d = step(&s, ev_sys(T + 1, SYS_UPTIME, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_PENDING);              /* no second call while pending */
    step(&s, ev_tele(T + 5, 50, 0, 1));                                   /* a hand appears */
    d = step(&s, ev_answer(T + 10, 1));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_PRESENCE && !s.pending && s.fan_duty == 0);
    /* stale during the wait */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    d = step(&s, ev_answer(T + 60, 1));
    CHECK(d.code == DEC_DENY_STALE && !s.pending);
    /* E-stop during the wait */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    step(&s, ev_tele(T + 5, 300, 1, 1));
    d = step(&s, ev_answer(T + 10, 1));
    CHECK(d.code == DEC_DENY_ESTOP && !s.pending);
    /* the clean case: y -> ALLOW + CONFIRM_YES, executed */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    step(&s, ev_tele(T + 5, 300, 0, 1));
    d = step(&s, ev_answer(T + 10, 1));
    CHECK(d.code == DEC_CONFIRM_YES && d.action == ACT_EXEC && d.reason == R_FIRST_FAN && s.fan_duty == 60);
    CHECK(s.fan_change_tick == T + 10);                                   /* the answer's tick */
    /* n -> CONFIRM_NO, -ECANCELED; a stray answer with nothing pending is silent */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    d = step(&s, ev_answer(T + 10, 0));
    CHECK(d.code == DEC_CONFIRM_NO && d.result == -ECANCELED && d.reason == R_ANSWER && !s.pending);
    d = step(&s, ev_answer(T + 11, 1));
    CHECK(d.audit == 0 && d.code == DEC_NONE && s.fan_duty == 0);
    /* timeout is a tick decision */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    d = step(&s, ev_tick(T + 2999));
    CHECK(d.audit == 0 && s.pending);
    d = step(&s, ev_tick(T + 3000));
    CHECK(d.code == DEC_CONFIRM_NO && d.reason == R_TIMEOUT && d.result == -ECANCELED && !s.pending);
    ok();
}

static void t_R2(void)
{
    begin("R2_quotas_are_tick_state (action quota paused while pending, request quota never; silent ticks)");
    struct pol_state s = fresh(CAP_ALL, 1);                               /* action 500, request 6000 */
    struct pol_decision d = step(&s, ev_tick(T + 499));
    CHECK(d.audit == 0 && d.action == ACT_NONE);
    d = step(&s, ev_tick(T + 500));
    CHECK(d.code == DEC_LIMIT && d.reason == R_ACTION_QUOTA && d.action == ACT_KILL && d.audit && s.killing);
    d = step(&s, ev_tick(T + 501));
    CHECK(d.audit == 0);                                                  /* decided once */
    d = step(&s, ev_end(T + 502, END_LIMIT, 500));
    CHECK(d.code == DEC_LIMIT && d.audit && !s.in_request && !s.killing);
    /* action boundaries restart the action quota; the request quota still ends it */
    s = fresh(CAP_ALL, 1);
    for (uint32_t t = T + 400; t < T + 6000; t += 400) {
        struct pol_event b = { EV_ACTION_BOUNDARY, 0, 0, 0, t, 0, { 0, 0, 0, 0 } };
        d = step(&s, b);
        CHECK(d.audit == 0 && s.act_start == t);
        d = step(&s, ev_tick(t + 1));
        CHECK(d.audit == 0);
    }
    d = step(&s, ev_tick(T + 6000));
    CHECK(d.code == DEC_LIMIT && d.reason == R_REQUEST_QUOTA);
    if (!policy_is_permissive()) {
        /* pending pauses the action quota */
        s = fresh(CAP_ALL, 1);
        step(&s, ev_tick(T + 300));
        step(&s, ev_tele(T + 300, 300, 0, 1));
        step(&s, ev_sys(T + 300, SYS_CELL_FAN, 60, 0, 0));               /* pending at T+300 */
        CHECK(s.pending);
        d = step(&s, ev_tick(T + 900));
        CHECK(d.audit == 0 && s.pending);                                 /* 600 ticks later: no LIMIT */
        step(&s, ev_tele(T + 1000, 300, 0, 1));
        d = step(&s, ev_answer(T + 1000, 1));
        CHECK(d.code == DEC_CONFIRM_YES && s.act_start == T + 700);       /* paused 700 ticks */
        step(&s, ev_sys(T + 1000, SYS_CELL_FAN, 0, 0, 0));               /* stop it: no lease events below */
        d = step(&s, ev_tick(T + 1199));
        CHECK(d.audit == 0);
        d = step(&s, ev_tick(T + 1200));
        CHECK(d.code == DEC_LIMIT && d.reason == R_ACTION_QUOTA);
        /* ... but not the request quota */
        s = fresh(CAP_ALL, 1);
        step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
        s.confirm_limit = 100000;                                         /* a very patient human */
        d = step(&s, ev_tick(T + 6000));
        CHECK(d.code == DEC_LIMIT && d.reason == R_REQUEST_QUOTA && d.action == ACT_KILL);
        d = step(&s, ev_end(T + 6001, END_LIMIT, 6000));
        CHECK(!s.pending && !s.in_request);
    }
    /* a silent tick leaves the state byte-identical */
    s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    struct pol_state before = s, next;
    struct pol_event t1 = ev_tick(T + 1);
    decide(&before, &t1, &next, &d);
    CHECK(memcmp(&before, &next, sizeof next) == 0 && d.audit == 0);
    struct pol_event other = ev_console(T + 1, CONSOLE_OTHER);
    decide(&before, &other, &next, &d);
    CHECK(memcmp(&before, &next, sizeof next) == 0 && d.audit == 0);
    ok();
}

static void t_R3(void)
{
    begin("R3_uaccess_first (uaccess_ok = 0 -> DENY_FAULT with the state unchanged)");
    uint32_t ptr_calls[] = { SYS_CONS_WRITE, SYS_OBJ_LIST, SYS_OBJ_READ, SYS_OBJ_WRITE, SYS_CELL_STATUS,
                             SYS_CELL_LCD, SYS_AGENT_SEND, SYS_AGENT_RECV };
    for (unsigned i = 0; i < 8; i++) {
        struct pol_state s = fresh(CAP_ALL, 1), next;
        struct pol_decision d;
        int32_t a0 = ptr_calls[i] == SYS_OBJ_READ || ptr_calls[i] == SYS_OBJ_WRITE ? OBJ_MAINT_NOTES : 0;
        struct pol_event e = ev_call(T, ptr_calls[i], a0, 0, 8, 0);
        decide(&s, &e, &next, &d);
        CHECK(d.code == DEC_DENY_FAULT && d.result == -EFAULT && d.reason == R_UACCESS && d.action == ACT_NONE);
        CHECK(memcmp(&s, &next, sizeof s) == 0);                          /* no taint, no prompt, no baseline */
    }
    /* non-pointer calls ignore the flag */
    struct pol_state s = fresh(CAP_ALL, 1);
    CHECK(step(&s, ev_call(T, SYS_UPTIME, 0, 0, 0, 0)).code == DEC_ALLOW);
    CHECK(step(&s, ev_call(T, SYS_CELL_HALT, 0, 0, 0, 0)).code == DEC_ALLOW);
    /* a bad pointer on a tainting read does not taint; on a confirm-write does not prompt */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_call(T, SYS_OBJ_READ, OBJ_MAINT_NOTES, 0, 8, 0));
    CHECK(!s.tainted);
    step(&s, ev_call(T, SYS_OBJ_WRITE, OBJ_MAINT_NOTES, 0, 8, 0));
    CHECK(!s.pending);
    ok();
}

static void t_R4(void)
{
    begin("R4_real_baselines (servo 90, fan 0, fan_changed/servo_moved gates)");
    struct pol_state s;
    policy_state_init(&s);
    CHECK(s.servo_deg == 90 && s.fan_duty == 0 && !s.servo_moved && !s.fan_changed && s.seq == 0);
    s = fresh(CAP_ALL, 1);
    CHECK(step(&s, ev_sys(T, SYS_CELL_SERVO, 60, 0, 0)).code == DEC_ALLOW);     /* 90 - 30 */
    s = fresh(CAP_ALL, 1);
    CHECK(step(&s, ev_sys(T, SYS_CELL_SERVO, 121, 0, 0)).code == DEC_DENY_LIMIT);
    s = fresh(CAP_ALL, 1);
    CHECK(fan(&s, T, 80).code == DEC_DENY_LIMIT);                              /* 0 -> 80 after a long idle */
    CHECK(fan(&s, T, 50).code != DEC_DENY_LIMIT && s.fan_duty == 50);
    CHECK(fan(&s, T, 70).reason == R_FAN_RATE);                                 /* same tick: 0->50->70 */
    ok();
}

static void t_R5(void)
{
    begin("R5_argument_first (negative and out-of-range arguments -> DENY_ARG before any other rule)");
    struct pol_state s = fresh(0, 0);                                           /* no caps, no telemetry */
    struct pol_decision d = step(&s, ev_call(T, SYS_CELL_FAN, -1, 0, 0, 0));
    CHECK(d.code == DEC_DENY_ARG && d.result == -EINVAL);                       /* not DENY_CAP, not STALE */
    d = step(&s, ev_call(T, SYS_CELL_SERVO, -5, 0, 0, 0));
    CHECK(d.code == DEC_DENY_ARG);
    d = step(&s, ev_call(T, SYS_OBJ_READ, -1, 0, 8, 0));
    CHECK(d.code == DEC_DENY_ARG);                                              /* before DENY_FAULT */
    d = step(&s, ev_call(T, SYS_OBJ_READ, OBJ_COUNT, 0x40001000, 8, EVF_UACCESS_OK));
    CHECK(d.code == DEC_DENY_ARG);
    d = step(&s, ev_call(T, SYS_OBJ_WRITE, OBJ_SCRATCH_0, 0x40001000, (int32_t)OBJ_DATA_MAX + 1, EVF_UACCESS_OK));
    CHECK(d.code == DEC_DENY_ARG);
    d = step(&s, ev_call(T, SYS_OBJ_WRITE, OBJ_SCRATCH_0, 0x40001000, -1, 0));
    CHECK(d.code == DEC_DENY_ARG);
    d = step(&s, ev_call(T, SYS_CELL_LCD, 2, 0x40001000, 0, EVF_UACCESS_OK));
    CHECK(d.code == DEC_DENY_ARG);
    d = step(&s, ev_call(T, SYS_CELL_LCD, -1, 0x40001000, 0, EVF_UACCESS_OK));
    CHECK(d.code == DEC_DENY_ARG);
    d = step(&s, ev_call(T, SYS_AGENT_RECV, 0x40001000, 8, -1, EVF_UACCESS_OK));
    CHECK(d.code == DEC_DENY_ARG);
    ok();
}

static void t_R6(void)
{
    begin("R6_strings (an unterminated or unprintable LCD text is DENY_ARG, an unmapped one DENY_FAULT)");
    struct pol_state s = fresh(CAP_ALL, 1);
    struct pol_decision d = step(&s, ev_call(T, SYS_CELL_LCD, 0, 0x40001000, 0, EVF_UACCESS_OK | EVF_STR_BAD));
    CHECK(d.code == DEC_DENY_ARG && d.result == -EINVAL);
    d = step(&s, ev_call(T, SYS_CELL_LCD, 0, 0x40001000, 0, 0));
    CHECK(d.code == DEC_DENY_FAULT && d.result == -EFAULT);
    d = step(&s, ev_call(T, SYS_CELL_LCD, 1, 0x40001000, 0, EVF_UACCESS_OK));
    CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC);
    ok();
}

static void t_R7(void)
{
    begin("R7_request_boundaries (EXEC_START resets per-request state; seq, latch, telemetry persist)");
    struct pol_state s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    step(&s, ev_sys(T, SYS_OBJ_READ, OBJ_MAINT_NOTES, 0x40001000, 8));
    uint32_t seq = s.seq;
    CHECK(s.tainted && s.fan_confirmed && s.in_request && seq > 0);
    struct pol_decision d = step(&s, ev_end(T + 1, END_FAULT, 14));
    CHECK(d.code == DEC_PROC_FAULT && d.target == 14 && d.audit && !s.in_request && s.caps == 0);
    d = step(&s, ev_end(T + 2, END_EXIT, 0));
    CHECK(d.audit == 0);                                                        /* nothing running: silent */
    d = step(&s, ev_sys(T + 2, SYS_UPTIME, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_NO_REQUEST);
    d = step(&s, ev_start(T + 3, PROG_TEST_CONSOLE, CAP_CONSOLE, 500, 6000, 3000));
    CHECK(d.code == DEC_ALLOW && d.reason == R_EXEC_START && d.target == PROG_TEST_CONSOLE && d.audit);
    CHECK(s.in_request && !s.tainted && !s.fan_confirmed && !s.pending);
    CHECK(s.caps == (policy_is_permissive() ? CAP_ALL : CAP_CONSOLE) && s.req_start == T + 3 && s.act_start == T + 3);
    CHECK(s.req_limit == 6000 && s.act_limit == 500 && s.confirm_limit == 3000);
    CHECK(s.seq == seq + 3 && s.tele_valid && s.fan_duty == 60 && s.servo_deg == 90);  /* kept */
    d = step(&s, ev_end(T + 4, END_EXIT, 3));
    CHECK(d.code == DEC_ALLOW && d.reason == R_EXEC_END && d.result == 3);
    /* a pending prompt dies with the request */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_CELL_FAN, 60, 0, 0));
    step(&s, ev_end(T + 1, END_LIMIT, 6000));
    CHECK(!s.pending);
    ok();
}

static void t_R8(void)
{
    begin("R8_silent_decisions (routine ticks and telemetry silent; AUTO_HALT once per hazard)");
    struct pol_state s = fresh(CAP_ALL, 1);
    struct pol_decision d = step(&s, ev_tele(T + 1, 310, 0, 1));
    CHECK(d.audit == 0 && d.code == DEC_NONE && s.distance_mm == 310);       /* state changes, no audit */
    d = step(&s, ev_tick(T + 2));
    CHECK(d.audit == 0);
    fan(&s, T + 2, 60);
    d = step(&s, ev_tele(T + 3, 50, 0, 1));
    CHECK(d.code == DEC_AUTO_HALT && d.audit);
    int audited = 0;
    for (uint32_t t = T + 4; t < T + 100; t++) {
        audited += step(&s, ev_tick(t)).audit;
        audited += step(&s, ev_tele(t, 50, 0, 1)).audit;
    }
    CHECK(audited == 0);                                                      /* once per hazard */
    /* hazard clears, motion resumes, hazard again: a second AUTO_HALT */
    step(&s, ev_tele(T + 200, 300, 0, 1));
    fan(&s, T + 200, 60);
    d = step(&s, ev_tele(T + 201, 50, 0, 1));
    CHECK(d.code == DEC_AUTO_HALT);
    /* uptime and exit are not audited */
    d = step(&s, ev_sys(T + 202, SYS_UPTIME, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.audit == 0);
    d = step(&s, ev_sys(T + 202, SYS_EXIT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.audit == 0);
    ok();
}

static void t_names(void)
{
    begin("names (every code, reason and syscall has a name; no banned words)");
    const char *banned[] = { "success", "secure", "safe", "real-time" };
    for (uint8_t c = 0; c < DEC_COUNT; c++)
        CHECK(strcmp(policy_code_name(c), "?") != 0);
    for (uint8_t r = 0; r < R_COUNT; r++)
        CHECK(strcmp(policy_reason_name(r), "?") != 0);
    for (uint32_t n = 0; n < SYS_COUNT; n++)
        CHECK(strcmp(policy_syscall_name(n), "unknown") != 0);
    CHECK(strcmp(policy_code_name(DEC_COUNT), "?") == 0 && strcmp(policy_syscall_name(99), "unknown") == 0);
    for (unsigned b = 0; b < 4; b++) {
        for (uint8_t c = 0; c < DEC_COUNT; c++)
            CHECK(strstr(policy_code_name(c), banned[b]) == 0);
        for (uint8_t r = 0; r < R_COUNT; r++)
            CHECK(strstr(policy_reason_name(r), banned[b]) == 0);
    }
    CHECK(strlen(policy_variant_name()) > 0);
    ok();
}

/* ---- T-02F (D-50, D-52) ---- */
static void t_F3(void)
{
    begin("F3_energy_reducing (D-50/D-52: halt and fan(0) need nothing and preserve a pending prompt; a non-zero "
          "decrease keeps every gate but the ramp rule and the taint prompt)");
    struct pol_state s, before;
    struct pol_decision d;
    const int32_t P = 0x40001000;
    if (!policy_is_permissive()) {
        /* with a pending confirmation: allowed, duty 0, the prompt untouched and still answerable */
        for (int call = 0; call < 2; call++) {
            s = fresh(CAP_ALL, 1);
            fan(&s, T, 60);
            step(&s, ev_tele(T + 200, 300, 0, 1));
            d = step(&s, ev_sys(T + 200, SYS_OBJ_WRITE, OBJ_MAINT_NOTES, P, 4));
            CHECK(d.code == DEC_CONFIRM_PENDING && s.pending && s.fan_duty == 60);
            before = s;
            d = step(&s, call ? ev_sys(T + 201, SYS_CELL_FAN, 0, 0, 0) : ev_sys(T + 201, SYS_CELL_HALT, 0, 0, 0));
            CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && d.audit && s.fan_duty == 0);
            CHECK(s.pending && s.pend_nr == SYS_OBJ_WRITE && s.pend_since == before.pend_since);
            struct pol_state cmp = s;                 /* nothing but the fan fields and seq changed */
            cmp.fan_duty = before.fan_duty;
            cmp.fan_change_tick = before.fan_change_tick;
            cmp.fan_changed = before.fan_changed;
            cmp.seq = before.seq;
            CHECK(memcmp(&cmp, &before, sizeof cmp) == 0);
            d = step(&s, ev_answer(T + 202, 1));
            CHECK(d.code == DEC_CONFIRM_YES && !s.pending);
            /* a non-zero decrease while pending keeps the gate */
            s = before;
            d = step(&s, ev_sys(T + 201, SYS_CELL_FAN, 50, 0, 0));
            CHECK(d.code == DEC_DENY_RULE && d.reason == R_PENDING && s.fan_duty == 60);
        }
    }
    /* after LIMIT (killing): halt and fan(0) still go through, a decrease and everything else do not */
    s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    d = step(&s, ev_tick(T + 500));
    CHECK(d.code == DEC_LIMIT && s.killing && s.fan_duty == 60);
    d = step(&s, ev_sys(T + 500, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.reason == R_HALT && d.action == ACT_EXEC && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T + 500, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T + 500, SYS_CELL_FAN, 50, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_NO_REQUEST && s.fan_duty == 60);
    d = step(&s, ev_sys(T + 500, SYS_UPTIME, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_NO_REQUEST);
    /* outside a request: nothing, not even halt (the first check) */
    policy_state_init(&s);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_DENY_RULE && d.reason == R_NO_REQUEST && s.fan_duty == 60);
    /* under E-stop (latched) */
    s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    d = step(&s, ev_tele(T + 1, 300, 1, 1));
    CHECK(d.code == DEC_AUTO_HALT && s.estop_latched && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T + 1, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T + 1, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T + 1, SYS_CELL_FAN, 50, 0, 0));
    CHECK(d.code == DEC_DENY_ESTOP && d.reason == R_ESTOP && s.fan_duty == 60);
    /* tainted: no prompt for halt / fan(0) */
    s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    step(&s, ev_sys(T, SYS_OBJ_READ, OBJ_MAINT_NOTES, P, 8));
    CHECK(s.tainted);
    d = step(&s, ev_sys(T + 1, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T + 1, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && s.fan_duty == 0 && s.tainted);
    /* without CAP_DEV_MOVE (and with no capability at all, no telemetry) */
    s = fresh(CAP_ALL & ~CAP_DEV_MOVE, 1);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T, SYS_CELL_HALT, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 50, 0, 0));
    CHECK(d.code == (policy_is_permissive() ? DEC_ALLOW : DEC_DENY_CAP));
    s = fresh(0, 0);
    s.fan_duty = 60;
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 0 && s.fan_change_tick == T && s.fan_changed);
    d = step(&s, ev_sys(T, SYS_CELL_FAN, 0, 0, 0));
    CHECK(d.code == DEC_ALLOW && s.fan_change_tick == T);                 /* already 0: no new change */
    /* a tainted 80 -> 60 decrease: allowed without a prompt, in the same tick as the last change */
    s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    step(&s, ev_tele(T + 100, 300, 0, 1));
    d = fan(&s, T + 100, 80);
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 80);
    step(&s, ev_sys(T + 100, SYS_OBJ_READ, OBJ_MAINT_NOTES, P, 8));
    CHECK(s.tainted);
    d = step(&s, ev_sys(T + 100, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_ALLOW && d.action == ACT_EXEC && d.reason == R_NONE && s.fan_duty == 60 && !s.pending);
    step(&s, ev_tele(T + 300, 300, 0, 1));
    d = step(&s, ev_sys(T + 300, SYS_CELL_FAN, 70, 0, 0));                 /* an increase still prompts */
    CHECK(d.code == (policy_is_permissive() ? DEC_ALLOW : DEC_CONFIRM_PENDING) &&
          (policy_is_permissive() || d.reason == R_TAINT));
    if (!policy_is_permissive())
        step(&s, ev_answer(T + 301, 0));
    /* a decrease to 60 with a hand present, with stale data; to 40 */
    s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    step(&s, ev_tele(T + 100, 300, 0, 1));
    fan(&s, T + 100, 80);
    s.distance_mm = 50;
    d = step(&s, ev_sys(T + 100, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_PRESENCE && d.result == -ESAFETY && s.fan_duty == 80);
    s.distance_mm = 0xFFFF;
    d = step(&s, ev_sys(T + 100, SYS_CELL_FAN, 60, 0, 0));
    CHECK(d.code == DEC_DENY_INTERLOCK && d.reason == R_NO_ECHO && s.fan_duty == 80);
    s.distance_mm = 300;
    d = step(&s, ev_sys(T + 200, SYS_CELL_FAN, 60, 0, 0));                 /* telemetry 1 s old */
    CHECK(d.code == DEC_DENY_STALE && d.reason == R_STALE && s.fan_duty == 80);
    step(&s, ev_tele(T + 200, 300, 0, 1));
    d = step(&s, ev_sys(T + 200, SYS_CELL_FAN, 40, 0, 0));
    CHECK(d.code == DEC_DENY_ARG && d.reason == R_ARG && d.result == -EINVAL && s.fan_duty == 80);
    d = step(&s, ev_sys(T + 200, SYS_CELL_FAN, 60, 0, 0));                 /* and now it goes through */
    CHECK(d.code == DEC_ALLOW && s.fan_duty == 60);
    /* a tainted servo move still prompts */
    s = fresh(CAP_ALL, 1);
    step(&s, ev_sys(T, SYS_OBJ_READ, OBJ_MAINT_NOTES, P, 8));
    d = step(&s, ev_sys(T, SYS_CELL_SERVO, 100, 0, 0));
    CHECK(d.code == (policy_is_permissive() ? DEC_ALLOW : DEC_CONFIRM_PENDING) &&
          (policy_is_permissive() || d.reason == R_TAINT));
    ok();
}

static void t_F7(void)
{
    begin("F7_autohalt_target (a telemetry-path AUTO_HALT records the duty that was running)");
    struct pol_state s = fresh(CAP_ALL, 1);
    fan(&s, T, 60);
    struct pol_decision d = step(&s, ev_tele(T + 10, 80, 0, 1));          /* a hand */
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_PRESENCE && d.target == 60 && s.fan_duty == 0);
    step(&s, ev_tele(T + 200, 300, 0, 1));
    fan(&s, T + 200, 60);
    d = step(&s, ev_tele(T + 210, 0xFFFF, 0, 1));                        /* no echo */
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_NO_ECHO && d.target == 60 && s.fan_duty == 0);
    step(&s, ev_tele(T + 400, 300, 0, 1));
    fan(&s, T + 400, 60);
    d = step(&s, ev_tele(T + 410, 300, 1, 1));                           /* E-stop pressed */
    CHECK(d.code == DEC_AUTO_HALT && d.reason == R_ESTOP && d.target == 60 && s.fan_duty == 0);
    ok();
}

static void t_F8(void)
{
    begin("F8_quota_arguments (EXEC_START with a zero or negative quota is refused: no '0 means unlimited')");
    const int32_t bad[] = { 0, -1, -500, (int32_t)0x80000000 };
    struct pol_state init, s;
    policy_state_init(&init);
    for (int field = 0; field < 3; field++) {
        for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
            int32_t q[3] = { 500, 6000, 3000 };
            q[field] = bad[i];
            s = init;
            struct pol_decision d = step(&s, ev_start(T, PROG_TEST_FULL, CAP_ALL, q[0], q[1], q[2]));
            CHECK(d.code == DEC_DENY_RULE && d.reason == R_ARG && d.result == -EINVAL && d.action == ACT_NONE);
            CHECK(!s.in_request && s.caps == 0);
            struct pol_state cmp = s;
            cmp.seq = init.seq;
            CHECK(memcmp(&cmp, &init, sizeof cmp) == 0);                 /* a deny changes no state */
        }
    }
    /* the smallest valid quotas work and fire on the first tick */
    s = init;
    struct pol_decision d = step(&s, ev_start(T, PROG_TEST_FULL, CAP_ALL, 1, 1, 1));
    CHECK(d.code == DEC_ALLOW && s.in_request && s.act_limit == 1 && s.req_limit == 1 && s.confirm_limit == 1);
    d = step(&s, ev_tick(T + 1));
    CHECK(d.code == DEC_LIMIT && d.reason == R_REQUEST_QUOTA);
    ok();
}

/* ---- replay: TAPE lines in, AUDIT lines out, same format as kernel/core/audit.c ---- */
static int replay(void)
{
    struct pol_state s;
    policy_state_init(&s);
    char line[512];
    unsigned long n = 0;
    while (fgets(line, sizeof line, stdin)) {
        unsigned k, sub, f, t, nr;
        int a0, a1, a2, a3;
        if (sscanf(line, "TAPE k=%u s=%u f=%u t=%u nr=%u a0=%d a1=%d a2=%d a3=%d", &k, &sub, &f, &t, &nr, &a0,
                   &a1, &a2, &a3) != 9)
            continue;
        struct pol_event e = { (uint8_t)k, (uint8_t)sub, (uint8_t)f, 0, t, nr, { a0, a1, a2, a3 } };
        struct pol_decision d = step(&s, e);
        n++;
        if (!d.audit)
            continue;
        printf("AUDIT seq=%u tick=%u sys=%s target=%d decision=%s reason=%s result=%d tainted=%u%s\n", s.seq, t,
               e.kind == EV_SYSCALL ? policy_syscall_name(nr) : policy_event_name(e.kind), (int)d.target,
               policy_code_name(d.code), policy_reason_name(d.reason), d.result, s.tainted,
               policy_is_permissive() ? " PERMISSIVE (EVAL ONLY)" : "");
    }
    fprintf(stderr, "replayed %lu events\n", n);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--replay") == 0)
        return replay();
    printf("policy_test: variant %s, state %u bytes, event %u bytes\n", policy_variant_name(),
           (unsigned)sizeof(struct pol_state), (unsigned)sizeof(struct pol_event));
    t_object_table();
    t_syscall_caps();
    t_default_deny();
    t_no_escalation();
    t_taint();
    t_halt_always();
    t_fan_rules();
    t_servo_rules();
    t_interlock();
    t_stale();
    t_estop();
    t_tick_autohalt();
    t_R1();
    t_R2();
    t_R3();
    t_R4();
    t_R5();
    t_R6();
    t_R7();
    t_R8();
    t_F3();
    t_F7();
    t_F8();
    t_names();
    printf("policy_test: %d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
