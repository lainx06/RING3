#include "audit.h"
#include "console.h"
#include "exec.h"
#include "gdt.h"
#include "idt.h"
#include "io.h"
#include "kernel.h"
#include "kmalloc.h"
#include "ktest.h"
#include "kstring.h"
#include "multiboot.h"
#include "objstore.h"
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

uint32_t boot_opt_num(const char *key, uint32_t dflt)
{
    uint32_t kl = (uint32_t)strlen(key);
    for (const char *s = cmdline; *s; s++) {
        if ((s == cmdline || s[-1] == ' ') && strncmp(s, key, kl) == 0 && s[kl] == '=') {
            const char *p = s + kl + 1;
            uint32_t v = 0;
            if (*p < '0' || *p > '9')
                return dflt;
            while (*p >= '0' && *p <= '9')
                v = v * 10 + (uint32_t)(*p++ - '0');
            return v;
        }
    }
    return dflt;
}

static void wait_ticks(uint32_t n)
{
    uint32_t t0 = ticks_now();
    while (ticks_now() - t0 < n)
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
    audit_print_banner();
    printk("memory: %u KiB usable\n", pmm_mmap_usable_kib(mbi));

    gdt_init();
    pic_init();
    idt_init();
    pit_init();
    pmm_init(mbi);
    paging_init();
    kmalloc_init();
    objstore_init();
    audit_init();
    sti();
    printk("init: gdt idt pic pit pmm paging objstore policy ok, interrupts on, %u frames free\n",
           pmm_free_frames());

    uint32_t t0 = ticks_now();
    printk("TIMER t0=%u\n", t0);
    wait_ticks(25);
    printk("TIMER t1=%u\n", ticks_now());

#ifdef KTEST
    if (boot_opt("ktest"))
        ktest_run_all();                 /* exits QEMU with the pass/fail code */
#endif

    if (boot_opt("panic")) {
        printk("deliberate CPL-0 fault (test): write to unmapped 0xFFFFF000\n");
        *(volatile uint32_t *)0xFFFFF000u = 1;
    }

#ifdef RING3_TEST
    exec_set_quotas(boot_opt_num("taction", QUOTA_ACTION_TICKS), boot_opt_num("trequest", QUOTA_REQUEST_TICKS),
                    boot_opt_num("tconfirm", CONFIRM_TIMEOUT_TICKS));
    printk("test image: quotas action=%u request=%u confirm=%u ticks\n",
           boot_opt_num("taction", QUOTA_ACTION_TICKS), boot_opt_num("trequest", QUOTA_REQUEST_TICKS),
           boot_opt_num("tconfirm", CONFIRM_TIMEOUT_TICKS));
#endif
    console_run();
}
