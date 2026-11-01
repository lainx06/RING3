/* Bitmap physical frame allocator over the identity-mapped window. Bit set = used. */
#include "pmm.h"
#include "kstring.h"
#include "panic.h"
#include "printk.h"

#define NFRAMES (PMM_LIMIT / PAGE_SIZE)
static uint32_t bitmap[NFRAMES / 32];
static uint32_t free_frames;

extern char _kernel_start[], _kernel_end[];

static inline void set_used(uint32_t i) { bitmap[i / 32] |= 1u << (i % 32); }
static inline void set_free(uint32_t i) { bitmap[i / 32] &= ~(1u << (i % 32)); }
static inline int is_used(uint32_t i) { return (bitmap[i / 32] >> (i % 32)) & 1u; }

void pmm_init(const struct multiboot_info *mbi)
{
    memset(bitmap, 0xFF, sizeof bitmap);           /* everything used until proven available */
    free_frames = 0;
    if (!(mbi->flags & MBI_FLAG_MMAP))
        panic("multiboot memory map missing");
    uint32_t p = mbi->mmap_addr, end = mbi->mmap_addr + mbi->mmap_length;
    while (p < end) {
        const struct multiboot_mmap_entry *e = (const struct multiboot_mmap_entry *)(uintptr_t)p;
        if (e->type == MULTIBOOT_MEMORY_AVAILABLE) {
            uint64_t a = (e->addr + PAGE_SIZE - 1) & ~(uint64_t)(PAGE_SIZE - 1);
            uint64_t b = (e->addr + e->len) & ~(uint64_t)(PAGE_SIZE - 1);
            for (; a + PAGE_SIZE <= b; a += PAGE_SIZE) {
                if (a < 0x100000 || a >= PMM_LIMIT)
                    continue;                      /* low memory (BIOS, mbi) and beyond the window: reserved */
                set_free((uint32_t)(a / PAGE_SIZE));
                free_frames++;
            }
        }
        p += e->size + 4;
    }
    uint32_t ks = (uint32_t)(uintptr_t)_kernel_start / PAGE_SIZE;
    uint32_t ke = ((uint32_t)(uintptr_t)_kernel_end + PAGE_SIZE - 1) / PAGE_SIZE;
    for (uint32_t i = ks; i < ke && i < NFRAMES; i++) {
        if (!is_used(i)) {
            set_used(i);
            free_frames--;
        }
    }
}

uint32_t pmm_alloc(void)
{
    for (uint32_t w = 0; w < NFRAMES / 32; w++) {
        if (bitmap[w] == 0xFFFFFFFFu)
            continue;
        for (uint32_t b = 0; b < 32; b++) {
            uint32_t i = w * 32 + b;
            if (!is_used(i)) {
                set_used(i);
                free_frames--;
                return i * PAGE_SIZE;
            }
        }
    }
    return 0;
}

void pmm_free(uint32_t paddr)
{
    if (paddr & (PAGE_SIZE - 1))
        panic("pmm_free: unaligned 0x%08x", paddr);
    uint32_t i = paddr / PAGE_SIZE;
    if (paddr < 0x100000 || i >= NFRAMES)
        panic("pmm_free: 0x%08x outside the allocator window", paddr);
    if (!is_used(i))
        panic("pmm_free: double free of 0x%08x", paddr);
    set_free(i);
    free_frames++;
}

uint32_t pmm_free_frames(void) { return free_frames; }
uint32_t pmm_total_frames(void) { return NFRAMES; }
int pmm_is_used(uint32_t paddr) { return is_used(paddr / PAGE_SIZE); }
