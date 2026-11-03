/* COM3 work-cell driver: see device.h. Layout constants come from firmware/ring3cell/protocol.h through
 * cellframe.h (one source for both ends). Everything here runs either in IRQ context (receive, PING,
 * AUTO_HALT) or in main context with interrupts off (syscalls, the console reset), never both at once:
 * every transmit holds interrupts off for the whole frame. */
#include "device.h"
#include "audit.h"
#include "cellframe.h"
#include "io.h"
#include "kstring.h"
#include "link_abi.h"
#include "pic.h"
#include "pit.h"
#include "printk.h"
#include "words.h"

#define COM3_PORT 0x3E8
#define COM3_IRQ 4
#define REG_DATA 0
#define REG_IER 1
#define REG_FCR 2
#define REG_LCR 3
#define REG_MCR 4
#define REG_LSR 5
#define REG_SCR 7
#define LSR_DR 0x01
#define LSR_ERR 0x1E                                   /* overrun, parity, framing, break */
#define LSR_THRE 0x20

#define BOOT_WINDOW_MS 3000u        /* a STATE with t_ms below this comes from the boot the port open caused (D-54) */
#define SYNC_TIMEOUT_TICKS (3u * POLICY_HZ)   /* no reset happened (D-20 path): trust the stream after 3 s */
#define PING_TICKS (250u * POLICY_HZ / 1000u) /* PING every 250 ms while nothing else was sent */
#define TX_SPIN_MAX 4000000u                  /* polled TX bound per byte */

static int present;
static uint32_t init_tick;
static int synced;                 /* a frame of the current boot has arrived: commands may go out */
static int resynced;               /* the first 0x00 since init was seen: bytes before it are a cut frame */
static uint8_t rx[CELL_MAX_ENCODED];
static uint32_t rx_n;
static int rx_over;
static struct cell_state last;     /* the last accepted frame, as reported by the board */
static uint32_t last_tick;
static int have_last;
static uint16_t tx_seq;
static uint32_t last_tx_tick;
static uint32_t mute_until;
static struct {
    uint32_t resync_bytes, prev_boot, frames, old, dropped, lsr_err, tx_frames, tx_failed, pings, halts;
} cnt;

int device_present(void) { return present; }
int device_synced(void) { return present && synced; }

/* ---- transmit (polled, whole frame with interrupts off) ---------------------------------------------- */
static int tx_byte(uint8_t b)
{
    for (uint32_t spin = 0; spin < TX_SPIN_MAX; spin++) {
        if (inb(COM3_PORT + REG_LSR) & LSR_THRE) {
            outb(COM3_PORT + REG_DATA, b);
            return 0;
        }
    }
    return -1;
}

static int tx_frame(uint8_t type, const uint8_t *body, uint32_t len)
{
    if (!present)
        return -ENOSYS;
    if (!synced)
        return -ETIMEDOUT;                             /* the board's boot is not known yet: nothing goes out */
    uint8_t wire[CELL_MAX_WIRE];
    uint32_t fl = irq_save();
    int r = 0;
    if (mute_until && (int32_t)(mute_until - ticks_now()) > 0) {
        r = -ETIMEDOUT;                                /* test knob: the link watchdog check */
    } else {
        tx_seq++;
        if (tx_seq == 0)
            tx_seq = 1;                                /* ack_seq 0 means "nothing yet" on the board */
        size_t n = cell_cmd_encode(type, tx_seq, body, len, wire, sizeof wire);
        if (n == 0)
            r = -EINVAL;
        for (size_t i = 0; i < n && r == 0; i++)
            if (tx_byte(wire[i]))
                r = -ETIMEDOUT;
        if (r == 0) {
            cnt.tx_frames++;
            last_tx_tick = ticks_now();
        } else {
            cnt.tx_failed++;
        }
    }
    irq_restore(fl);
    return r;
}

int device_fan(uint32_t duty)
{
    uint8_t b = (uint8_t)duty;
    return tx_frame(CELL_T_FAN, &b, CELL_FAN_LEN);
}

int device_servo(uint32_t deg)
{
    uint8_t b = (uint8_t)deg;
    return tx_frame(CELL_T_SERVO, &b, CELL_SERVO_LEN);
}

int device_halt(void) { return device_fan(0); }

int device_lcd(uint32_t line, const char *text, uint32_t len)
{
    uint8_t b[CELL_LCD_MAX_LEN];
    if (line > 1 || len > CELL_LCD_COLS)
        return -EINVAL;
    b[0] = (uint8_t)line;
    for (uint32_t i = 0; i < len; i++) {
        if ((uint8_t)text[i] < CELL_LCD_CHAR_MIN || (uint8_t)text[i] > CELL_LCD_CHAR_MAX)
            return -EINVAL;
        b[1 + i] = (uint8_t)text[i];
    }
    return tx_frame(CELL_T_LCD, b, 1 + len);
}

static int alarm(uint8_t pattern) { return tx_frame(CELL_T_ALARM, &pattern, CELL_ALARM_LEN); }

int device_estop_reset(void) { return tx_frame(CELL_T_ESTOP_RESET, 0, 0); }

/* ---- the panel: the kernel's last decision in plain words (INV-11: no banned words) ----------------- */
/* Verdict words and reason phrases come from kernel/ui/words.c, the table the VGA screen shares (T-08). */
static void lcd_text(uint32_t line, const char *s) { device_lcd(line, s, (uint32_t)strlen(s)); }

static void put_dec(char *dst, uint32_t cap, uint32_t *n, int v)
{
    char b[12];
    int k = 0;
    uint32_t m = v < 0 ? (uint32_t)(-v) : (uint32_t)v;
    do {
        b[k++] = (char)('0' + m % 10);
        m /= 10;
    } while (m);
    if (v < 0 && *n < cap)
        dst[(*n)++] = '-';
    while (k && *n < cap)
        dst[(*n)++] = b[--k];
}

static void put_str(char *dst, uint32_t cap, uint32_t *n, const char *s)
{
    while (*s && *n < cap)
        dst[(*n)++] = *s++;
}

/* Row 0: verdict + call, e.g. "DENIED fan 100", "ALLOW servo 120", "AUTO HALT fan 60". Row 1: the reason.
 * When verdict + call do not fit the 16 columns ("CONFIRMED servo 120"), row 0 is the verdict alone and
 * row 1 the call: a number is never cut. */
void device_show(uint32_t nr, const struct pol_decision *d)
{
    if (!present || !synced)
        return;
    const char *verdict = ui_verdict_word(d->code);
    if (!verdict)
        return;
    if ((d->code == DEC_ALLOW || d->code == DEC_CONFIRM_YES) && nr != SYS_CELL_FAN && nr != SYS_CELL_SERVO &&
        nr != SYS_CELL_HALT && nr != PEND_ESTOP_RESET)
        return;                                        /* status reads and the executor's own LCD text */
    int beep = ui_verdict_adverse(d->code) ? 1 : d->code == DEC_AUTO_HALT ? (d->reason == R_ESTOP ? 2 : 1) : 0;
    char call[CELL_LCD_COLS + 1];
    uint32_t c = 0;
    switch (nr) {
    case SYS_CELL_FAN:
        put_str(call, CELL_LCD_COLS, &c, "fan ");
        put_dec(call, CELL_LCD_COLS, &c, (int)d->target);
        break;
    case SYS_CELL_SERVO:
        put_str(call, CELL_LCD_COLS, &c, "servo ");
        put_dec(call, CELL_LCD_COLS, &c, (int)d->target);
        break;
    case SYS_CELL_HALT:    put_str(call, CELL_LCD_COLS, &c, "halt"); break;
    case SYS_CELL_LCD:     put_str(call, CELL_LCD_COLS, &c, "lcd"); break;
    case SYS_CELL_STATUS:  put_str(call, CELL_LCD_COLS, &c, "status"); break;
    case PEND_ESTOP_RESET: put_str(call, CELL_LCD_COLS, &c, "reset"); break;
    default:
        if (d->code == DEC_AUTO_HALT && d->target) {
            put_str(call, CELL_LCD_COLS, &c, "fan ");
            put_dec(call, CELL_LCD_COLS, &c, (int)d->target);
        }
        break;
    }
    char row[CELL_LCD_COLS + 1];
    uint32_t n = 0;
    uint32_t vlen = (uint32_t)strlen(verdict);
    put_str(row, CELL_LCD_COLS, &n, verdict);
    if (c && vlen + 1 + c <= CELL_LCD_COLS) {
        row[n++] = ' ';
        for (uint32_t i = 0; i < c; i++)
            row[n++] = call[i];
        c = 0;
    }
    device_lcd(0, row, n);
    if (c)
        device_lcd(1, call, c);
    else
        lcd_text(1, ui_detail_words(d->code, d->reason));
    if (beep)
        alarm((uint8_t)beep);
}

void device_auto_halt(const struct pol_decision *d)
{
    if (!present)
        return;
    cnt.halts++;
    device_fan(0);                                     /* the only motion command the kernel sends on its own */
    device_show(0, d);
}

/* ---- receive (IRQ context only: IRQ 4 and the PIT tick are never concurrent) ------------------------- */
static void reject(enum cell_rx e, uint32_t len)
{
    cnt.dropped++;
    struct pol_event ev = { EV_TELEMETRY, (uint8_t)e, 0, 0, ticks_now(), 0, { (int32_t)len, 0, 0, 0 } };
    struct pol_decision d;
    audit_feed(&ev, &d);                               /* FRAME_DROP, audited */
}

static void on_state(const struct cell_state *st)
{
    uint32_t now = ticks_now();
    if (!synced) {
        /* D-54: whole frames of the board's previous boot arrive first; the new boot shows a small t_ms.
         * Without a reset (nothing within 3 s) the stream is taken as it is. Counted, not fed, not drops. */
        if (st->t_ms >= BOOT_WINDOW_MS && now - init_tick < SYNC_TIMEOUT_TICKS) {
            cnt.prev_boot++;
            return;
        }
        synced = 1;
    }
    last = *st;
    last_tick = now;
    have_last = 1;
    cnt.frames++;
    struct pol_event ev = { EV_TELEMETRY, 0, EVF_TELE_VALID | EVF_TELE_SEQ, 0, now, 0,
                            { (int32_t)st->dist_mm,
                              (int32_t)((uint32_t)st->estop | (uint32_t)st->fan << 8 |
                                        (uint32_t)st->servo << 16 | (uint32_t)st->flags << 24),
                              (int32_t)st->seq, (int32_t)st->t_ms } };
    struct pol_decision d;
    audit_feed(&ev, &d);
    if (!d.audit && d.reason == R_OLD_FRAME)
        cnt.old++;
    if (d.action == ACT_HALT)
        device_auto_halt(&d);                          /* AUTO_HALT or BOARD_BOOT: FAN 0 now, nothing else */
}

static void end_of_frame(void)
{
    uint32_t n = rx_n;
    int over = rx_over;
    rx_n = 0;
    rx_over = 0;
    if (over) {
        reject(CELL_RX_OVERSIZE, CELL_MAX_ENCODED + 1);
        return;
    }
    if (n == 0)
        return;                                        /* 0x00 0x00: line idle, not a frame */
    struct cell_state st;
    enum cell_rx e = cell_state_decode(rx, n, &st);
    if (e != CELL_RX_OK) {
        reject(e, n);
        return;
    }
    on_state(&st);
}

static void pump(void)
{
    for (;;) {
        uint8_t lsr = inb(COM3_PORT + REG_LSR);
        if (lsr & LSR_ERR)
            cnt.lsr_err++;
        if (!(lsr & LSR_DR))
            return;
        uint8_t b = inb(COM3_PORT + REG_DATA);
        if (!resynced) {
            if (b == 0x00)
                resynced = 1;                          /* D-53: the cut frame before it is not a drop */
            else
                cnt.resync_bytes++;
            continue;
        }
        if (b != 0x00) {
            if (rx_n < sizeof rx)
                rx[rx_n++] = b;
            else
                rx_over = 1;
            continue;
        }
        end_of_frame();
    }
}

void device_on_irq(struct trap_frame *f)
{
    if (!present)
        return;
    audit_irq_enter(f);
    pump();
    audit_irq_leave();
}

void device_on_tick(void)
{
    if (!present)
        return;
    pump();                                            /* backstop for a lost shared-line edge (D-39a) */
    if (synced && ticks_now() - last_tx_tick >= PING_TICKS) {
        if (tx_frame(CELL_T_PING, 0, 0) == 0)
            cnt.pings++;
    }
}

/* ---- status ----------------------------------------------------------------------------------------- */
int device_status(uint8_t out[LINK_CELLST_LEN])
{
    if (!present)
        return -ENOSYS;
    const struct pol_state *s = audit_state();
    uint32_t fl = irq_save();
    struct cell_state st = last;
    uint32_t age = have_last ? (ticks_now() - last_tick) * (1000u / POLICY_HZ) : 0xFFFFFFFFu;
    int hl = have_last;
    irq_restore(fl);
    memset(out, 0, LINK_CELLST_LEN);
    link_put_u16(out + LINK_CELLST_OFF_DIST, hl ? st.dist_mm : CELL_DIST_NO_ECHO);
    out[LINK_CELLST_OFF_FAN] = hl ? st.fan : 0;
    out[LINK_CELLST_OFF_SERVO] = hl ? st.servo : 0;
    out[LINK_CELLST_OFF_ESTOP] = hl ? st.estop : 0;
    out[LINK_CELLST_OFF_FLAGS] = hl ? st.flags : 0;
    out[LINK_CELLST_OFF_FRESH] = s->tele_valid && ticks_now() - s->tele_tick <= STALE_TICKS ? 1 : 0;
    out[LINK_CELLST_OFF_LATCHED] = s->estop_latched ? 1 : 0;
    link_put_u32(out + LINK_CELLST_OFF_AGE_MS, age);
    link_put_u16(out + LINK_CELLST_OFF_SEQ, hl ? st.seq : 0);
    out[LINK_CELLST_OFF_CMD_FAN] = s->fan_duty;
    out[LINK_CELLST_OFF_CMD_SERVO] = s->servo_deg;
    return LINK_CELLST_LEN;
}

int device_last_frame(struct cell_state *st, uint32_t *tick)
{
    uint32_t fl = irq_save();
    int h = have_last;
    if (h) {
        *st = last;
        *tick = last_tick;
    }
    irq_restore(fl);
    return h;
}

void device_print_status(void)
{
    uint32_t fl = irq_save();
    struct cell_state st = last;
    int hl = have_last;
    uint32_t lt = last_tick;
    irq_restore(fl);
    printk("cell: com3=%u synced=%u frames=%u prev_boot=%u old=%u frame_drops=%u resync_bytes=%u lsr_err=%u "
           "tx=%u tx_failed=%u pings=%u halts=%u\n",
           (unsigned)present, (unsigned)synced, cnt.frames, cnt.prev_boot, cnt.old, cnt.dropped,
           cnt.resync_bytes, cnt.lsr_err, cnt.tx_frames, cnt.tx_failed, cnt.pings, cnt.halts);
    if (hl)
        printk("cell: last seq=%u t_ms=%u fan=%u servo=%u dist=%u estop=%u flags=0x%x ack=%u age_ticks=%u\n",
               st.seq, st.t_ms, st.fan, st.servo, st.dist_mm, st.estop, st.flags, st.ack_seq,
               ticks_now() - lt);
}

#ifdef RING3_TEST
void device_mute(uint32_t ticks)
{
    uint32_t fl = irq_save();
    mute_until = ticks ? ticks_now() + ticks : 0;
    irq_restore(fl);
}
#endif

/* ---- init ------------------------------------------------------------------------------------------- */
void device_init(void)
{
    outb(COM3_PORT + REG_SCR, 0x5A);
    if (inb(COM3_PORT + REG_SCR) != 0x5A) {
        present = 0;
        return;
    }
    outb(COM3_PORT + REG_SCR, 0xA5);
    if (inb(COM3_PORT + REG_SCR) != 0xA5) {
        present = 0;
        return;
    }
    present = 1;
    outb(COM3_PORT + REG_IER, 0x00);
    outb(COM3_PORT + REG_LCR, 0x80);                   /* DLAB on */
    outb(COM3_PORT + REG_DATA, 0x01);                  /* divisor 1 -> 115200 */
    outb(COM3_PORT + REG_IER, 0x00);
    outb(COM3_PORT + REG_LCR, 0x03);                   /* 8N1, DLAB off */
    outb(COM3_PORT + REG_FCR, 0xC7);                   /* FIFO on, clear both (D-53: flush before counting) */
    /* DTR + RTS as the host already holds them (no second reset, D-54); OUT2 routes the IRQ. */
    outb(COM3_PORT + REG_MCR, 0x0B);
    while (inb(COM3_PORT + REG_LSR) & LSR_DR)
        (void)inb(COM3_PORT + REG_DATA);
    init_tick = ticks_now();
    last_tx_tick = init_tick;
    outb(COM3_PORT + REG_IER, 0x01);                   /* received data available */
    pic_unmask(COM3_IRQ);
}
