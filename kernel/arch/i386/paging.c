/* 32-bit paging, no PAE. Low 16 MiB identity-mapped supervisor-only (U = 0) with CR0.WP set, so ring 3 can't
 * reach kernel memory and ring 0 honours read-only user pages (INV-3). User mappings live under 0x40000000..
 * 0x7FFFF000 and are added/removed per program by exec.c. */
#include "paging.h"
#include "exec.h"
#include "kstring.h"
#include "panic.h"
#include "pmm.h"

#define PDE_COUNT 1024
#define IDENT_TABLES (PMM_LIMIT / (4u * 1024 * 1024))

static uint32_t page_dir[PDE_COUNT] __attribute__((aligned(4096)));
static uint32_t ident_tables[IDENT_TABLES][1024] __attribute__((aligned(4096)));

static uint32_t *table_for(uint32_t vaddr, int create)
{
    uint32_t pdi = vaddr >> 22;
    if (!(page_dir[pdi] & PTE_P)) {
        if (!create)
            return 0;
        uint32_t frame = pmm_alloc();
        if (!frame)
            panic("paging: out of frames for a page table");
        memset((void *)(uintptr_t)frame, 0, PAGE_SIZE);
        /* User-region PDEs are U|W at directory level; the PTE decides per page. */
        page_dir[pdi] = frame | PTE_P | PTE_W | PTE_U;
    }
    return (uint32_t *)(uintptr_t)PTE_ADDR(page_dir[pdi]);
}

void paging_init(void)
{
    memset(page_dir, 0, sizeof page_dir);
    for (uint32_t t = 0; t < IDENT_TABLES; t++) {
        for (uint32_t i = 0; i < 1024; i++)
            ident_tables[t][i] = ((t * 1024 + i) * PAGE_SIZE) | PTE_P | PTE_W;   /* supervisor only */
        page_dir[t] = (uint32_t)(uintptr_t)ident_tables[t] | PTE_P | PTE_W;
    }
    ident_tables[0][0] = 0;                          /* page 0 unmapped: a kernel null dereference panics */
    uint32_t cr0;
    __asm__ volatile("mov %0, %%cr3" : : "r"((uint32_t)(uintptr_t)page_dir) : "memory");
    __asm__ volatile("mov %%cr0, %0" : "=r"(cr0));
    cr0 |= (1u << 31) | (1u << 16);                 /* PG | WP */
    __asm__ volatile("mov %0, %%cr0" : : "r"(cr0) : "memory");
}

static void check_user_vaddr(uint32_t vaddr)
{
    if (vaddr & (PAGE_SIZE - 1))
        panic("paging: unaligned user vaddr 0x%08x", vaddr);
    if (vaddr < USER_BASE || vaddr >= USER_TOP)
        panic("paging: vaddr 0x%08x outside user range", vaddr);
}

void paging_map_user(uint32_t vaddr, uint32_t paddr, int writable)
{
    check_user_vaddr(vaddr);
    uint32_t *pt = table_for(vaddr, 1);
    uint32_t idx = (vaddr >> 12) & 1023;
    if (pt[idx] & PTE_P)
        panic("paging: 0x%08x already mapped", vaddr);
    pt[idx] = PTE_ADDR(paddr) | PTE_P | PTE_U | (writable ? PTE_W : 0);
}

uint32_t paging_unmap_user(uint32_t vaddr)
{
    check_user_vaddr(vaddr);
    uint32_t *pt = table_for(vaddr, 0);
    if (!pt)
        return 0;
    uint32_t idx = (vaddr >> 12) & 1023;
    uint32_t old = pt[idx];
    pt[idx] = 0;
    return (old & PTE_P) ? PTE_ADDR(old) : 0;
}

uint32_t paging_lookup(uint32_t vaddr)
{
    uint32_t pde = page_dir[vaddr >> 22];
    if (!(pde & PTE_P))
        return 0;
    const uint32_t *pt = (const uint32_t *)(uintptr_t)PTE_ADDR(pde);
    uint32_t pte = pt[(vaddr >> 12) & 1023];
    /* R3: a user access needs P|U (and W for writes) at both levels; fold the PDE's bits into the answer. */
    return (pte & ~(PTE_U | PTE_W)) | (pte & pde & (PTE_U | PTE_W));
}

void paging_map_kernel(uint32_t vaddr, uint32_t paddr, uint32_t npages)
{
    if ((vaddr | paddr) & (PAGE_SIZE - 1))
        panic("paging: unaligned kernel window 0x%08x -> 0x%08x", vaddr, paddr);
    if (vaddr < PMM_LIMIT || vaddr + npages * PAGE_SIZE < vaddr)
        panic("paging: kernel window 0x%08x inside the identity map or wrapping", vaddr);
    if (vaddr < USER_TOP && vaddr + npages * PAGE_SIZE > USER_BASE)
        panic("paging: kernel window 0x%08x overlaps the user range", vaddr);
    for (uint32_t i = 0; i < npages; i++) {
        uint32_t v = vaddr + i * PAGE_SIZE, pdi = v >> 22;
        if (!(page_dir[pdi] & PTE_P)) {
            uint32_t frame = pmm_alloc();
            if (!frame)
                panic("paging: out of frames for a kernel window table");
            memset((void *)(uintptr_t)frame, 0, PAGE_SIZE);
            page_dir[pdi] = frame | PTE_P | PTE_W;            /* supervisor only at the directory level */
        } else if (page_dir[pdi] & PTE_U) {
            panic("paging: kernel window 0x%08x in a user directory entry", v);
        }
        uint32_t *pt = (uint32_t *)(uintptr_t)PTE_ADDR(page_dir[pdi]);
        uint32_t idx = (v >> 12) & 1023;
        if (pt[idx] & PTE_P)
            panic("paging: kernel window 0x%08x already mapped", v);
        pt[idx] = (paddr + i * PAGE_SIZE) | PTE_P | PTE_W;
    }
    paging_flush_tlb();
}

void paging_flush_tlb(void)
{
    uint32_t cr3;
    __asm__ volatile("mov %%cr3, %0; mov %0, %%cr3" : "=r"(cr3) : : "memory");
}
