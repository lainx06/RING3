#include "gdt.h"
#include "idt.h"
#include "io.h"
#include "kernel.h"
#include "kstring.h"
#include "multiboot.h"
#include "panic.h"
#include "pic.h"
#include "pit.h"
#include "printk.h"
#include "qemu.h"
#include "serial.h"

static char cmdline[256];

int boot_opt(const char *word)
{
    return str_has_word(cmdline, word);
}

static uint32_t total_ram_kib(const struct multiboot_info *mbi)
{
    uint32_t kib = 0;
    if (mbi->flags & MBI_FLAG_MMAP) {
        uint32_t p = mbi->mmap_addr, end = mbi->mmap_addr + mbi->mmap_length;
        while (p < end) {
            const struct multiboot_mmap_entry *e = (const struct multiboot_mmap_entry *)(uintptr_t)p;
            if (e->type == MULTIBOOT_MEMORY_AVAILABLE)
                kib += (uint32_t)(e->len / 1024);
            p += e->size + 4;
        }
    } else if (mbi->flags & MBI_FLAG_MEM) {
        kib = mbi->mem_lower + mbi->mem_upper;
    }
    return kib;
}

static void wait_ticks(uint32_t n)
{
    uint32_t t0 = ticks_now();
    while (ticks_now() - t0 < n)
        hlt();
}

static void finish(void)
{
    if (boot_opt("testexit"))
        qemu_exit(QEMU_EXIT_PASS);
    for (;;)
        hlt();
}

void kmain(uint32_t magic, uint32_t mbi_addr)
{
    serial_init();
    if (magic != MULTIBOOT_BOOTLOADER_MAGIC)
        panic("bad multiboot magic 0x%08x", magic);
    const struct multiboot_info *mbi = (const struct multiboot_info *)(uintptr_t)mbi_addr;
    if (mbi->flags & MBI_FLAG_CMDLINE)
        strncpy(cmdline, (const char *)(uintptr_t)mbi->cmdline, sizeof cmdline - 1);

    printk("RING3 %s build %s\n", RING3_VERSION, BUILD_HASH);
    printk("memory: %u KiB usable\n", total_ram_kib(mbi));

    gdt_init();
    pic_init();
    idt_init();
    pit_init();
    sti();
    printk("init: gdt idt pic pit ok, interrupts on\n");

    uint32_t t0 = ticks_now();
    printk("TIMER t0=%u\n", t0);
    wait_ticks(25);
    printk("TIMER t1=%u\n", ticks_now());

    if (boot_opt("panic")) {
        printk("deliberate CPL-0 fault (test)\n");
        __asm__ volatile("ud2");
    }

    printk("KERNEL ALIVE uptime=%u\n", ticks_now());
    finish();
}
