#ifndef RING3_PMM_H
#define RING3_PMM_H
#include "multiboot.h"
#include <stdint.h>

#define PAGE_SIZE 4096u
/* The allocator hands out frames only from the identity-mapped window [1 MiB, PMM_LIMIT), so the kernel can
 * always touch a frame through its physical address. */
#define PMM_LIMIT (16u * 1024 * 1024)   /* (prov.) matches the identity map in paging.c */

void pmm_init(const struct multiboot_info *mbi);
uint32_t pmm_alloc(void);               /* physical address of a frame, or 0 when out of frames */
void pmm_free(uint32_t paddr);          /* panics on double free or on a frame it never handed out */
uint32_t pmm_free_frames(void);
uint32_t pmm_total_frames(void);
int pmm_is_used(uint32_t paddr);
#endif
