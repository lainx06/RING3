#ifndef RING3_SERIAL_H
#define RING3_SERIAL_H
#include <stdint.h>

#define COM1_PORT 0x3F8

void serial_init(void);           /* COM1: 115200 8N1, IER = 0 (polled) */
void serial_putc(char c);
void serial_write(const char *s, uint32_t n);
int serial_getc_nonblock(void);   /* -1 if no byte waiting */
#endif
