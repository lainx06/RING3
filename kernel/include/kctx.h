#ifndef RING3_KCTX_H
#define RING3_KCTX_H
#include <stdint.h>
/* Saved kernel context (kernel/arch/i386/usermode.S): kctx_save returns 0 now and `val` when resumed. */
struct kctx {
    uint32_t esp, ebp, ebx, esi, edi, eip;
};
int kctx_save(struct kctx *c) __attribute__((returns_twice));
void kctx_resume(struct kctx *c, int val) __attribute__((noreturn));
#endif
