#include "audit.h"
#include "exec.h"
#include "io.h"
#include "kstring.h"
#include "objstore.h"
#include "pit.h"
#include "policy_hash.h"
#include "printk.h"
#include "serial.h"
#include "trap.h"

#define RING 256u
#define TICK_DRAIN_MAX 2u      /* records printed per tick while ring 3 runs (F5) */

struct rec {
    struct pol_event ev;
    struct pol_decision dec;
    uint32_t seq;
    int32_t result;
    uint8_t tainted;
};

static struct pol_state state;
static struct rec ring[RING];
static uint32_t head, tail;       /* head = next write, tail = next flush; count = head - tail */
static uint32_t dropped;
static int tape_on = 1;               /* test images: `tape off` silences COM1 printing (audit_log still filled) */
static int irq_ctx;                   /* inside audit_on_tick */
static int irq_from_user;             /* ... and the tick interrupted ring 3 (main context is parked) */

void audit_set_tape(int on) { tape_on = on; }

void audit_init(void)
{
    policy_state_init(&state);
    head = tail = 0;
}

const struct pol_state *audit_state(void) { return &state; }
uint32_t audit_dropped(void) { return dropped; }

static void print_record(const struct rec *r);
static void drain_one(void);

int audit_feed(const struct pol_event *ev, struct pol_decision *out)
{
    struct pol_state next;
    decide(&state, ev, &next, out);
    int changed = memcmp(&state, &next, sizeof state) != 0;
    int idx = -1;
    if (out->audit)
        next.seq = state.seq + 1;
    if (out->audit || changed) {
        if (head - tail >= RING) {
            /* Never drop (F5, D-52). From a syscall or the kernel loop, and from a tick that interrupted
             * ring 3, the oldest record is printed right here, interrupts as they are (off inside a
             * syscall), until this one fits. A tick that interrupted kernel code mid-print cannot do
             * that; the per-tick and per-syscall drains keep that case from arising. */
            if (irq_ctx && !irq_from_user) {
                dropped++;
            } else {
                if (!serial_at_line_start())
                    printk("\n");
                drain_one();
            }
        }
        if (head - tail < RING) {
            idx = (int)(head % RING);
            struct rec *r = &ring[idx];
            r->ev = *ev;
            r->dec = *out;
            r->seq = next.seq;
            r->result = out->result;
            r->tainted = next.tainted;
            head++;
        }
    }
    state = next;
    return idx;
}

void audit_set_result(int idx, int32_t result)
{
    if (idx >= 0)
        ring[idx].result = result;
}

static const char *subject(const struct rec *r)
{
    return r->ev.kind == EV_SYSCALL ? policy_syscall_name(r->ev.nr) : policy_event_name(r->ev.kind);
}

/* Takes the oldest record out of the ring (interrupt state preserved) and prints it. */
static void drain_one(void)
{
    uint32_t fl = irq_save();
    struct rec r = ring[tail % RING];
    tail++;
    irq_restore(fl);
    print_record(&r);
}

static uint32_t queued(void)
{
    uint32_t fl = irq_save();
    uint32_t n = head - tail;
    irq_restore(fl);
    return n;
}

static void print_record(const struct rec *r)
{
    char line[160];
#ifdef RING3_TEST
    if (tape_on)
        printk("TAPE k=%u s=%u f=%u t=%u nr=%u a0=%d a1=%d a2=%d a3=%d\n", r->ev.kind, r->ev.sub, r->ev.flags,
               r->ev.tick, r->ev.nr, r->ev.a[0], r->ev.a[1], r->ev.a[2], r->ev.a[3]);
#endif
    if (!r->dec.audit)
        return;
    ksnprintf(line, sizeof line,
              "AUDIT seq=%u tick=%u sys=%s target=%d decision=%s reason=%s result=%d tainted=%u%s\n", r->seq,
              r->ev.tick, subject(r), (int)r->dec.target, policy_code_name(r->dec.code),
              policy_reason_name(r->dec.reason), r->result, r->tainted,
              policy_is_permissive() ? " PERMISSIVE (EVAL ONLY)" : "");
    objstore_audit_append(line);
#ifdef RING3_TEST
    if (tape_on)
        printk("%s", line);
#endif
}

/* Line discipline shared by both drains: while a program's own line is in progress the records wait, unless
 * the ring is three quarters full; then the line is broken. Returns 0 when nothing may be printed now. */
static int may_print(int force)
{
    uint32_t n = queued();
    if (!n)
        return 0;
    if (!serial_at_line_start()) {
        if (!force && n < RING * 3 / 4)
            return 0;
        printk("\n");
    }
    return 1;
}

void audit_flush(int force)
{
    if (!may_print(force))
        return;
    while (queued())
        drain_one();
}

uint32_t audit_drain(uint32_t max)
{
    if (!may_print(0))
        return 0;
    uint32_t n = 0;
    while (n < max && queued()) {
        drain_one();
        n++;
    }
    return n;
}

void audit_print_banner(void)
{
    printk("policy: %s sha256=%s\n", policy_variant_name(), POLICY_HASH);
}

#ifdef RING3_TEST
/* Test images: a telemetry sample injected from the tick IRQ every `tt_every` ticks, the way the COM3 driver
 * (T-04) will feed frames from interrupt context while ring 3 runs. */
static int32_t tt_mm, tt_estop;
static uint32_t tt_every;
void audit_set_tick_telemetry(int32_t mm, int32_t estop, uint32_t every)
{
    tt_mm = mm;
    tt_estop = estop;
    tt_every = every;
}
#endif

void audit_on_tick(struct trap_frame *f)
{
    struct pol_event ev = { EV_TICK, 0, 0, 0, ticks_now(), 0, { 0, 0, 0, 0 } };
    struct pol_decision d;
    irq_ctx = 1;
    irq_from_user = trap_from_user(f);
#ifdef RING3_TEST
    if (tt_every && ticks_now() % tt_every == 0) {
        struct pol_event te = { EV_TELEMETRY, 0, EVF_TELE_VALID, 0, ticks_now(), 0, { tt_mm, tt_estop, 0, 0 } };
        audit_feed(&te, &d);
    }
#endif
    audit_feed(&ev, &d);
    if (d.action == ACT_KILL) {
        irq_ctx = 0;
        exec_kill_limit(f);                /* from ring 3: EOI + abandon now; from the kernel: flag */
    } else if (irq_from_user) {
        /* F5: ring 3 is running and may never make a syscall; records fed from interrupt context (telemetry
         * in T-04) are drained a bounded amount per tick. The kernel's main context is parked in the ring-3
         * entry, so nothing of its own is mid-print. */
        audit_drain(TICK_DRAIN_MAX);
    }
    irq_ctx = 0;
    /* ACT_HALT: the device driver (T-04) commands FAN 0 here; the decision is already on the tape. */
}
