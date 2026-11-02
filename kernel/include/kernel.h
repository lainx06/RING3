#ifndef RING3_KERNEL_H
#define RING3_KERNEL_H
#include <stdint.h>
/* Boot command-line tokens (QEMU -append): "testexit" (exit QEMU when done), "panic" (deliberate CPL-0 fault),
 * "ktest" (run in-kernel tests; KTEST builds only). Test images also take "taction=N trequest=N tconfirm=N"
 * (ticks) to shorten the quotas and the confirmation timeout. */
int boot_opt(const char *word);
uint32_t boot_opt_num(const char *key, uint32_t dflt);   /* "key=N", else dflt */
#endif
