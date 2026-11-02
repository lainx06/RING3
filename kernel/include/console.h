#ifndef RING3_CONSOLE_H
#define RING3_CONSOLE_H
#include <stdint.h>
#define CONSOLE_LINE_MAX 240u   /* a request line (T-04: the self-test request is ~150 chars; < USER_ARG_MAX) */
/* COM1 line input (polled) and the kernel command loop. Only main context calls these. */
void console_poll(void);                                /* move waiting COM1 bytes into the line buffer */
int console_line_ready(void);
void console_take_line(char *dst, uint32_t cap);        /* copies the finished line, clears the buffer */
void console_drain(void);                               /* R10: empty the receive FIFO and the line buffer */
void console_run(void) __attribute__((noreturn));       /* the `ring3> ` loop */
#endif
