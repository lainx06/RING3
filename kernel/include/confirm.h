#ifndef RING3_CONFIRM_H
#define RING3_CONFIRM_H
#include "policy.h"
/* Two-phase human confirmation (R1, R10, INV-05). Called from the syscall path after decide() returned
 * CONFIRM_PENDING: drains COM1, prints the prompt, idles with interrupts on until the next console line or
 * the policy's timeout (a tick event), feeds the answer back through decide(), and returns the final decision
 * in `d` (CONFIRM_YES -> execute, otherwise `d->result` is the error). May abandon the program (LIMIT). */
void confirm_run(const struct pol_event *call, struct pol_decision *d);
#endif
