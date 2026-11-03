#ifndef RING3_DEVICE_H
#define RING3_DEVICE_H
#include "policy.h"
#include "trap.h"
#include <stdint.h>
/* COM3 work-cell driver (T-04, ARCHITECTURE §3a, INV-13, INV-16): the kernel owns the Uno link; no syscall
 * touches the port. Frames from the board are decoded here (COBS, CRC, STATE layout, hardware/protocol.md
 * receiver rules) and every valid one is an EV_TELEMETRY event into decide(); commands leave only when a
 * decision says so: ACT_EXEC on a cell_* syscall, ACT_HALT (FAN 0) from the lease, a board boot or the
 * E-stop latch, and the console's confirmed `estop reset`. PING every 250 ms once the board's current boot
 * is known. Receive: IRQ 4 (shared with the polled COM1, D-39a) plus a poll on every PIT tick (D-54), under a
 * per-tick byte/frame budget (T-09 F4) so a flood cannot hold the CPU in interrupt context. */
void device_init(void);                        /* probe COM3; 115200 8N1, RX IRQ 4 */
int device_present(void);
int device_synced(void);                       /* a frame of the board's current boot has arrived (TX allowed) */
void device_on_irq(struct trap_frame *f);      /* IRQ 4 handler */
void device_on_tick(void);                     /* from audit_on_tick (IRQ 0 context): poll backstop, PING */
/* Commands, each only after the matching decision. Return 0, -ENOSYS (no COM3), -ETIMEDOUT (board boot not
 * seen yet, or the UART refused the frame: nothing was sent). */
int device_fan(uint32_t duty);
int device_servo(uint32_t deg);
int device_halt(void);                         /* FAN 0; the servo is left where it is (held) */
int device_lcd(uint32_t line, const char *text, uint32_t len);
int device_estop_reset(void);
/* ACT_HALT: FAN 0 now (never anything else) and the decision on the panel. */
void device_auto_halt(const struct pol_decision *d);
/* The decision on a cell_* syscall (or the console reset) shown on the LCD and the alarm. */
void device_show(uint32_t nr, const struct pol_decision *d);
/* The 16-byte cell_status record (abi/link_abi.h LINK_CELLST_*); -ENOSYS without a device. */
int device_status(uint8_t out[16]);
/* A copy of the last accepted STATE frame and its tick (display, T-08); 0 when none has arrived. */
struct cell_state;
int device_last_frame(struct cell_state *st, uint32_t *tick);
void device_print_status(void);                /* one `cell:` line for the console `status` command */
#ifdef RING3_TEST
void device_mute(uint32_t ticks);              /* test images: send nothing for `ticks` (link-watchdog test) */
int device_rx_ready(void);                     /* test images: a COM3 byte is waiting in the UART (F1 seam) */
#endif
#endif
