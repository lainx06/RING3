#ifndef RING3_LINKPROBE_H
#define RING3_LINKPROBE_H
/* T-01B COM3 link smoke test (KTEST_LINK=1 builds only, `linktest` boot option). Exits QEMU pass/fail. */
void linkprobe_run(void) __attribute__((noreturn));
#endif
