#ifndef RING3_KTEST_H
#define RING3_KTEST_H
/* In-kernel unit tests (KTEST=1 builds). Prints `KTEST <name> PASS|FAIL`, a summary, then exits QEMU. */
void ktest_run_all(void) __attribute__((noreturn));
#endif
