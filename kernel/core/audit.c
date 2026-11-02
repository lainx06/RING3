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

void audit_set_tape(int on) { tape_on = on; }

void audit_init(void)
{
    policy_state_init(&state);
    head = tail = 0;
}

const struct pol_state *audit_state(void) { return &state; }
uint32_t audit_dropped(void) { return dropped; }

int audit_feed(const struct pol_event *ev, struct pol_decision *out)
{
    struct pol_state next;
    decide(&state, ev, &next, out);
    int changed = memcmp(&state, &next, sizeof state) != 0;
    int idx = -1;
    if (out->audit)
        next.seq = state.seq + 1;
    if (out->audit || changed) {
        if (head - tail < RING) {
            idx = (int)(head % RING);
            struct rec *r = &ring[idx];
            r->ev = *ev;
            r->dec = *out;
            r->seq = next.seq;
            r->result = out->result;
            r->tainted = next.tainted;
            head++;
        } else {
            dropped++;
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

void audit_flush(int force)
{
    char line[160];
    if (!force && !serial_at_line_start() && head - tail < RING * 3 / 4)
        return;
    if (!serial_at_line_start() && head != tail)
        printk("\n");
    for (;;) {
        cli();
        if (head == tail) {
            sti();
            return;
        }
        struct rec r = ring[tail % RING];
        tail++;
        sti();
#ifdef RING3_TEST
        if (tape_on)
            printk("TAPE k=%u s=%u f=%u t=%u nr=%u a0=%d a1=%d a2=%d a3=%d\n", r.ev.kind, r.ev.sub, r.ev.flags,
               r.ev.tick, r.ev.nr, r.ev.a[0], r.ev.a[1], r.ev.a[2], r.ev.a[3]);
#endif
        if (!r.dec.audit)
            continue;
        ksnprintf(line, sizeof line,
                  "AUDIT seq=%u tick=%u sys=%s target=%d decision=%s reason=%s result=%d tainted=%u%s\n",
                  r.seq, r.ev.tick, subject(&r), (int)r.dec.target, policy_code_name(r.dec.code),
                  policy_reason_name(r.dec.reason), r.result, r.tainted,
                  policy_is_permissive() ? " PERMISSIVE (EVAL ONLY)" : "");
        objstore_audit_append(line);
#ifdef RING3_TEST
        if (tape_on)
            printk("%s", line);
#endif
    }
}

void audit_print_banner(void)
{
    printk("policy: %s sha256=%s\n", policy_variant_name(), POLICY_HASH);
}

void audit_on_tick(struct trap_frame *f)
{
    struct pol_event ev = { EV_TICK, 0, 0, 0, ticks_now(), 0, { 0, 0, 0, 0 } };
    struct pol_decision d;
    audit_feed(&ev, &d);
    if (d.action == ACT_KILL)
        exec_kill_limit(f);                /* from ring 3: EOI + abandon now; from the kernel: flag */
    /* ACT_HALT: the device driver (T-04) commands FAN 0 here; the decision is already on the tape. */
}
