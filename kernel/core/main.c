#include "exec.h"
#include "gdt.h"
#include "idt.h"
#include "io.h"
#include "kernel.h"
#include "kmalloc.h"
#include "ktest.h"
#include "kstring.h"
#include "multiboot.h"
#include "paging.h"
#include "pmm.h"
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

static void wait_ticks(uint32_t n)
{
    uint32_t t0 = ticks_now();
    while (ticks_now() - t0 < n)
        hlt();
}

#define UPROG(sym) extern const uint8_t uprog_##sym##_start[], uprog_##sym##_end[];
UPROG(hello) UPROG(kwrite) UPROG(ticks) UPROG(priv) UPROG(badptr) UPROG(spin)
#define ENTRY(sym) { #sym, uprog_##sym##_start, 0, 0, 0 }
static struct uprog progs[] = {
    ENTRY(hello), ENTRY(kwrite), ENTRY(ticks), ENTRY(priv), ENTRY(badptr), ENTRY(spin), ENTRY(ticks),
};
#define LEN(sym) (uint32_t)(uprog_##sym##_end - uprog_##sym##_start)

/* The isolation slice (T-01 step 8): run each embedded program in ring 3, one after another. */
static void run_isolation_slice(void)
{
    progs[0].image_len = LEN(hello);
    progs[1].image_len = LEN(kwrite);
    progs[2].image_len = LEN(ticks);
    progs[3].image_len = LEN(priv);
    progs[4].image_len = LEN(badptr);
    progs[5].image_len = LEN(spin);
    progs[6].image_len = LEN(ticks);
    /* The first program allocates the (kept) user page tables; after that every program must give back
     * exactly the frames it took. */
    uint32_t baseline = 0;
    for (unsigned i = 0; i < sizeof progs / sizeof progs[0]; i++) {
        int pid = (int)i + 1;
        printk("RUN pid=%d name=%s uptime=%u\n", pid, progs[i].name, ticks_now());
        struct exec_outcome o = exec_run(&progs[i], pid);
        const char *kind = o.kind == EXEC_EXIT ? "EXIT" : o.kind == EXEC_FAULT ? "FAULT" : "LIMIT";
        printk("END pid=%d %s ticks=%u uptime=%u\n", pid, kind, o.ticks_used, ticks_now());
        if (i == 0)
            baseline = pmm_free_frames();
        else if (pmm_free_frames() != baseline)
            panic("frame leak after pid %d: %u free, expected %u", pid, pmm_free_frames(), baseline);
    }
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
    printk("memory: %u KiB usable\n", pmm_mmap_usable_kib(mbi));

    gdt_init();
    pic_init();
    idt_init();
    pit_init();
    pmm_init(mbi);
    paging_init();
    kmalloc_init();
    sti();
    printk("init: gdt idt pic pit pmm paging ok, interrupts on, %u frames free\n", pmm_free_frames());

    uint32_t t0 = ticks_now();
    printk("TIMER t0=%u\n", t0);
    wait_ticks(25);
    printk("TIMER t1=%u\n", ticks_now());

#ifdef KTEST
    if (boot_opt("ktest")) {
        ktest_run_all();                 /* exits QEMU with the pass/fail code */
        finish();
    }
#endif

    if (boot_opt("panic")) {
        printk("deliberate CPL-0 fault (test): write to unmapped 0xFFFFF000\n");
        *(volatile uint32_t *)0xFFFFF000u = 1;
    }

    run_isolation_slice();

    printk("KERNEL ALIVE uptime=%u\n", ticks_now());
    finish();
}
