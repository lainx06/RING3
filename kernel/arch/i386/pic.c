/* 8259A PIC pair remapped to vectors 0x20-0x2F. Everything masked until a driver unmasks its line. */
#include "pic.h"
#include "io.h"

#define PIC1_CMD  0x20
#define PIC1_DATA 0x21
#define PIC2_CMD  0xA0
#define PIC2_DATA 0xA1
#define PIC_EOI   0x20

void pic_init(void)
{
    outb(PIC1_CMD, 0x11);  io_wait();      /* ICW1: init, cascade, ICW4 needed */
    outb(PIC2_CMD, 0x11);  io_wait();
    outb(PIC1_DATA, 0x20); io_wait();      /* ICW2: master vector base */
    outb(PIC2_DATA, 0x28); io_wait();      /* ICW2: slave vector base */
    outb(PIC1_DATA, 0x04); io_wait();      /* ICW3: slave on IR2 */
    outb(PIC2_DATA, 0x02); io_wait();      /* ICW3: slave id 2 */
    outb(PIC1_DATA, 0x01); io_wait();      /* ICW4: 8086 mode */
    outb(PIC2_DATA, 0x01); io_wait();
    outb(PIC1_DATA, 0xFF);                 /* mask all */
    outb(PIC2_DATA, 0xFF);
}

void pic_unmask(uint8_t irq)
{
    uint16_t port = irq < 8 ? PIC1_DATA : PIC2_DATA;
    uint8_t bit = (uint8_t)(1u << (irq & 7));
    outb(port, inb(port) & (uint8_t)~bit);
    if (irq >= 8)
        outb(PIC1_DATA, inb(PIC1_DATA) & (uint8_t)~(1u << 2));
}

void pic_mask(uint8_t irq)
{
    uint16_t port = irq < 8 ? PIC1_DATA : PIC2_DATA;
    outb(port, inb(port) | (uint8_t)(1u << (irq & 7)));
}

void pic_eoi(uint8_t irq)
{
    if (irq >= 8)
        outb(PIC2_CMD, PIC_EOI);
    outb(PIC1_CMD, PIC_EOI);
}
