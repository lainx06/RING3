/* One ring-3 program at a time, run from the kernel loop (D-26). Fresh zeroed frames per program, TLB flush,
 * iret entry with IOPL = 0; faults, LIMIT and exit all return to the context saved here (D-41b, D-41c). */
#include "exec.h"
#include "gdt.h"
#include "io.h"
#include "kstring.h"
#include "paging.h"
#include "panic.h"
#include "pic.h"
#include "pit.h"
#include "pmm.h"
#include "printk.h"

struct kctx {
    uint32_t esp, ebp, ebx, esi, edi, eip;
};
void user_enter(uint32_t entry, uint32_t user_esp) __attribute__((noreturn));
int kctx_save(struct kctx *c) __attribute__((returns_twice));
void kctx_resume(struct kctx *c, int val) __attribute__((noreturn));

/* Flat image header written by user/crt0.S (sizes come from user/user.ld symbols, D-41d). */
struct uprog_header {
    uint32_t magic;          /* "RNG3" */
    uint32_t ro_len;         /* code + rodata, page-aligned */
    uint32_t mem_len;        /* code + rodata + data + bss, page-aligned */
    uint32_t entry_off;      /* offset of _start from USER_BASE */
};
#define UPROG_MAGIC 0x33474E52u
#define MAX_PROG_PAGES 64u                      /* 256 KiB per program (prov.) */
#define STACK_PAGES (USER_STACK_SIZE / PAGE_SIZE)

static struct {
    int active;
    int pid;
    uint32_t start_tick;
    uint32_t frames[MAX_PROG_PAGES + STACK_PAGES];
    uint32_t vaddrs[MAX_PROG_PAGES + STACK_PAGES];
    uint32_t nframes;
    struct exec_outcome out;
    struct kctx loop;
} cur;

static uint8_t trap_stack[8192] __attribute__((aligned(16)));

int exec_current_pid(void) { return cur.active ? cur.pid : 0; }

static void map_fresh(uint32_t vaddr, int writable)
{
    uint32_t f = pmm_alloc();
    if (!f)
        panic("exec: out of frames");
    memset((void *)(uintptr_t)f, 0, PAGE_SIZE);             /* fresh, zeroed (D-41c) */
    paging_map_user(vaddr, f, writable);
    cur.frames[cur.nframes] = f;
    cur.vaddrs[cur.nframes] = vaddr;
    cur.nframes++;
}

static void teardown(void)
{
    for (uint32_t i = 0; i < cur.nframes; i++) {
        uint32_t f = paging_unmap_user(cur.vaddrs[i]);
        if (f != cur.frames[i])
            panic("exec: mapping mismatch at 0x%08x", cur.vaddrs[i]);
        pmm_free(f);
    }
    cur.nframes = 0;
    paging_flush_tlb();
}

struct exec_outcome exec_run(const struct uprog *p, int pid)
{
    const struct uprog_header *h = (const struct uprog_header *)p->image;
    if (p->image_len < sizeof *h || h->magic != UPROG_MAGIC)
        panic("exec: %s has no valid header", p->name);
    if ((h->ro_len | h->mem_len) & (PAGE_SIZE - 1) || h->ro_len == 0 || h->mem_len < h->ro_len ||
        h->mem_len / PAGE_SIZE > MAX_PROG_PAGES || p->image_len > h->mem_len || h->entry_off >= h->ro_len)
        panic("exec: %s has a bad layout (ro=%u mem=%u img=%u)", p->name, h->ro_len, h->mem_len, p->image_len);

    memset(&cur, 0, sizeof cur);
    cur.active = 1;
    cur.pid = pid;

    /* Code + rodata read-only, data + bss writable, stack writable, guard page below the stack unmapped. */
    for (uint32_t off = 0; off < h->mem_len; off += PAGE_SIZE)
        map_fresh(USER_BASE + off, off >= h->ro_len);
    for (uint32_t i = 0; i < STACK_PAGES; i++)
        map_fresh(USER_STACK_TOP - USER_STACK_SIZE + i * PAGE_SIZE, 1);
    /* Copy the image through the physical (identity-mapped) addresses: the user pages are read-only to the
     * kernel too, because CR0.WP is set. */
    for (uint32_t off = 0; off < p->image_len; off += PAGE_SIZE) {
        uint32_t n = p->image_len - off < PAGE_SIZE ? p->image_len - off : PAGE_SIZE;
        memcpy((void *)(uintptr_t)cur.frames[off / PAGE_SIZE], p->image + off, n);
    }
    paging_flush_tlb();

    tss_set_kernel_stack((uint32_t)(uintptr_t)trap_stack + sizeof trap_stack);
    cur.start_tick = ticks_now();

    if (kctx_save(&cur.loop) == 0) {
        user_enter(USER_BASE + h->entry_off, USER_STACK_TOP - 16);
        /* not reached */
    }
    /* Resumed from exec_on_exit / exec_on_user_fault / exec_on_tick_user, on the kernel-loop stack, IF = 0. */
    sti();
    cur.active = 0;
    cur.out.ticks_used = ticks_now() - cur.start_tick;
    teardown();
    return cur.out;
}

void exec_on_exit(int code)
{
    if (!cur.active)
        panic("exec_on_exit with no active program");
    cur.out.kind = EXEC_EXIT;
    cur.out.exit_code = code;
    printk("EXIT pid=%d code=%d\n", cur.pid, code);
    kctx_resume(&cur.loop, 1);
}

void exec_on_user_fault(struct trap_frame *f)
{
    if (!cur.active)
        panic_trap(f);
    cur.out.kind = EXEC_FAULT;
    cur.out.vector = f->vector;
    cur.out.eip = f->eip;
    printk("PROC_FAULT pid=%d vector=%u eip=0x%08x err=0x%x cs=0x%x\n", cur.pid, f->vector, f->eip, f->err, f->cs);
    kctx_resume(&cur.loop, 1);
}

void exec_on_tick_user(struct trap_frame *f)
{
    (void)f;
    if (!cur.active)
        return;
    uint32_t used = ticks_now() - cur.start_tick;
    if (used < TICK_BUDGET)
        return;
    pic_eoi(0);                                  /* EOI first: we won't return through the IRQ frame (D-41b) */
    cur.out.kind = EXEC_LIMIT;
    printk("LIMIT pid=%d ticks=%u\n", cur.pid, used);
    kctx_resume(&cur.loop, 1);
}
