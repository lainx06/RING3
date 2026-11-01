#ifndef RING3_TRAP_H
#define RING3_TRAP_H
#include <stdint.h>

/* Register frame saved by the common ISR stub (kernel/arch/i386/isr.S), lowest address first. */
struct trap_frame {
    uint32_t gs, fs, es, ds;
    uint32_t edi, esi, ebp, esp_at_pusha, ebx, edx, ecx, eax;
    uint32_t vector, err;
    uint32_t eip, cs, eflags;
    uint32_t useresp, ss;             /* present only when the trap came from CPL 3 */
};

#define IRQ_BASE   0x20
#define IRQ_TIMER  (IRQ_BASE + 0)
#define SYSCALL_VECTOR 0x80

static inline int trap_from_user(const struct trap_frame *f) { return (f->cs & 3) == 3; }

void isr_dispatch(struct trap_frame *f);   /* called from isr.S */
#endif
