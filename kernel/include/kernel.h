#ifndef RING3_KERNEL_H
#define RING3_KERNEL_H
#include <stdint.h>
/* Boot command-line tokens (QEMU -append): "testexit" (exit QEMU when done), "panic" (deliberate CPL-0 fault),
 * "ktest" (run in-kernel tests; only present in KTEST builds). */
int boot_opt(const char *word);
#endif
