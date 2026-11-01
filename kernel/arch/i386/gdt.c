/* GDT: null, kernel code/data (DPL 0), user code/data (DPL 3), TSS. Flat 4 GiB segments; paging does isolation. */
#include "gdt.h"
#include "kstring.h"

struct gdt_entry {
    uint16_t limit_lo;
    uint16_t base_lo;
    uint8_t base_mid;
    uint8_t access;
    uint8_t gran;      /* flags (4 bits) | limit_hi (4 bits) */
    uint8_t base_hi;
} __attribute__((packed));

struct gdt_ptr {
    uint16_t limit;
    uint32_t base;
} __attribute__((packed));

struct tss {
    uint32_t prev_task;
    uint32_t esp0, ss0;
    uint32_t esp1, ss1;
    uint32_t esp2, ss2;
    uint32_t cr3, eip, eflags;
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi;
    uint32_t es, cs, ss, ds, fs, gs;
    uint32_t ldt;
    uint16_t trap, iomap_base;
} __attribute__((packed));

static struct gdt_entry gdt[6];
static struct gdt_ptr gdtp;
static struct tss tss __attribute__((aligned(16)));

static void set_entry(int i, uint32_t base, uint32_t limit, uint8_t access, uint8_t flags)
{
    gdt[i].limit_lo = limit & 0xFFFF;
    gdt[i].base_lo = base & 0xFFFF;
    gdt[i].base_mid = (base >> 16) & 0xFF;
    gdt[i].access = access;
    gdt[i].gran = (uint8_t)(((limit >> 16) & 0x0F) | (flags & 0xF0));
    gdt[i].base_hi = (base >> 24) & 0xFF;
}

extern char boot_stack_top[];

void gdt_init(void)
{
    /* access: P(0x80) | DPL | S(0x10) | type.  flags: G(0x80) | D(0x40). */
    set_entry(0, 0, 0, 0, 0);
    set_entry(1, 0, 0xFFFFF, 0x9A, 0xC0);   /* kernel code: exec/read */
    set_entry(2, 0, 0xFFFFF, 0x92, 0xC0);   /* kernel data: read/write */
    set_entry(3, 0, 0xFFFFF, 0xFA, 0xC0);   /* user code, DPL 3 */
    set_entry(4, 0, 0xFFFFF, 0xF2, 0xC0);   /* user data, DPL 3 */

    memset(&tss, 0, sizeof tss);
    tss.ss0 = GDT_KDATA;
    tss.esp0 = (uint32_t)(uintptr_t)boot_stack_top;
    tss.iomap_base = sizeof tss;             /* no I/O permission bitmap: IOPL rules apply */
    set_entry(5, (uint32_t)(uintptr_t)&tss, sizeof tss - 1, 0x89, 0x00); /* 32-bit TSS, available */

    gdtp.limit = sizeof gdt - 1;
    gdtp.base = (uint32_t)(uintptr_t)gdt;
    __asm__ volatile("lgdt %0\n\t"
                     "ljmp $0x08, $1f\n\t"
                     "1:\n\t"
                     "movw $0x10, %%ax\n\t"
                     "movw %%ax, %%ds\n\t"
                     "movw %%ax, %%es\n\t"
                     "movw %%ax, %%fs\n\t"
                     "movw %%ax, %%gs\n\t"
                     "movw %%ax, %%ss\n\t"
                     "movw $0x28, %%ax\n\t"
                     "ltr %%ax"
                     : : "m"(gdtp) : "eax", "memory");
}

void tss_set_kernel_stack(uint32_t esp0)
{
    tss.esp0 = esp0;
}
