#ifndef RING3_GDT_H
#define RING3_GDT_H
#include <stdint.h>

#define GDT_KCODE 0x08
#define GDT_KDATA 0x10
#define GDT_UCODE 0x18
#define GDT_UDATA 0x20
#define GDT_TSS   0x28
#define SEL_USER_CODE (GDT_UCODE | 3)
#define SEL_USER_DATA (GDT_UDATA | 3)

void gdt_init(void);
void tss_set_kernel_stack(uint32_t esp0);
#endif
