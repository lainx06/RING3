#ifndef RING3_IO_H
#define RING3_IO_H
#include <stdint.h>

static inline void outb(uint16_t port, uint8_t v) { __asm__ volatile("outb %0, %1" : : "a"(v), "Nd"(port)); }
static inline void outw(uint16_t port, uint16_t v) { __asm__ volatile("outw %0, %1" : : "a"(v), "Nd"(port)); }
static inline uint8_t inb(uint16_t port)
{
    uint8_t v;
    __asm__ volatile("inb %1, %0" : "=a"(v) : "Nd"(port));
    return v;
}
static inline void io_wait(void) { outb(0x80, 0); }
static inline void cli(void) { __asm__ volatile("cli"); }
static inline void sti(void) { __asm__ volatile("sti"); }
static inline void hlt(void) { __asm__ volatile("hlt"); }
static inline uint32_t read_cr2(void)
{
    uint32_t v;
    __asm__ volatile("mov %%cr2, %0" : "=r"(v));
    return v;
}
static inline uint32_t read_eflags(void)
{
    uint32_t v;
    __asm__ volatile("pushfl; popl %0" : "=r"(v));
    return v;
}
/* Interrupt-state save/restore: code that runs from both the kernel loop (IF = 1) and syscall handling
 * (IF = 0, F1/D-50) must never turn interrupts on behind the caller's back. */
#define EFLAGS_IF 0x200u
static inline uint32_t irq_save(void)
{
    uint32_t f = read_eflags();
    cli();
    return f;
}
static inline void irq_restore(uint32_t f)
{
    if (f & EFLAGS_IF)
        sti();
}
#endif
