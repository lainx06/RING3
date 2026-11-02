/* Common trap dispatcher. CPL-0 exception -> panic (INV-12). CPL-3 exception -> exec_on_user_fault (INV-6).
 * NMI, #DF and #MC are never user faults. Timer -> tick + a policy tick event (quotas, lease; R2). int 0x80 ->
 * syscall. */
#include "trap.h"
#include "audit.h"
#include "panic.h"
#include "pic.h"
#include "pit.h"
#include "printk.h"
#include "exec.h"
#include "syscall.h"

void isr_dispatch(struct trap_frame *f)
{
    if (f->vector < 32) {
        int machine = f->vector == 2 || f->vector == 8 || f->vector == 18;
        if (trap_from_user(f) && !machine)
            exec_on_user_fault(f);            /* does not return */
        panic_trap(f);                        /* does not return */
    }
    if (f->vector == SYSCALL_VECTOR) {
        if (!trap_from_user(f))
            panic("int 0x80 from CPL 0 at eip=0x%08x", f->eip);
        syscall_dispatch(f);
        return;
    }
    if (f->vector >= IRQ_BASE && f->vector < IRQ_BASE + 16) {
        uint8_t irq = (uint8_t)(f->vector - IRQ_BASE);
        if (irq == 0) {
            pit_on_tick();
            audit_on_tick(f);                 /* may not return (LIMIT from ring 3): sends EOI itself first */
        }
        pic_eoi(irq);
        return;
    }
    panic("unexpected vector %u at eip=0x%08x", f->vector, f->eip);
}
