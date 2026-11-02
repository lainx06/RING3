/* COM1 console: polled, 115200 8N1, interrupts disabled (IER = 0). */
#include "serial.h"
#include "io.h"

#define REG_DATA 0
#define REG_IER  1
#define REG_FCR  2
#define REG_LCR  3
#define REG_MCR  4
#define REG_LSR  5

void serial_init(void)
{
    outb(COM1_PORT + REG_IER, 0x00);        /* no interrupts: COM1 is polled */
    outb(COM1_PORT + REG_LCR, 0x80);        /* DLAB on */
    outb(COM1_PORT + REG_DATA, 0x01);       /* divisor 1 -> 115200 */
    outb(COM1_PORT + REG_IER, 0x00);
    outb(COM1_PORT + REG_LCR, 0x03);        /* 8N1, DLAB off */
    outb(COM1_PORT + REG_FCR, 0xC7);        /* FIFO on, clear, 14-byte threshold */
    outb(COM1_PORT + REG_MCR, 0x0B);        /* DTR, RTS, OUT2 */
}

static int line_start = 1;

int serial_at_line_start(void) { return line_start; }

void serial_putc(char c)
{
    line_start = c == '\n';
    if (c == '\n')
        serial_putc('\r');
    while ((inb(COM1_PORT + REG_LSR) & 0x20) == 0)
        ;
    outb(COM1_PORT + REG_DATA, (uint8_t)c);
}

void serial_write(const char *s, uint32_t n)
{
    while (n--)
        serial_putc(*s++);
}

int serial_getc_nonblock(void)
{
    if (inb(COM1_PORT + REG_LSR) & 0x01)
        return inb(COM1_PORT + REG_DATA);
    return -1;
}
