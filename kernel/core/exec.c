/* One ring-3 program at a time, run from the kernel loop (D-26). Fresh zeroed frames per program, TLB flush,
 * iret entry with IOPL = 0; faults, LIMIT and exit all return to the context saved here (D-41b, D-41c).
 * Request boundaries are policy events (R7); the tick quota is a policy decision (R2). */
#include "exec.h"
#include "agentlink.h"
#include "audit.h"
#include "gdt.h"
#include "io.h"
#include "kctx.h"
#include "kstring.h"
#include "paging.h"
#include "panic.h"
#include "pic.h"
#include "pit.h"
#include "pmm.h"
#include "printk.h"

void user_enter(uint32_t entry, uint32_t user_esp) __attribute__((noreturn));

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
#define STACK_CANARY 0x5AC0FFEEu

static struct {
    int active;
    int pid;
    const char *name;
    uint32_t start_tick;
    uint32_t frames[MAX_PROG_PAGES + STACK_PAGES];
    uint32_t vaddrs[MAX_PROG_PAGES + STACK_PAGES];
    uint32_t nframes;
    struct exec_outcome out;
    struct kctx loop;
    volatile int kill_pending;
} cur;

static uint32_t trap_stack[8192 / 4] __attribute__((aligned(16)));   /* TSS.esp0: its own stack (R9) */
static int next_pid;
static uint32_t q_action = QUOTA_ACTION_TICKS, q_request = QUOTA_REQUEST_TICKS,
                q_confirm = CONFIRM_TIMEOUT_TICKS;

int exec_current_pid(void) { return cur.active ? cur.pid : 0; }
const char *exec_current_name(void) { return cur.active ? cur.name : "kernel"; }
int exec_kill_pending(void) { return cur.active && cur.kill_pending; }

void exec_flag_kill(void)
{
    if (cur.active)
        cur.kill_pending = 1;                   /* carried out at the executor's syscall return (F1, D-50) */
}

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

static struct exec_outcome exec_run(const struct uprog *p, int pid, const char *arg)
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
    cur.name = p->name;

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
    /* The request text (if any) goes into the top stack page at USER_ARG_ADDR, NUL-terminated and bounded;
     * the program's stack starts below it. Written through the physical frame like the image above. */
    {
        uint8_t *top = (uint8_t *)(uintptr_t)cur.frames[cur.nframes - 1] + (USER_ARG_ADDR & (PAGE_SIZE - 1));
        uint32_t n = 0;
        if (arg) {
            while (n < USER_ARG_MAX - 1 && arg[n]) {
                top[n] = (uint8_t)arg[n];
                n++;
            }
        }
        top[n] = 0;
    }
    paging_flush_tlb();

    trap_stack[0] = STACK_CANARY;
    tss_set_kernel_stack((uint32_t)(uintptr_t)trap_stack + sizeof trap_stack);
    cur.start_tick = ticks_now();

    if (kctx_save(&cur.loop) == 0) {
        user_enter(USER_BASE + h->entry_off, USER_ARG_ADDR - 16);
        /* not reached */
    }
    /* Resumed from exec_on_exit / exec_on_user_fault / exec_kill_limit / exec_abandon_limit, on the
     * kernel-loop stack, IF = 0. */
    sti();
    if (trap_stack[0] != STACK_CANARY)
        panic("exec: trap stack overflow");
    cur.active = 0;
    cur.out.ticks_used = ticks_now() - cur.start_tick;
    teardown();
    return cur.out;
}

static void feed(const struct pol_event *ev, struct pol_decision *d)
{
    cli();
    audit_feed(ev, d);
    sti();
    audit_flush(1);
}

struct exec_outcome exec_request(const struct uprog *p, const char *arg)
{
    int pid = ++next_pid;
    struct pol_decision d;
    struct pol_event ev = { EV_EXEC_START, p->prog, 0, 0, ticks_now(), policy_caps_for_prog(p->prog),
                            { (int32_t)q_action, (int32_t)q_request, (int32_t)q_confirm, 0 } };
    feed(&ev, &d);
    if (d.code != DEC_ALLOW) {
        /* F8 (D-50): a zero or negative quota is refused by decide(); nothing runs and no state changed. A
         * refusal while a request is running would be a kernel bug (one executor at a time). */
        if (audit_state()->in_request)
            panic("exec: EXEC_START refused (%s) with a request running", policy_reason_name(d.reason));
        next_pid--;
        printk("RUN REFUSED name=%s reason=%s\n", p->name, policy_reason_name(d.reason));
        struct exec_outcome refused = { EXEC_REFUSED, 0, 0, 0, 0 };
        return refused;
    }
    printk("RUN pid=%d name=%s prog=%u caps=0x%x uptime=%u\n", pid, p->name, p->prog,
           audit_state()->caps, ticks_now());
    agentlink_flush_queue();                    /* D-53: nothing from before this request reaches it */
    struct exec_outcome o = exec_run(p, pid, arg);
    const char *kind = o.kind == EXEC_EXIT ? "EXIT" : o.kind == EXEC_FAULT ? "FAULT" : "LIMIT";
    if (o.kind == EXEC_LIMIT)
        printk("LIMIT pid=%d ticks=%u\n", pid, o.ticks_used);
    agentlink_flush_queue();                    /* D-53: a late frame never reaches the next request */
    struct pol_event end = { EV_EXEC_END, (uint8_t)o.kind, 0, 0, ticks_now(), 0,
                             { o.kind == EXEC_EXIT ? o.exit_code : o.kind == EXEC_FAULT ? (int32_t)o.vector
                                                                                        : (int32_t)o.ticks_used,
                               0, 0, 0 } };
    feed(&end, &d);
    printk("END pid=%d %s ticks=%u uptime=%u\n", pid, kind, o.ticks_used, ticks_now());
    return o;
}

void exec_action_boundary(void)
{
    struct pol_event ev = { EV_ACTION_BOUNDARY, 0, 0, 0, ticks_now(), 0, { 0, 0, 0, 0 } };
    struct pol_decision d;
    uint32_t fl = irq_save();
    audit_feed(&ev, &d);
    irq_restore(fl);
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
    printk("PROC_FAULT pid=%d vector=%u eip=0x%08x err=0x%x cs=0x%x cr2=0x%08x\n", cur.pid, f->vector, f->eip,
           f->err, f->cs, f->vector == 14 ? read_cr2() : 0);
    kctx_resume(&cur.loop, 1);
}

void exec_kill_limit(struct trap_frame *f)
{
    if (!cur.active)
        return;
    if (!trap_from_user(f)) {
        cur.kill_pending = 1;                   /* a kernel wait on the program's behalf will abandon it */
        return;
    }
    pic_eoi(0);                                 /* EOI first: we won't return through the IRQ frame (D-41b) */
    cur.out.kind = EXEC_LIMIT;
    kctx_resume(&cur.loop, 1);
}

void exec_abandon_limit(void)
{
    cli();
    if (!cur.active)
        panic("exec_abandon_limit with no active program");
    cur.out.kind = EXEC_LIMIT;
    kctx_resume(&cur.loop, 1);
}

#define UPROG(sym) extern const uint8_t uprog_##sym##_start[], uprog_##sym##_end[];
#define ENTRY(sym, prof) { #sym, uprog_##sym##_start, 0, prof }
#define LEN(sym) (uint32_t)(uprog_##sym##_end - uprog_##sym##_start)

/* The production executor (ARCHITECTURE §5), embedded in every image. Test images add two builds of the
 * same source: AGENTSH_TEST (a crash op for the fault tests) and AGENTSH_TEST + AGENTSH_NO_CAP (the kernel's
 * own 8-action cap is what ends it, A7). */
UPROG(agentsh)
static struct uprog agent_progs[3] = { ENTRY(agentsh, PROG_AGENTSH) };
#ifdef RING3_TEST
UPROG(agentsh_test) UPROG(agentsh_nocap)
#endif

int exec_request_agent(const char *text, int variant)
{
    if (!agent_progs[0].image_len) {
        agent_progs[0].image_len = LEN(agentsh);
#ifdef RING3_TEST
        struct uprog t = ENTRY(agentsh_test, PROG_AGENTSH), n = ENTRY(agentsh_nocap, PROG_AGENTSH);
        t.image_len = LEN(agentsh_test);
        n.image_len = LEN(agentsh_nocap);
        agent_progs[1] = t;
        agent_progs[2] = n;
#endif
    }
    if (variant < 0 || variant > 2 || !agent_progs[variant].image_len)
        return 0;
    struct exec_outcome o = exec_request(&agent_progs[variant], text);
    return o.kind == EXEC_REFUSED ? 0 : 1;
}

#ifdef RING3_TEST
UPROG(hello) UPROG(kwrite) UPROG(ticks) UPROG(priv) UPROG(spin) UPROG(badptr) UPROG(null) UPROG(hlt)
UPROG(ud) UPROG(div0) UPROG(stack) UPROG(dflag) UPROG(fuzz) UPROG(taint) UPROG(confirmw) UPROG(nocap)
UPROG(objs) UPROG(fan) UPROG(multi) UPROG(lcd) UPROG(audspin)
static struct uprog progs[] = {
    ENTRY(hello, PROG_TEST_CONSOLE), ENTRY(kwrite, PROG_TEST_CONSOLE), ENTRY(ticks, PROG_TEST_CONSOLE),
    ENTRY(priv, PROG_TEST_CONSOLE),  ENTRY(spin, PROG_TEST_CONSOLE),   ENTRY(badptr, PROG_TEST_CONSOLE),
    ENTRY(null, PROG_TEST_CONSOLE),  ENTRY(hlt, PROG_TEST_CONSOLE),    ENTRY(ud, PROG_TEST_CONSOLE),
    ENTRY(div0, PROG_TEST_CONSOLE),  ENTRY(stack, PROG_TEST_CONSOLE),  ENTRY(dflag, PROG_TEST_CONSOLE),
    ENTRY(fuzz, PROG_TEST_CONSOLE),  ENTRY(taint, PROG_TEST_OBJ),      ENTRY(confirmw, PROG_TEST_OBJ),
    ENTRY(nocap, PROG_TEST_NOCAP),   ENTRY(objs, PROG_TEST_OBJ),       ENTRY(fan, PROG_TEST_FULL),
    ENTRY(multi, PROG_TEST_CONSOLE), ENTRY(lcd, PROG_TEST_FULL),     ENTRY(audspin, PROG_TEST_CONSOLE),
};

static void fill_lengths(void)
{
    static int done;
    if (done)
        return;
    done = 1;
    uint32_t lens[] = { LEN(hello), LEN(kwrite), LEN(ticks), LEN(priv), LEN(spin), LEN(badptr), LEN(null),
                        LEN(hlt), LEN(ud), LEN(div0), LEN(stack), LEN(dflag), LEN(fuzz), LEN(taint),
                        LEN(confirmw), LEN(nocap), LEN(objs), LEN(fan), LEN(multi), LEN(lcd), LEN(audspin) };
    for (unsigned i = 0; i < sizeof progs / sizeof progs[0]; i++)
        progs[i].image_len = lens[i];
}

int exec_request_by_name(const char *name)
{
    fill_lengths();
    for (unsigned i = 0; i < sizeof progs / sizeof progs[0]; i++) {
        if (strncmp(name, progs[i].name, 32) == 0) {
            static int first = 1;
            uint32_t before = pmm_free_frames();
            struct exec_outcome o = exec_request(&progs[i], 0);
            if (o.kind == EXEC_REFUSED)
                return 1;                        /* nothing was mapped */
            /* The first request allocates the (kept) user page tables; after that every request must give
             * back exactly the frames it took. */
            if (!first && pmm_free_frames() != before)
                panic("exec: frame leak after a request: %u free, expected %u", pmm_free_frames(), before);
            first = 0;
            return 1;
        }
    }
    return 0;
}

void exec_set_quotas(uint32_t action, uint32_t request, uint32_t confirm)
{
    q_action = action;
    q_request = request;
    q_confirm = confirm;
}

void exec_inject_telemetry(int32_t distance_mm, int32_t estop, int valid)
{
    struct pol_event ev = { EV_TELEMETRY, 0, valid ? EVF_TELE_VALID : 0, 0, ticks_now(), 0,
                            { distance_mm, estop, 0, 0 } };
    struct pol_decision d;
    uint32_t fl = irq_save();
    audit_feed(&ev, &d);
    irq_restore(fl);
}
#endif
