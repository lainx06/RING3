/* A kernel panic is always a bug (INV-12). Print, try to exit QEMU with the panic code, then halt forever. */
#include "panic.h"
#include "io.h"
#include "printk.h"
#include "qemu.h"

void qemu_exit(unsigned code)
{
    outw(QEMU_EXIT_PORT, (uint16_t)code);
}

static void __attribute__((noreturn)) halt_forever(void)
{
    qemu_exit(QEMU_EXIT_PANIC);
    for (;;) {
        cli();
        hlt();
    }
}

void panic(const char *fmt, ...)
{
    cli();
    va_list ap;
    printk("\nPANIC: ");
    va_start(ap, fmt);
    vprintk(fmt, ap);
    va_end(ap);
    printk("\n");
    halt_forever();
}

void panic_trap(const struct trap_frame *f)
{
    cli();
    printk("\nPANIC: CPL-0 exception vector=%u err=0x%08x\n", f->vector, f->err);
    printk("  eip=0x%08x cs=0x%04x eflags=0x%08x cr2=0x%08x\n", f->eip, f->cs, f->eflags, read_cr2());
    printk("  eax=0x%08x ebx=0x%08x ecx=0x%08x edx=0x%08x\n", f->eax, f->ebx, f->ecx, f->edx);
    printk("  esi=0x%08x edi=0x%08x ebp=0x%08x esp=0x%08x\n", f->esi, f->edi, f->ebp,
           (uint32_t)(uintptr_t)&f->eip + 12);
    printk("  ds=0x%04x es=0x%04x fs=0x%04x gs=0x%04x\n", f->ds, f->es, f->fs, f->gs);
    halt_forever();
}
