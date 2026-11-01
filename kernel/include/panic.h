#ifndef RING3_PANIC_H
#define RING3_PANIC_H
#include "trap.h"

void panic(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));
void panic_trap(const struct trap_frame *f) __attribute__((noreturn));
#endif
