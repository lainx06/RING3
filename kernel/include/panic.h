#ifndef RING3_PANIC_H
#define RING3_PANIC_H
#include "trap.h"

void panic(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));
void panic_trap(const struct trap_frame *f) __attribute__((noreturn));
#ifdef KTEST
/* KTEST builds only: a test may arm a context that panic() resumes instead of halting, so detection paths
 * (double free, corrupt header) can be exercised. Absent from production builds. */
struct kctx;
void panic_arm_test_trap(struct kctx *c);
void panic_disarm_test_trap(void);
#endif
#endif
