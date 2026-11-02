/* RING3 policy engine (see policy.h). Pure: no globals, no clock, no kernel headers. Rule order for a syscall
 * (R5, R3, D-45, D-52): inside a request -> energy-reducing calls (cell_halt, cell_fan(0): nothing else is
 * checked) -> killing -> pending -> argument ranges -> uaccess -> capability -> object rule -> E-stop latch ->
 * static device limits -> stale telemetry -> presence -> confirmation -> allow. A deny changes no state. */
#include "policy.h"
#include POLICY_VARIANT_HEADER

#define TICKS_SINCE(now, then) ((uint32_t)((now) - (then)))

static void copy_state(struct pol_state *dst, const struct pol_state *src)
{
    const uint8_t *s = (const uint8_t *)src;
    uint8_t *d = (uint8_t *)dst;
    for (uint32_t i = 0; i < sizeof *dst; i++)
        d[i] = s[i];
}

void policy_state_init(struct pol_state *s)
{
    uint8_t *p = (uint8_t *)s;
    for (uint32_t i = 0; i < sizeof *s; i++)
        p[i] = 0;
    s->servo_deg = SERVO_PARK_DEG;            /* R4: real baselines */
    s->fan_duty = 0;
}

/* ---- tables ---- */
static const struct pol_object_rule objects[OBJ_COUNT] = {
    { "cell_spec",   1, RULE_ALLOW, RULE_DENY },
    { "maint_notes", 0, RULE_ALLOW, RULE_CONFIRM },
    { "scratch_0",   0, RULE_ALLOW, RULE_ALLOW },
    { "scratch_1",   0, RULE_ALLOW, RULE_ALLOW },
    { "scratch_2",   0, RULE_ALLOW, RULE_ALLOW },
    { "scratch_3",   0, RULE_ALLOW, RULE_ALLOW },
    { "audit_log",   1, RULE_ALLOW, RULE_DENY },
};

const struct pol_object_rule *policy_object(uint32_t id)
{
    return id < OBJ_COUNT ? &objects[id] : 0;
}

uint32_t policy_caps_for_prog(uint8_t prog)
{
#if POLICY_PERMISSIVE
    (void)prog;
    return CAP_ALL;
#else
    switch (prog) {
    case PROG_AGENTSH:      return CAP_ALL;
    case PROG_TEST_CONSOLE: return CAP_CONSOLE;
    case PROG_TEST_NOCAP:   return 0;
    case PROG_TEST_FULL:    return CAP_ALL;
    case PROG_TEST_OBJ:     return CAP_CONSOLE | CAP_OBJ_READ;
    default:                return 0;            /* unknown program: nothing */
    }
#endif
}

static uint32_t cap_for_syscall(uint32_t nr)
{
    switch (nr) {
    case SYS_CONS_WRITE:  return CAP_CONSOLE;
    case SYS_OBJ_LIST:
    case SYS_OBJ_READ:
    case SYS_OBJ_WRITE:   return CAP_OBJ_READ;
    case SYS_CELL_STATUS: return CAP_DEV_READ;
    case SYS_CELL_FAN:
    case SYS_CELL_SERVO:  return CAP_DEV_MOVE;
    case SYS_CELL_LCD:    return CAP_DEV_HMI;
    case SYS_AGENT_SEND:
    case SYS_AGENT_RECV:  return CAP_AGENT_LINK;
    default:              return 0;               /* exit, cell_halt, uptime */
    }
}

static int has_pointer(uint32_t nr)
{
    switch (nr) {
    case SYS_CONS_WRITE: case SYS_OBJ_LIST: case SYS_OBJ_READ: case SYS_OBJ_WRITE: case SYS_CELL_STATUS:
    case SYS_CELL_LCD: case SYS_AGENT_SEND: case SYS_AGENT_RECV:
        return 1;
    default:
        return 0;
    }
}

static void set(struct pol_decision *d, uint8_t code, uint8_t reason, int32_t result, uint8_t action)
{
    d->code = code;
    d->reason = reason;
    d->result = result;
    d->action = action;
    d->audit = 1;
}

/* ---- hazards (shared by the interlock rule and the per-tick lease) ---- */
/* Returns R_NONE when motion is allowed by the telemetry, else the reason. */
static uint8_t hazard(const struct pol_state *s, uint32_t tick)
{
    if (s->estop_latched)
        return R_ESTOP;
    if (!s->tele_valid)
        return R_NO_TELEMETRY;
    if (TICKS_SINCE(tick, s->tele_tick) > STALE_TICKS)
        return R_STALE;
    if (s->distance_mm == DIST_NO_ECHO)
        return R_NO_ECHO;
    if (s->distance_mm < PRESENCE_MM)
        return R_PRESENCE;
    return R_NONE;
}

static uint8_t code_for_hazard(uint8_t reason)
{
    switch (reason) {
    case R_ESTOP:        return DEC_DENY_ESTOP;
    case R_NO_TELEMETRY:
    case R_STALE:        return DEC_DENY_STALE;
    default:             return DEC_DENY_INTERLOCK;
    }
}

/* Tick-driven lease (D-30, INV-16): a hazard while the fan is commanded on -> AUTO_HALT, once per hazard (R8),
 * because the commanded duty drops to 0 in `next`. The first-fan confirmation is withdrawn too (D-53, T-04):
 * after a hazard stop any duty > 0 is a step from 0 that asks the human again. */
static void lease(const struct pol_state *s, uint32_t tick, struct pol_state *n, struct pol_decision *d)
{
    uint8_t duty = s->fan_duty;               /* F7: `s` may alias `n` (the telemetry path evaluates `next`) */
    if (duty == 0)
        return;
    uint8_t h = hazard(s, tick);
    if (h == R_NONE)
        return;
    n->fan_duty = 0;
    n->fan_change_tick = tick;
    n->fan_changed = 1;
    n->fan_confirmed = 0;
    set(d, DEC_AUTO_HALT, h, 0, ACT_HALT);
    d->target = duty;                         /* the duty that was running */
}

/* R10: the console E-stop reset goes through only on valid, fresh telemetry that shows the button released.
 * Called at the answer's tick (T-04: the reset is confirmed first, like any destructive action, INV-05). */
static void estop_reset_rules(const struct pol_state *s, uint32_t tick, struct pol_state *n,
                              struct pol_decision *d)
{
    if (!s->tele_valid) {
        set(d, DEC_DENY_ESTOP, R_NO_TELEMETRY, -ESAFETY, ACT_NONE);
        return;
    }
    if (TICKS_SINCE(tick, s->tele_tick) > STALE_TICKS) {
        set(d, DEC_DENY_ESTOP, R_STALE, -ESAFETY, ACT_NONE);
        return;
    }
    if (s->estop_pressed) {
        set(d, DEC_DENY_ESTOP, R_ESTOP_BUTTON, -ESAFETY, ACT_NONE);
        return;
    }
    n->estop_latched = 0;
    set(d, DEC_CONFIRM_YES, R_ESTOP_RESET, 0, ACT_EXEC);   /* ACT_EXEC: the kernel sends ESTOP_RESET on COM3 */
}

/* A sequenced telemetry sample against the current boot baseline (T-04, D-53, D-54). */
enum { TELE_BOOT = 0, TELE_FORWARD = 1, TELE_OLD = 2 };
static int classify_frame(const struct pol_state *s, uint16_t seq, uint32_t tms)
{
    if (s->tele_run == 0)
        return TELE_BOOT;
    uint16_t ahead = (uint16_t)(seq - s->tele_seq);
    uint16_t behind = (uint16_t)(s->tele_seq - seq);
    if (ahead >= 1 && ahead <= TELE_GAP_MAX && tms >= s->tele_tms)
        return TELE_FORWARD;
    if (behind <= TELE_OLD_WINDOW && tms <= s->tele_tms && s->tele_tms - tms <= TELE_OLD_MS &&
        s->tele_old_run < TELE_OLD_MAX)
        return TELE_OLD;
    return TELE_BOOT;                         /* seq or t_ms went backwards: the board rebooted */
}

static void resolve_pending(const struct pol_state *s, uint32_t tick, struct pol_state *n)
{
    n->pending = 0;
    n->pend_nr = 0;
    n->pend_a[0] = n->pend_a[1] = n->pend_a[2] = n->pend_a[3] = 0;
    n->pend_since = 0;
    n->act_start = s->act_start + TICKS_SINCE(tick, s->pend_since);   /* R2: the action quota was paused */
}

/* ---- one syscall, all rules (R1: re-run in full when the answer arrives) ---- */
static void eval_call(const struct pol_state *s, uint32_t tick, uint32_t nr, const int32_t a[4], uint8_t flags,
                      int answered, struct pol_state *n, struct pol_decision *d)
{
    d->target = (uint32_t)a[0];
    if (!s->in_request) {
        set(d, DEC_DENY_RULE, R_NO_REQUEST, -EPERM, ACT_NONE);
        return;
    }
    /* Energy-reducing calls come first (INV-16, D-50, D-52): cell_halt and cell_fan(0) need no capability and
     * no telemetry, ignore taint and the E-stop latch, never prompt, and are allowed even while a LIMIT is
     * being carried out or a confirmation is pending (the prompt stays as it is). Only the commanded duty
     * changes. */
    if (nr == SYS_CELL_HALT || (nr == SYS_CELL_FAN && a[0] == 0)) {
        n->fan_duty = 0;
        if (s->fan_duty != 0) {
            n->fan_change_tick = tick;
            n->fan_changed = 1;
        }
        set(d, DEC_ALLOW, nr == SYS_CELL_HALT ? R_HALT : R_NONE, 0, ACT_EXEC);
        return;
    }
    if (s->killing) {
        set(d, DEC_DENY_RULE, R_NO_REQUEST, -EPERM, ACT_NONE);
        return;
    }
    if (s->pending && !answered) {
        set(d, DEC_DENY_RULE, R_PENDING, -EPERM, ACT_NONE);
        return;
    }
    if (nr >= SYS_COUNT) {
        set(d, DEC_DENY_RULE, R_NOSYS, -ENOSYS, ACT_NONE);
        return;
    }
    /* R5: argument ranges first (negative = out of range). */
    int arg_bad = 0;
    switch (nr) {
    case SYS_OBJ_READ:
    case SYS_OBJ_WRITE:
        if (a[0] < 0 || a[0] >= (int32_t)OBJ_COUNT)
            arg_bad = 1;
        if (nr == SYS_OBJ_WRITE && (uint32_t)a[2] > OBJ_DATA_MAX)
            arg_bad = 1;
        break;
    case SYS_CELL_FAN:
        if (a[0] < 0 || a[0] > 100 || (a[0] > 0 && a[0] < FAN_MIN_SPIN))   /* D-47: 1..49 would stall */
            arg_bad = 1;
        break;
    case SYS_CELL_SERVO:
        if (a[0] < 0 || a[0] > 180)
            arg_bad = 1;
        break;
    case SYS_CELL_LCD:
        if (a[0] < 0 || a[0] > 1 || (flags & EVF_STR_BAD))
            arg_bad = 1;
        break;
    case SYS_AGENT_RECV:
        if (a[2] < 0)
            arg_bad = 1;
        break;
    default:
        break;
    }
    if (arg_bad) {
        set(d, DEC_DENY_ARG, R_ARG, -EINVAL, ACT_NONE);
        return;
    }
    /* R3: uaccess ran first in the kernel; a bad pointer denies with no state change. */
    if (has_pointer(nr) && !(flags & EVF_UACCESS_OK)) {
        set(d, DEC_DENY_FAULT, R_UACCESS, -EFAULT, ACT_NONE);
        return;
    }
    if (nr == SYS_EXIT || nr == SYS_UPTIME) {
        set(d, DEC_ALLOW, R_NONE, 0, ACT_EXEC);
        d->audit = 0;                              /* no authority involved; EXEC_END covers exit */
        return;
    }
    /* Capability (default deny, INV-04). */
    uint32_t need = cap_for_syscall(nr);
    if ((s->caps & need) != need) {
        set(d, DEC_DENY_CAP, R_CAP, -EPERM, ACT_NONE);
        return;
    }
    /* Object rules. */
    const struct pol_object_rule *obj = 0;
    int need_confirm = 0;
    if (nr == SYS_OBJ_READ || nr == SYS_OBJ_WRITE) {
        obj = &objects[a[0]];
        uint8_t rule = nr == SYS_OBJ_READ ? obj->read : obj->write;
#if POLICY_PERMISSIVE
        rule = RULE_ALLOW;
#endif
        if (rule == RULE_DENY) {
            set(d, DEC_DENY_RULE, R_OBJ_RULE, -EACCES, ACT_NONE);
            return;
        }
        if (rule == RULE_CONFIRM)
            need_confirm = R_OBJ_CONFIRM;
    }
    /* Device rules (ARCHITECTURE §3a, D-47, D-52). Every non-zero fan command and every servo move is
     * `motion`: the E-stop latch and the interlock (stale, presence, no echo) apply to all of them. Only a
     * fan increase or a servo move adds energy: the ramp rules (step, rate) and the taint prompt apply to
     * those alone; a decrease to a non-zero duty never prompts (D-50, INV-16). fan(0) was handled above. */
    int fan_on = nr == SYS_CELL_FAN;
    int fan_up = fan_on && a[0] > (int)s->fan_duty;
    int motion = fan_on || nr == SYS_CELL_SERVO;
    int adds_energy = fan_up || nr == SYS_CELL_SERVO;
    if (motion && s->estop_latched) {                                   /* the latch outranks every limit */
        set(d, DEC_DENY_ESTOP, R_ESTOP, -ESAFETY, ACT_NONE);
        return;
    }
    if (fan_on && a[0] > FAN_MAX_DUTY) {
        set(d, DEC_DENY_LIMIT, R_FAN_RANGE, -ESAFETY, ACT_NONE);
        return;
    }
    if (fan_up) {
        int duty = a[0];
        int step_ok = s->fan_duty == 0 ? duty <= FAN_FIRST_STEP_MAX : duty - s->fan_duty <= FAN_STEP_MAX;
        if (!step_ok) {
            set(d, DEC_DENY_LIMIT, R_FAN_STEP, -ESAFETY, ACT_NONE);
            return;
        }
        if (s->fan_changed && TICKS_SINCE(tick, s->fan_change_tick) < FAN_MIN_GAP_TICKS) {
            set(d, DEC_DENY_LIMIT, R_FAN_RATE, -ESAFETY, ACT_NONE);
            return;
        }
    }
    if (nr == SYS_CELL_SERVO) {
        int deg = a[0];
        if (deg < SERVO_MIN_DEG || deg > SERVO_MAX_DEG) {
            set(d, DEC_DENY_LIMIT, R_SERVO_RANGE, -ESAFETY, ACT_NONE);
            return;
        }
        int delta = deg - (int)s->servo_deg;
        if (delta < 0)
            delta = -delta;
        if (delta > SERVO_STEP_MAX_DEG) {
            set(d, DEC_DENY_LIMIT, R_SERVO_STEP, -ESAFETY, ACT_NONE);
            return;
        }
        if (s->servo_moved && TICKS_SINCE(tick, s->servo_tick) < SERVO_MIN_GAP_TICKS) {
            set(d, DEC_DENY_LIMIT, R_SERVO_RATE, -ESAFETY, ACT_NONE);
            return;
        }
    }
    if (motion) {
        uint8_t h = hazard(s, tick);
        if (h != R_NONE) {
            set(d, code_for_hazard(h), h, -ESAFETY, ACT_NONE);
            return;
        }
    }
    if (fan_up && !s->fan_confirmed && !need_confirm)
        need_confirm = R_FIRST_FAN;
    /* Taint (D-27, INV-15, D-50): every write and every energy-adding motion needs a human answer while
     * tainted; a fan decrease does not. */
    if (s->tainted && (nr == SYS_OBJ_WRITE || adds_energy))
        need_confirm = R_TAINT;
    uint8_t allow_reason = R_NONE;
    if (need_confirm && !answered) {
#if POLICY_PERMISSIVE
        allow_reason = R_PERMISSIVE;
#else
        n->pending = 1;
        n->pend_nr = nr;
        n->pend_a[0] = a[0];
        n->pend_a[1] = a[1];
        n->pend_a[2] = a[2];
        n->pend_a[3] = a[3];
        n->pend_since = tick;
        set(d, DEC_CONFIRM_PENDING, (uint8_t)need_confirm, 0, ACT_PROMPT);
        return;
#endif
    }
    /* ALLOW: apply the state changes of the call. */
    if (nr == SYS_OBJ_READ && !obj->trusted)
        n->tainted = 1;
    if (nr == SYS_AGENT_RECV)
        n->waiting = 1;                            /* D-58: the wait for the bridge pauses the action quota */
    if (nr == SYS_CELL_FAN) {
        if (a[0] != s->fan_duty) {
            n->fan_change_tick = tick;
            n->fan_changed = 1;
        }
        n->fan_duty = (uint8_t)a[0];
        if (a[0] > 0)
            n->fan_confirmed = 1;
    }
    if (nr == SYS_CELL_SERVO) {
        n->servo_deg = (uint8_t)a[0];
        n->servo_tick = tick;
        n->servo_moved = 1;
    }
    set(d, answered ? DEC_CONFIRM_YES : DEC_ALLOW, answered ? (uint8_t)need_confirm : allow_reason, 0, ACT_EXEC);
}

/* ---- the entry point ---- */
void decide(const struct pol_state *s, const struct pol_event *ev, struct pol_state *n, struct pol_decision *d)
{
    copy_state(n, s);
    d->code = DEC_NONE;
    d->reason = R_NONE;
    d->audit = 0;
    d->action = ACT_NONE;
    d->result = 0;
    d->target = 0;
    uint32_t tick = ev->tick;

    switch (ev->kind) {
    case EV_EXEC_START:
        d->target = ev->sub;
        if (s->in_request) {
            set(d, DEC_DENY_RULE, R_PENDING, -EPERM, ACT_NONE);
            return;
        }
        /* F8 (D-50): quotas are positive tick counts; zero or negative is refused, there is no "unlimited". */
        if (ev->a[0] <= 0 || ev->a[1] <= 0 || ev->a[2] <= 0) {
            set(d, DEC_DENY_RULE, R_ARG, -EINVAL, ACT_NONE);
            return;
        }
        n->in_request = 1;
        n->prog = ev->sub;
        n->tainted = 0;
        n->fan_confirmed = 0;
        n->killing = 0;
        n->actions = 0;
        n->waiting = 0;
#if POLICY_PERMISSIVE
        n->caps = CAP_ALL;
#else
        n->caps = ev->nr;
#endif
        n->req_start = tick;
        n->req_limit = (uint32_t)ev->a[1];
        n->act_start = tick;
        n->act_limit = (uint32_t)ev->a[0];
        n->confirm_limit = (uint32_t)ev->a[2];
        n->pending = 0;
        n->pend_nr = 0;
        n->pend_a[0] = n->pend_a[1] = n->pend_a[2] = n->pend_a[3] = 0;
        n->pend_since = 0;
        set(d, DEC_ALLOW, R_EXEC_START, 0, ACT_EXEC);
        return;

    case EV_EXEC_END:
        d->target = (uint32_t)ev->a[0];
        if (!s->in_request)
            return;                                   /* silent: nothing was running */
        n->in_request = 0;
        n->killing = 0;
        n->waiting = 0;
        n->caps = 0;
        n->tainted = 0;
        if (s->pending)
            resolve_pending(s, tick, n);
        if (ev->sub == END_FAULT)
            set(d, DEC_PROC_FAULT, R_EXEC_END, 0, ACT_NONE);
        else if (ev->sub == END_LIMIT)
            set(d, DEC_LIMIT, R_EXEC_END, 0, ACT_NONE);
        else
            set(d, DEC_ALLOW, R_EXEC_END, ev->a[0], ACT_NONE);
        return;

    case EV_SYSCALL:
        eval_call(s, tick, ev->nr, ev->a, ev->flags, 0, n, d);
        return;

    case EV_CONFIRM:
        if (!s->pending) {
            d->reason = R_NO_PENDING;                 /* stray line, no prompt open: silent */
            return;
        }
        d->target = (uint32_t)s->pend_a[0];
        if (ev->sub != ANSWER_YES) {
            resolve_pending(s, tick, n);
            set(d, DEC_CONFIRM_NO, R_ANSWER, -ECANCELED, ACT_NONE);
            return;
        }
        resolve_pending(s, tick, n);
        /* R1: the answer re-runs every rule at the answer's tick. */
        {
            struct pol_state base;
            copy_state(&base, n);                     /* pending already cleared, quota un-paused */
            if (s->pend_nr == PEND_ESTOP_RESET)
                estop_reset_rules(&base, tick, n, d);
            else
                eval_call(&base, tick, s->pend_nr, s->pend_a, EVF_UACCESS_OK, 1, n, d);
        }
        return;

    case EV_CONSOLE:
        if (ev->sub != CONSOLE_ESTOP_RESET)
            return;                                   /* other console lines carry no policy meaning */
        if (!s->estop_latched) {
            set(d, DEC_DENY_RULE, R_NOT_LATCHED, -EINVAL, ACT_NONE);
            return;
        }
        if (s->pending) {
            set(d, DEC_DENY_RULE, R_PENDING, -EPERM, ACT_NONE);
            return;
        }
        if (ev->a[0] <= 0) {                          /* F8: a positive timeout, never "wait forever" */
            set(d, DEC_DENY_RULE, R_ARG, -EINVAL, ACT_NONE);
            return;
        }
        /* T-04: a human "y" at the console is required (INV-05); the R10 rules run at the answer's tick. */
        n->pending = 1;
        n->pend_nr = PEND_ESTOP_RESET;
        n->pend_a[0] = n->pend_a[1] = n->pend_a[2] = n->pend_a[3] = 0;
        n->pend_since = tick;
        n->confirm_limit = (uint32_t)ev->a[0];
        set(d, DEC_CONFIRM_PENDING, R_ESTOP_RESET, 0, ACT_PROMPT);
        return;

    case EV_TICK:
        if (s->pending && TICKS_SINCE(tick, s->pend_since) >= s->confirm_limit) {
            d->target = (uint32_t)s->pend_a[0];
            resolve_pending(s, tick, n);
            set(d, DEC_CONFIRM_NO, R_TIMEOUT, -ECANCELED, ACT_NONE);
            return;
        }
        if (s->in_request && !s->killing) {
            if (TICKS_SINCE(tick, s->req_start) >= s->req_limit) {
                n->killing = 1;
                set(d, DEC_LIMIT, R_REQUEST_QUOTA, 0, ACT_KILL);
                d->target = TICKS_SINCE(tick, s->req_start);
                return;
            }
            if (!s->pending && !s->waiting && TICKS_SINCE(tick, s->act_start) >= s->act_limit) {
                n->killing = 1;
                set(d, DEC_LIMIT, R_ACTION_QUOTA, 0, ACT_KILL);
                d->target = TICKS_SINCE(tick, s->act_start);
                return;
            }
        }
        lease(s, tick, n, d);
        return;

    case EV_TELEMETRY:
        if (!(ev->flags & EVF_TELE_VALID)) {
            set(d, DEC_FRAME_DROP, R_BAD_FRAME, 0, ACT_NONE);
            d->target = ev->sub;                      /* the decoder's error code */
            return;
        }
        if (ev->flags & EVF_TELE_SEQ) {
            uint16_t seq = (uint16_t)ev->a[2];
            uint32_t tms = (uint32_t)ev->a[3];
            int cls = classify_frame(s, seq, tms);
            if (cls == TELE_OLD) {
                d->reason = R_OLD_FRAME;              /* delivered again (D-54): no decision, only counted */
                n->tele_old_run = (uint8_t)(s->tele_old_run + 1);
                return;
            }
            n->tele_old_run = 0;
            n->tele_seq = seq;
            n->tele_tms = tms;
            n->tele_tick = tick;
            n->distance_mm = (uint16_t)ev->a[0];
            n->estop_pressed = (ev->a[1] & 1) != 0;
            if (cls == TELE_BOOT) {
                /* The board (re)started (D-53): the baseline moves to this frame, telemetry is stale until
                 * TELE_SYNC_FRAMES monotonic frames, the commanded duty is 0 (the firmware boots with the fan
                 * off and the servo parked) and the first-fan confirmation is withdrawn. The kernel sends
                 * FAN 0 and nothing else; the previous motion is never re-commanded. Audited as BOARD_BOOT,
                 * not as a fault: the port open resets the board every time. */
                n->tele_run = 1;
                n->tele_valid = 0;
                d->target = s->fan_duty;
                n->fan_duty = 0;
                if (s->fan_duty) {
                    n->fan_change_tick = tick;
                    n->fan_changed = 1;
                }
                n->fan_confirmed = 0;
                n->servo_deg = SERVO_PARK_DEG;
                if (n->estop_pressed)
                    n->estop_latched = 1;             /* a button held through the boot latches too */
                set(d, DEC_BOARD_BOOT, R_BOARD_BOOT, 0, ACT_HALT);
                return;
            }
            if (s->tele_run < TELE_SYNC_FRAMES)
                n->tele_run = (uint8_t)(s->tele_run + 1);
            n->tele_valid = n->tele_run >= TELE_SYNC_FRAMES;
        } else {
            n->tele_valid = 1;                        /* an unsequenced sample (test injection) */
            n->tele_tick = tick;
            n->distance_mm = (uint16_t)ev->a[0];
            n->estop_pressed = (ev->a[1] & 1) != 0;
        }
        if (n->estop_pressed && !s->estop_latched) {
            n->estop_latched = 1;                     /* latched until a human resets it (INV-13) */
            d->target = s->fan_duty;
            n->fan_duty = 0;
            if (s->fan_duty) {
                n->fan_change_tick = tick;
                n->fan_changed = 1;
            }
            n->fan_confirmed = 0;
            set(d, DEC_AUTO_HALT, R_ESTOP, 0, ACT_HALT);
            return;
        }
        lease(n, tick, n, d);                         /* evaluate against the new sample */
        return;

    case EV_ACTION_BOUNDARY:
        /* agent_recv returned (or a test program marked a boundary): the executor runs again and the action
         * quota restarts (R2). With `waiting` pausing the quota meanwhile, it counts ring-3 ticks only (D-58). */
        if (!s->in_request)
            return;
        n->waiting = 0;
        if (!s->killing && !s->pending)
            n->act_start = tick;                      /* silent; the state change puts it on the tape */
        return;

    case EV_ACTION:
        /* D-53: a valid ACTION frame accepted by the link, whatever the executor does with it. Counted per
         * request; the (ACTIONS_PER_REQUEST + 1)th ends the request with LIMIT in ring 0. Outside a request
         * (or after a LIMIT) it changes nothing: the link flushes it at the next boundary. */
        d->target = (uint32_t)ev->a[0];
        if (!s->in_request || s->killing)
            return;
        n->actions = (uint8_t)(s->actions + 1);
        d->target = n->actions;
        if (n->actions > ACTIONS_PER_REQUEST) {
            n->killing = 1;
            set(d, DEC_LIMIT, R_ACTION_CAP, 0, ACT_KILL);
            return;
        }
        if (!s->pending)
            n->act_start = tick;                      /* a new action: the quota restarts (silent, on the tape) */
        return;

    case EV_LINK_DROP:
        /* INV-02: a malformed, oversize, unqueueable or flushed COM2 frame. Audited, no state change. */
        set(d, DEC_FRAME_DROP, ev->sub == LINKDROP_FLUSHED ? R_FLUSHED : R_BAD_FRAME, 0, ACT_NONE);
        d->target = ev->sub;
        return;

    default:
        return;
    }
}

/* ---- names ---- */
static const char *const code_names[DEC_COUNT] = {
    "NONE", "ALLOW", "DENY_CAP", "DENY_RULE", "DENY_ARG", "DENY_FAULT", "DENY_LIMIT", "DENY_INTERLOCK",
    "DENY_STALE", "DENY_ESTOP", "CONFIRM_PENDING", "CONFIRM_YES", "CONFIRM_NO", "FRAME_DROP", "PROC_FAULT",
    "LIMIT", "AUTO_HALT", "BOARD_BOOT",
};
static const char *const reason_names[R_COUNT] = {
    "none", "exec_start", "exec_end", "arg_range", "uaccess", "no_cap", "obj_rule", "obj_confirm", "taint",
    "first_fan", "fan_range", "fan_step", "fan_rate", "servo_range", "servo_step", "servo_rate",
    "no_telemetry", "stale", "presence", "no_echo", "estop", "estop_button", "estop_reset", "not_latched",
    "answer", "timeout", "no_pending", "pending", "action_quota", "request_quota", "nosys", "no_request",
    "halt", "bad_frame", "permissive", "action_cap", "flushed", "board_boot", "old_frame",
};
static const char *const syscall_names[SYS_COUNT] = {
    "exit", "cons_write", "obj_list", "obj_read", "obj_write", "cell_status", "cell_fan", "cell_servo",
    "cell_halt", "cell_lcd", "uptime", "agent_send", "agent_recv",
};

static const char *const event_names[EV_COUNT] = {
    "none", "exec_start", "exec_end", "syscall", "console", "confirm", "tick", "telemetry", "action_boundary",
    "action", "link_drop",
};

const char *policy_event_name(uint8_t kind) { return kind < EV_COUNT ? event_names[kind] : "?"; }
const char *policy_code_name(uint8_t code) { return code < DEC_COUNT ? code_names[code] : "?"; }
const char *policy_reason_name(uint8_t reason) { return reason < R_COUNT ? reason_names[reason] : "?"; }
const char *policy_syscall_name(uint32_t nr) { return nr < SYS_COUNT ? syscall_names[nr] : "unknown"; }
const char *policy_variant_name(void) { return POLICY_VARIANT_NAME; }
int policy_is_permissive(void) { return POLICY_PERMISSIVE; }
