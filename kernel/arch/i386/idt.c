/* IDT: 256 gates. Exceptions 0-31 and IRQs 0x20-0x2F are DPL-0 interrupt gates; 0x80 is the only DPL-3 gate
 * (INV-3). All gates are interrupt gates, so IF is cleared on entry: the kernel is non-preemptible. */
#include "idt.h"
#include "gdt.h"
#include "kstring.h"
#include "trap.h"
#include <stdint.h>

struct idt_entry {
    uint16_t off_lo;
    uint16_t sel;
    uint8_t zero;
    uint8_t type;
    uint16_t off_hi;
} __attribute__((packed));

struct idt_ptr {
    uint16_t limit;
    uint32_t base;
} __attribute__((packed));

static struct idt_entry idt[256];
static struct idt_ptr idtp;
extern const uint32_t isr_stub_table[49];

static void set_gate(int vec, uint32_t handler, uint8_t type)
{
    idt[vec].off_lo = handler & 0xFFFF;
    idt[vec].sel = GDT_KCODE;
    idt[vec].zero = 0;
    idt[vec].type = type;
    idt[vec].off_hi = (handler >> 16) & 0xFFFF;
}

void idt_init(void)
{
    memset(idt, 0, sizeof idt);               /* unused vectors: not present -> #GP, never a jump to 0 */
    for (int v = 0; v < 48; v++)
        set_gate(v, isr_stub_table[v], 0x8E);  /* present, DPL 0, 32-bit interrupt gate */
    set_gate(SYSCALL_VECTOR, isr_stub_table[48], 0xEE); /* present, DPL 3, interrupt gate */
    idtp.limit = sizeof idt - 1;
    idtp.base = (uint32_t)(uintptr_t)idt;
    __asm__ volatile("lidt %0" : : "m"(idtp));
}
