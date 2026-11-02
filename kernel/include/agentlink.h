#ifndef RING3_AGENTLINK_H
#define RING3_AGENTLINK_H
#include "policy.h"
#include <stdint.h>
/* COM2 link to the host bridge (ARCHITECTURE §4, docs/LINK_ABI.md, INV-02, D-53).
 *
 * IRQ 3 moves bytes into a bounded ring; main context (the kernel loop, an agent_recv wait, a confirmation
 * wait) assembles frames, checks COBS, length, CRC, type and the per-type body bounds, and queues the body of
 * every valid bridge->kernel frame for the executor holding CAP_AGENT_LINK. The kernel never reads a byte of
 * an ACTION / TEXT / FINAL / STOP body: ring 3 parses them. Anything malformed is dropped, counted and
 * audited (EV_LINK_DROP -> FRAME_DROP); a valid ACTION is an EV_ACTION policy event (quota restart, cap).
 * Kernel->bridge frames (HELLO, REQUEST, OBSERVATION, AUDIT, EVENT, HEARTBEAT) are sent polled with
 * interrupts off for the whole frame, so an IRQ-context tape record never interleaves with a syscall's. */
void agentlink_init(void);                      /* probe COM2; 115200 8N1, RX interrupt, IRQ 3 */
int agentlink_present(void);                    /* 0 when QEMU has no second serial port */
int agentlink_connected(void);                  /* a valid bridge frame has arrived since boot */
void agentlink_on_irq(void);                    /* IRQ 3 handler: UART FIFO -> ring (flow control when full) */
void agentlink_poll(void);                      /* main context: ring -> frames; HELLO / HEARTBEAT timers */
/* Kernel -> bridge. Returns 0, -EINVAL (body outside the type's bounds), -ENOSYS (no COM2 device) or
 * -ETIMEDOUT (the UART did not accept the frame: counted as a TX drop). */
int agentlink_send(uint8_t type, const uint8_t *body, uint32_t len);
void agentlink_send_event(const struct pol_event *ev);
void agentlink_send_audit(uint32_t seq, const struct pol_event *ev, const struct pol_decision *d,
                          int32_t result, uint8_t tainted);
/* Bridge -> kernel queue for the executor: copies `type u8 | seq u16 | len u16 | body` into dst. Returns the
 * byte count, 0 when the queue is empty, -EINVAL when `cap` cannot hold the frame (nothing dequeued). */
int agentlink_recv(uint8_t *dst, uint32_t cap);
int agentlink_queued(void);
void agentlink_flush_queue(void);               /* D-53: at EXEC_START / EXEC_END; each frame audited */
uint32_t agentlink_frames_dropped(void);
void agentlink_print_status(void);              /* one `link:` line for the console `status` command */
#endif
