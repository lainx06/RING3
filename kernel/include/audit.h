#ifndef RING3_AUDIT_H
#define RING3_AUDIT_H
#include "policy.h"
#include "trap.h"
#include <stdint.h>
/* The kernel's only policy state and the tape/audit ring. Every policy input goes through audit_feed(): it
 * calls decide(), commits `next`, and queues a record when the decision audits or the state changed (silent
 * unchanged ticks are dropped losslessly, R2/R8). IRQ context only queues; audit_flush() prints from main
 * context and appends audit lines to the `audit_log` object. */
void audit_init(void);
const struct pol_state *audit_state(void);
/* Returns the ring index of the queued record, or -1 when nothing was queued. */
int audit_feed(const struct pol_event *ev, struct pol_decision *out);
void audit_set_result(int idx, int32_t result);      /* the real result of an executed call */
/* Prints queued records. Without `force` it waits while the console is mid-line (so a program's own output
 * stays intact) unless the ring is three quarters full; with `force` it starts a new line if needed. */
void audit_flush(int force);
uint32_t audit_dropped(void);
void audit_set_tape(int on);
void audit_print_banner(void);
/* Timer path (IRQ context): feeds a tick; acts on LIMIT (kill) and AUTO_HALT. */
void audit_on_tick(struct trap_frame *f);
#endif
