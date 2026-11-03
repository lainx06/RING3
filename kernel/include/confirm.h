#ifndef RING3_CONFIRM_H
#define RING3_CONFIRM_H
#include "policy.h"
/* Two-phase human confirmation (R1, R10, INV-05). Called from the syscall path after decide() returned
 * CONFIRM_PENDING: drains COM1, prints the prompt, idles with interrupts on until the next console line or
 * the policy's timeout (a tick event), feeds the answer back through decide(), and returns the final decision
 * in `d` (CONFIRM_YES -> execute, otherwise `d->result` is the error). May abandon the program (LIMIT).
 * Returns with interrupts OFF (T-09 F1): the caller writes the confirmed command to COM3 before anything
 * re-enables them, so no telemetry decision can land between the answer's decision and that command. */
void confirm_run(const struct pol_event *call, struct pol_decision *d);
#ifdef RING3_TEST
/* F4 test knob: while on, a ready console line is held until the timeout tick has cleared `pending`, which
 * reproduces an answer arriving in the same wake-up as the timeout (D-52). */
void confirm_set_race(int on);
/* T-09 F1 test seam: while on, after a CONFIRM_YES the kernel prints `confirmgap: waiting for COM3` and holds
 * (interrupts as they are) until a COM3 byte is waiting or the E-stop latched, before the command is sent. */
void confirm_set_gap(int on);
#endif
#endif
