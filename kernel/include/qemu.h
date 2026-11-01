#ifndef RING3_QEMU_H
#define RING3_QEMU_H
/* isa-debug-exit at 0xf4: QEMU exits with (value << 1) | 1. Values chosen so QEMU's own exit code 1 is distinct. */
#define QEMU_EXIT_PORT   0xF4
#define QEMU_EXIT_PASS   0x10   /* host sees 33 */
#define QEMU_EXIT_FAIL   0x11   /* host sees 35 */
#define QEMU_EXIT_PANIC  0x12   /* host sees 37 */

void qemu_exit(unsigned code);  /* returns only if no isa-debug-exit device is present */
#endif
