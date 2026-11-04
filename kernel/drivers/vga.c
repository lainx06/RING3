/* VGA text status screen driver: see vga.h. The snapshot is filled from read-only copies of existing state
 * (the policy state through audit_state(), the audit path's decisions, the COM3 driver's last accepted
 * frame, the frame allocator's count, COM1 output). Nothing here is an input to decide(). */
#include "vga.h"
#include "audit.h"
#include "cellframe.h"
#include "device.h"
#include "exec.h"
#include "gfx.h"
#include "gfxdev.h"
#include "io.h"
#include "kernel.h"
#include "kstring.h"
#include "pit.h"
#include "pmm.h"
#include "printk.h"
#include "screen.h"
#include "trap.h"
#include "words.h"

#define VGA_TEXT ((volatile uint16_t *)0xB8000)
#define CALIB_TICKS 50u                   /* TSC rate measured over the first 0.5 s, boot not delayed */

static int on;
/* T-18: the pixel screen (gfx=1): the same snapshot, rendered by kernel/ui/gfx.c into a back buffer; only
 * the rectangles the renderer touched are copied to the framebuffer. */
static int gfx;
static struct gfxdev gdev;
static struct gfx_font font;
static struct screen_model prev_model;
static struct gfx_rect rects[GFX_MAX_RECTS];
static uint32_t gfx_pending;              /* regions still to draw (the per-tick budget) */
static uint32_t over_2ms;
/* decisions (written by the audit path with interrupts off) */
static struct scr_decision dec[SCR_DECISIONS];
static uint32_t ndec;                     /* total noted; ring index = n % SCR_DECISIONS */
static uint8_t kill_kind;                 /* SCR_KILL_* since the last EXEC_START */
static uint32_t kill_req;
/* console window: committed rows (ring) + the line being written */
#define CON_DONE (SCR_CON_ROWS - 1)
static char con_done[CON_DONE][SCR_CON_COLS + 1];
static uint32_t con_ndone;
static char con_cur[SCR_CON_COLS + 1];
static uint32_t con_len;                  /* characters in con_cur */
static uint32_t con_logical;              /* characters in the logical line (across wraps) */
static int con_hidden;                    /* test-image TAPE / AUDIT record line: not mirrored */
/* rendering */
static struct screen_model model;
static uint16_t back[SCR_ROWS][SCR_COLS];
static uint16_t front[SCR_ROWS][SCR_COLS];
static uint32_t last_render_tick;
static int rendered_once;
static int busy;
/* measurement */
static uint32_t tsc_mhz;                  /* 0 until calibrated: no figures recorded before that */
static uint64_t cal_tsc;
static uint32_t cal_tick;
static uint32_t n_renders, max_us, sum_us, over_1ms, max_cells, max_gross_us;
#define HIST_STEP_US 100u                  /* pass durations in 100 us buckets, the last one is 4 ms and over */
#define HIST_N 41
static uint32_t hist[HIST_N];

int vga_on(void) { return on; }
int vga_gfx_on(void) { return gfx; }

void vga_init(const struct multiboot_info *mbi)
{
    int want_gfx = boot_opt_num("gfx", 0) == 1;
    if (!boot_opt("screen") && !want_gfx)
        return;
    if (want_gfx)
        gfx = gfxdev_init(mbi, &font, &gdev);   /* 0 = the text screen below, reason on COM1 */
    if (gfx)
        goto calibrate;
    /* Attribute mode control (index 0x10), bit 3 off: bit 7 of an attribute is a bright background, never
     * blink. Reading 0x3DA resets the index/data flip-flop; 0x20 keeps the palette enabled. */
    (void)inb(0x3DA);
    outb(0x3C0, 0x30);
    uint8_t mode = inb(0x3C1);
    outb(0x3C0, (uint8_t)(mode & ~0x08u));
    /* CRTC cursor start (0x0A), bit 5: cursor off. */
    outb(0x3D4, 0x0A);
    outb(0x3D5, 0x20);
calibrate:
    /* TSC rate against the PIT, for the render-time measurement only (finished in vga_service). */
    cal_tsc = rdtsc();
    cal_tick = ticks_now();
    for (int r = 0; r < SCR_ROWS; r++)
        for (int c = 0; c < SCR_COLS; c++)
            front[r][c] = 0xFFFFu;           /* nothing on the screen is ours yet: the first pass writes all */
    on = 1;
}

/* ---- inputs ----------------------------------------------------------------------------------------------- */
static int call_shown(uint32_t nr)
{
    switch (nr) {
    case SYS_CELL_STATUS:
    case SYS_CELL_FAN:
    case SYS_CELL_SERVO:
    case SYS_CELL_HALT:
    case SYS_CELL_LCD:
    case SYS_OBJ_READ:
    case SYS_OBJ_WRITE:
        return 1;
    default:
        return 0;
    }
}

void vga_note_decision(const struct pol_event *ev, const struct pol_decision *d, const struct pol_state *before)
{
    if (!on)
        return;
    if (ev->kind == EV_EXEC_START && d->code == DEC_ALLOW)
        kill_kind = SCR_KILL_NONE;
    if (d->code == DEC_LIMIT || d->code == DEC_PROC_FAULT) {
        /* the executor is being ended by the kernel (tick quota, action cap, fault) */
        kill_kind = d->code == DEC_PROC_FAULT ? SCR_KILL_FAULT : SCR_KILL_LIMIT;
        kill_req = (uint32_t)exec_last_pid();
    }
    if (!ui_verdict_word(d->code))
        return;
    uint32_t nr;
    switch (ev->kind) {
    case EV_SYSCALL:
        if (!call_shown(ev->nr) && d->code == DEC_ALLOW)
            return;                           /* console writes, link calls, uptime: not cell or object calls */
        nr = ev->nr;
        break;
    case EV_CONFIRM:
    case EV_TICK:
        nr = d->code == DEC_CONFIRM_YES || d->code == DEC_CONFIRM_NO ? before->pend_nr : SCR_NO_CALL;
        break;
    case EV_TELEMETRY:
        nr = SCR_NO_CALL;
        break;
    case EV_CONSOLE:
        if (ev->sub != CONSOLE_ESTOP_RESET)
            return;
        nr = PEND_ESTOP_RESET;
        break;
    default:
        return;                               /* EXEC_START / END, ACTION frames: the executor line shows them */
    }
    struct scr_decision *r = &dec[ndec % SCR_DECISIONS];
    r->tick = ev->tick;
    r->req = before->in_request ? (uint32_t)exec_last_pid() : 0;
    r->nr = nr;
    r->target = d->target;
    r->code = d->code;
    r->reason = d->reason;
    ndec++;
}

static void con_commit(void)
{
    con_cur[con_len] = 0;
    memcpy(con_done[con_ndone % CON_DONE], con_cur, con_len + 1);
    con_ndone++;
    con_len = 0;
}

void vga_putc(char c)
{
    if (!on)
        return;
    uint32_t fl = irq_save();
    if (c == '\n') {
        if (!con_hidden)
            con_commit();
        con_len = con_logical = 0;
        con_hidden = 0;
    } else if (c != '\r') {
        if (con_len == SCR_CON_COLS) {        /* wrap a long line onto the next row */
            if (!con_hidden)
                con_commit();
            con_len = 0;
        }
        con_cur[con_len++] = (uint8_t)c >= 32 && (uint8_t)c < 127 ? c : '?';
        con_logical++;
#ifdef RING3_TEST
        /* test images print every tape record and audit line on COM1 for the QEMU tests; the window
         * shows the console's own lines (the decisions have their own region) */
        if ((con_logical == 5 && memcmp(con_cur, "TAPE ", 5) == 0) ||
            (con_logical == 6 && memcmp(con_cur, "AUDIT ", 6) == 0))
            con_hidden = 1;
#endif
    }
    irq_restore(fl);
}

void vga_backspace(void)
{
    if (!on)
        return;
    uint32_t fl = irq_save();
    if (con_len && !con_hidden) {
        con_len--;
        con_logical--;
    }
    irq_restore(fl);
}

/* ---- snapshot + render ------------------------------------------------------------------------------------ */
static int hidden_prefix(void)
{
#ifdef RING3_TEST
    return con_len < 6 && con_logical == con_len &&
           (memcmp("TAPE ", con_cur, con_len) == 0 || memcmp("AUDIT ", con_cur, con_len) == 0) && con_len;
#else
    return 0;
#endif
}

static void snapshot(struct screen_model *m)
{
    struct cell_state st;
    uint32_t st_tick = 0;
    uint32_t fl = irq_save();
    const struct pol_state *s = audit_state();
    uint32_t now = ticks_now();
    m->now_tick = now;
    m->hz = POLICY_HZ;
    m->tainted = s->tainted;
    m->pending = s->pending;
    m->logged = s->seq;
    m->free_kib = pmm_free_frames() * (PAGE_SIZE / 1024u);
    if (kill_kind) {
        m->exec_state = SCR_EXEC_KILLED;
        m->kill_kind = kill_kind;
        m->req = kill_req;
    } else if (s->in_request) {
        m->exec_state = SCR_EXEC_RUNNING;
        m->kill_kind = SCR_KILL_NONE;
        m->req = (uint32_t)exec_last_pid();
    } else {
        m->exec_state = SCR_EXEC_IDLE;
        m->kill_kind = SCR_KILL_NONE;
        m->req = 0;
    }
    uint32_t n = ndec < SCR_DECISIONS ? ndec : SCR_DECISIONS;
    m->ndec = n;
    for (uint32_t i = 0; i < n; i++)
        m->dec[i] = dec[(ndec - n + i) % SCR_DECISIONS];
    uint32_t done = con_ndone < CON_DONE ? con_ndone : CON_DONE;
    int row = 0;
    for (uint32_t i = done; i < CON_DONE; i++)
        m->con[row++][0] = 0;
    for (uint32_t i = 0; i < done; i++)
        memcpy(m->con[row++], con_done[(con_ndone - done + i) % CON_DONE], SCR_CON_COLS + 1);
    uint32_t cl = con_hidden || hidden_prefix() ? 0 : con_len;
    memcpy(m->con[row], con_cur, cl);
    m->con[row][cl] = 0;
    int have = device_last_frame(&st, &st_tick);
    irq_restore(fl);

    m->have_tele = (uint8_t)have;
    m->fan = have ? st.fan : 0;
    m->servo = have ? st.servo : 0;
    m->dist_mm = have ? st.dist_mm : DIST_NO_ECHO;
    m->estop_pressed = have ? st.estop : 0;
    m->estop_latched = s->estop_latched || (have && (st.flags & CELL_F_ESTOP_LATCHED));
    m->tele_age_ms = have ? (now - st_tick) * (1000u / POLICY_HZ) : 0;
    if (!device_present())
        m->link = SCR_LINK_NO_CELL;
    else if (!have)
        m->link = SCR_LINK_WAITING;
    else if (now - st_tick > STALE_TICKS)
        m->link = SCR_LINK_STALE;
    else if (st.flags & CELL_F_LINK_LOST)
        m->link = SCR_LINK_LOST;
    else
        m->link = SCR_LINK_OK;
}

void vga_service(void)
{
    if (!on || busy)
        return;
    uint32_t now = ticks_now();
    if (rendered_once && now == last_render_tick)
        return;                               /* at most once per tick (100 Hz) */
    busy = 1;
    uint64_t c0 = rdtsc(), i0 = irq_cycles_total();
    if (!tsc_mhz && now - cal_tick >= CALIB_TICKS) {
        tsc_mhz = (uint32_t)(c0 - cal_tsc) / ((now - cal_tick) * (1000000u / POLICY_HZ));
        if (!tsc_mhz)
            tsc_mhz = 1;
    }
    snapshot(&model);
    uint32_t cells = 0;
    if (gfx) {
        uint32_t n = gfx_render(&model, rendered_once ? &prev_model : 0, &font, gdev.back, rects, &gfx_pending,
                                GFX_TICK_BUDGET);
        for (uint32_t i = 0; i < n && i < GFX_MAX_RECTS; i++)
            cells += gfxdev_copy(&gdev, rects[i].x, rects[i].y, rects[i].w, rects[i].h);
        prev_model = model;
    } else {
        screen_render(&model, back);
        for (int r = 0; r < SCR_ROWS; r++)
            for (int c = 0; c < SCR_COLS; c++)
                if (back[r][c] != front[r][c]) {
                    front[r][c] = back[r][c];
                    VGA_TEXT[r * SCR_COLS + c] = back[r][c];
                    cells++;
                }
    }
    uint64_t c1 = rdtsc();
    /* the pass's own work: interrupts that landed inside it (COM3 frames, the tick) are handler time, not
     * render time, so they are taken out; the gross figure is kept as well */
    uint32_t gross = (uint32_t)(c1 - c0), cycles = gross - (uint32_t)(irq_cycles_total() - i0);
    /* the first pass writes the whole screen once; the per-tick figures are the passes after it */
    if (rendered_once && tsc_mhz) {
        uint32_t us = cycles / tsc_mhz, gross_us = gross / tsc_mhz;
        if (gross_us > max_gross_us)
            max_gross_us = gross_us;
        n_renders++;
        sum_us += us;
        if (us > max_us)
            max_us = us;
        if (us >= 1000u)
            over_1ms++;
        if (us >= 2000u)
            over_2ms++;
        hist[us / HIST_STEP_US < HIST_N - 1 ? us / HIST_STEP_US : HIST_N - 1]++;
        if (cells > max_cells)
            max_cells = cells;
    }
    last_render_tick = now;
    rendered_once = 1;
    busy = 0;
}

void vga_print_stats(void)
{
    if (!on) {
        printk("screen: off (boot option `screen` not given)\n");
        return;
    }
    printk("screen: renders=%u avg_us=%u max_us=%u over_1ms=%u max_cells_changed=%u tsc_mhz=%u gfx=%u over_2ms=%u"
           " max_gross_us=%u\n",
           n_renders, n_renders ? sum_us / n_renders : 0, max_us, over_1ms, max_cells, tsc_mhz, gfx, over_2ms,
           max_gross_us);
    printk("screen-hist: step_us=%u", HIST_STEP_US);     /* the distribution: p99 and the like come from here */
    for (uint32_t i = 0; i < HIST_N; i++)
        printk("%s%u", i ? "," : " ", hist[i]);
    printk("\n");
}

void vga_print_font(void)
{
    if (!gfx) {
        printk("gfxfont: the pixel screen is off (no glyphs read)\n");
        return;
    }
    static const char hex[] = "0123456789abcdef";
    for (uint32_t c = 0; c < 256; c++) {
        char line[40];
        line[0] = 'F';
        line[1] = ' ';
        line[2] = hex[c >> 4];
        line[3] = hex[c & 15];
        line[4] = ' ';
        for (uint32_t r = 0; r < 16; r++) {
            line[5 + 2 * r] = hex[font.rows[c][r] >> 4];
            line[6 + 2 * r] = hex[font.rows[c][r] & 15];
        }
        line[37] = '\n';
        line[38] = 0;
        printk("%s", line);
    }
}
