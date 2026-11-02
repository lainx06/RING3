#ifndef RING3_SYSCALL_H
#define RING3_SYSCALL_H
#include "policy.h"          /* syscall numbers, capabilities and error codes: one source of truth */
#include "trap.h"

#define MAX_SYSCALL_BUF 4096u

#ifdef RING3_TEST
/* Test-image-only numbers (absent from production builds): kernel instrumentation, not executor authority. */
#define SYS_TEST_UREAD    100   /* kernel writes a 0xA5 pattern into a user buffer (exercises the W check) */
#define SYS_TEST_BOUNDARY 101   /* calls exec_action_boundary() */
#define SYS_TEST_TELE     102   /* injects a telemetry sample: a0 = distance_mm, a1 = estop, a2 = valid */
#endif

void syscall_dispatch(struct trap_frame *f);
#endif
