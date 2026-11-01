#ifndef RING3_PAGING_H
#define RING3_PAGING_H
#include <stdint.h>

#define PTE_P 0x001u
#define PTE_W 0x002u
#define PTE_U 0x004u
#define PTE_ADDR(x) ((x) & ~0xFFFu)

void paging_init(void);                                   /* identity map 16 MiB supervisor-only; CR0.PG|WP */
void paging_map_user(uint32_t vaddr, uint32_t paddr, int writable);
uint32_t paging_unmap_user(uint32_t vaddr);               /* returns the old physical address (0 if unmapped) */
uint32_t paging_lookup(uint32_t vaddr);                   /* raw PTE, 0 when no mapping */
void paging_flush_tlb(void);
#endif
