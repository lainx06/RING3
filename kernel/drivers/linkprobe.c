/* T-01B link smoke test: a polling COM3 (0x3E8) driver variant, built only with KTEST_LINK=1 and run with the
 * `linktest` boot option. Receives the real ring3cell firmware's STATE frames, decodes them with the kernel's
 * own COBS/CRC code, sends PING frames (and nothing else: no FAN, SERVO or ESTOP_RESET) and checks that the
 * firmware's ack_seq follows them. Prints every STATE and a summary, then exits QEMU with pass/fail.
 * COM3 is polled with IER = 0; T-04's driver handles the shared IRQ 4 (D-39a). */
#include "linkprobe.h"
#ifdef KTEST_LINK
#include "cellframe.h"
#include "io.h"
#include "kernel.h"
#include "pit.h"
#include "printk.h"
#include "qemu.h"

#define COM3_PORT 0x3E8
#define REG_DATA 0
#define REG_IER 1
#define REG_FCR 2
#define REG_LCR 3
#define REG_MCR 4
#define REG_LSR 5
#define LSR_DR 0x01
#define LSR_ERR 0x1E /* overrun, parity, framing, break */
#define LSR_THRE 0x20

#define FIRST_STATE_TICKS 300 /* 3 s at PIT_HZ = 100: the Uno resets when QEMU opens the port */
#define WANT_CONSECUTIVE 40   /* valid STATEs with contiguous seq before the PING phase (card: >= 20) */
#define COLLECT_TICKS 400     /* deadline for those, from the first valid STATE */
#define PINGS 8
#define PING_PERIOD_TICKS 25  /* 250 ms, the kernel's healthy PING rate (protocol.md) */
#define ACK_WAIT_TICKS 30     /* a STATE every 50 ms; the ack must show within 300 ms */
#define PING_SEQ0 1
#define BOOT_WINDOW_MS 3000   /* a STATE with t_ms below this comes from the boot that the port open caused */

static uint8_t rx[CELL_MAX_ENCODED];
static uint32_t rx_n;
static int rx_overflow;

static struct {
    int fresh;                     /* a STATE of the new boot has arrived */
    uint32_t stale, stale_last_tms; /* STATEs before that (sent before the reset, or no reset happened) */
    uint32_t restarts;             /* t_ms going backwards after the first fresh STATE */
    uint32_t states, rejected, lsr_err, gaps, consecutive, best_consecutive;
    uint32_t rej[CELL_RX_COUNT];
    uint32_t run_tick0, run_tms0, run_seq0, run_n; /* the current consecutive run */
    uint32_t last_tick, last_tms;
    uint16_t last_seq;
    int have_last;
    uint8_t fan_max, servo_min, servo_max;
    uint16_t ack_seq;
    uint8_t flags;
} s;

static void com3_init(void)
{
    outb(COM3_PORT + REG_IER, 0x00); /* polled: no COM3 interrupts */
    outb(COM3_PORT + REG_LCR, 0x80); /* DLAB on */
    outb(COM3_PORT + REG_DATA, 0x01); /* divisor 1 -> 115200 (QEMU sets the host tty to match) */
    outb(COM3_PORT + REG_IER, 0x00);
    outb(COM3_PORT + REG_LCR, 0x03); /* 8N1, DLAB off */
    outb(COM3_PORT + REG_FCR, 0xC7); /* FIFO on, clear both */
    /* DTR + RTS, as the host already holds them since QEMU opened the tty: no new DTR edge, so no second Uno
     * reset. OUT2 off: no interrupt routing needed for polling. */
    outb(COM3_PORT + REG_MCR, 0x03);
}

static void com3_send(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        while ((inb(COM3_PORT + REG_LSR) & LSR_THRE) == 0)
            ;
        outb(COM3_PORT + REG_DATA, p[i]);
    }
}

/* Returns 1 if the STATE counts (it belongs to the new boot). */
static int on_state(const struct cell_state *st)
{
    uint32_t now = ticks_now();
    if (!s.fresh && st->t_ms >= BOOT_WINDOW_MS) {
        s.stale++;
        s.stale_last_tms = st->t_ms;
        printk("STATE_STALE seq=%u t_ms=%u fan=%u servo=%u flags=0x%x ack=%u tick=%u\n", st->seq, st->t_ms,
               st->fan, st->servo, st->flags, st->ack_seq, now);
        return 0;
    }
    if (s.fresh && s.have_last && st->t_ms < s.last_tms)
        s.restarts++;
    s.fresh = 1;
    s.states++;
    if (s.have_last && st->seq == (uint16_t)(s.last_seq + 1)) {
        s.consecutive++;
    } else {
        if (s.have_last)
            s.gaps++;
        s.consecutive = 1;
        s.run_tick0 = now;
        s.run_tms0 = st->t_ms;
        s.run_seq0 = st->seq;
    }
    if (s.consecutive > s.best_consecutive)
        s.best_consecutive = s.consecutive;
    s.run_n = s.consecutive;
    s.have_last = 1;
    s.last_seq = st->seq;
    s.last_tick = now;
    s.last_tms = st->t_ms;
    if (st->fan > s.fan_max)
        s.fan_max = st->fan;
    if (st->servo < s.servo_min)
        s.servo_min = st->servo;
    if (st->servo > s.servo_max)
        s.servo_max = st->servo;
    s.ack_seq = st->ack_seq;
    s.flags = st->flags;
    printk("STATE seq=%u t_ms=%u fan=%u servo=%u dist=%u estop=%u flags=0x%x ack=%u tick=%u\n", st->seq, st->t_ms,
           st->fan, st->servo, st->dist_mm, st->estop, st->flags, st->ack_seq, now);
    return 1;
}

static void on_reject(enum cell_rx e)
{
    s.rejected++;
    s.rej[e]++;
    s.consecutive = 0;
    s.run_n = 0;
    s.have_last = 0;
    printk("FRAME_DROP %s tick=%u\n", cell_rx_name(e), ticks_now());
}

/* Drains COM3; returns 1 when at least one valid STATE was decoded. */
static int poll(void)
{
    int got = 0;
    for (;;) {
        uint8_t lsr = inb(COM3_PORT + REG_LSR);
        if (lsr & LSR_ERR)
            s.lsr_err++;
        if (!(lsr & LSR_DR))
            return got;
        uint8_t b = inb(COM3_PORT + REG_DATA);
        if (b != 0x00) {
            if (rx_n < sizeof rx)
                rx[rx_n++] = b;
            else
                rx_overflow = 1; /* dropped whole at the next delimiter */
            continue;
        }
        if (rx_overflow) {
            on_reject(CELL_RX_OVERSIZE);
        } else if (rx_n > 0) {
            struct cell_state st;
            enum cell_rx e = cell_state_decode(rx, rx_n, &st);
            if (e == CELL_RX_OK) {
                got |= on_state(&st);
            } else {
                on_reject(e);
            }
        } /* an empty frame (0x00 0x00) is line idle/resync, not a frame */
        rx_n = 0;
        rx_overflow = 0;
    }
}

static void __attribute__((noreturn)) finish(const char *fail)
{
    uint32_t span = s.last_tick - s.run_tick0;
    uint32_t rate_mhz = (s.run_n > 1 && span) ? (s.run_n - 1) * 100u * 1000u / span : 0;
    uint32_t dev_period_us = s.run_n > 1 ? (s.last_tms - s.run_tms0) * 1000u / (s.run_n - 1) : 0;
    printk("LINK SUMMARY stale=%u restarts=%u states=%u rejected=%u lsr_err=%u gaps=%u best_consecutive=%u final_run=%u "
           "run_ticks=%u rate_mhz=%u dev_period_us=%u fan_max=%u servo_min=%u servo_max=%u\n",
           s.stale, s.restarts, s.states, s.rejected, s.lsr_err, s.gaps, s.best_consecutive, s.run_n, span, rate_mhz, dev_period_us,
           s.fan_max, s.servo_min, s.servo_max);
    printk("LINK REJECTS cobs=%u oversize=%u short=%u len=%u crc=%u type=%u body_len=%u field=%u\n",
           s.rej[CELL_RX_COBS], s.rej[CELL_RX_OVERSIZE], s.rej[CELL_RX_SHORT], s.rej[CELL_RX_LEN],
           s.rej[CELL_RX_CRC], s.rej[CELL_RX_TYPE], s.rej[CELL_RX_BODY_LEN], s.rej[CELL_RX_FIELD]);
    if (fail)
        printk("LINK FAIL %s\n", fail);
    else
        printk("LINK PASS\n");
    qemu_exit(fail ? QEMU_EXIT_FAIL : QEMU_EXIT_PASS);
    for (;;)
        __asm__ volatile("cli; hlt");
}

void linkprobe_run(void)
{
    s.servo_min = 0xFF;
    /* `linkdelay=N`: wait N ticks before touching COM3 (shows whether the reset follows the port open or the
     * guest's own UART programming). */
    uint32_t delay = boot_opt_num("linkdelay", 0);
    for (uint32_t td = ticks_now(); ticks_now() - td < delay;)
        __asm__ volatile("hlt");
    com3_init();
    uint32_t t0 = ticks_now();
    printk("LINK com3 init port=0x3e8 baud=115200 8N1 ier=0 mcr=0x3 tick=%u\n", t0);

    /* 1. First valid STATE of the new boot within 3 s. Bytes from before the reset can still arrive (cut
     *    frames: rejected; whole frames: STATE_STALE); neither fails the run, both are counted. */
    while (!poll()) {
        if (ticks_now() - t0 > FIRST_STATE_TICKS) {
            printk("LINK no fresh STATE: stale=%u last_stale_t_ms=%u rejected=%u\n", s.stale, s.stale_last_tms,
                   s.rejected);
            finish("no valid STATE of a new boot within 3 s of COM3 init");
        }
        __asm__ volatile("pause");
    }
    uint32_t t_first = s.last_tick;
    printk("LINK first_state ticks_since_com3_init=%u ticks_since_kernel_timer_start=%u dev_seq=%u dev_t_ms=%u "
           "stale_before=%u rejected_before=%u\n",
           t_first - t0, t_first, s.last_seq, s.last_tms, s.stale, s.rejected);
    uint32_t rejected_before = s.rejected;

    /* 2. >= WANT_CONSECUTIVE valid STATEs, seq contiguous, nothing sent yet (ack_seq must stay 0). */
    while (s.consecutive < WANT_CONSECUTIVE) {
        poll();
        if (s.ack_seq != 0)
            finish("ack_seq != 0 before any kernel frame");
        if (ticks_now() - t_first > COLLECT_TICKS)
            finish("fewer than 40 consecutive valid STATE frames");
        __asm__ volatile("pause");
    }
    printk("LINK collect consecutive=%u rejected_since_first=%u flags=0x%x (LINK_LOST expected: no kernel "
           "frame yet)\n",
           s.consecutive, s.rejected - rejected_before, s.flags);

    /* 3. PING only; each must show up as the firmware's ack_seq. */
    uint32_t acked = 0;
    uint16_t ack_prev = s.ack_seq;
    for (uint32_t i = 0; i < PINGS; i++) {
        uint16_t seq = (uint16_t)(PING_SEQ0 + i);
        uint8_t wire[CELL_MAX_WIRE];
        size_t n = cell_cmd_encode(CELL_T_PING, seq, 0, 0, wire, sizeof wire);
        uint32_t tp = ticks_now();
        com3_send(wire, n);
        while (s.ack_seq != seq && ticks_now() - tp <= ACK_WAIT_TICKS) {
            poll();
            __asm__ volatile("pause");
        }
        if (s.ack_seq == seq && seq != ack_prev) {
            acked++;
            printk("LINK ping seq=%u acked after %u ticks flags=0x%x\n", seq, s.last_tick - tp, s.flags);
        } else {
            printk("LINK ping seq=%u NOT acked (ack_seq=%u)\n", seq, s.ack_seq);
        }
        ack_prev = s.ack_seq;
        while (ticks_now() - tp < PING_PERIOD_TICKS)
            poll();
    }
    printk("LINK pings sent=%u acked=%u last_ack=%u\n", PINGS, acked, s.ack_seq);

    if (s.fan_max != 0)
        finish("fan_duty != 0 in a STATE frame");
    if (acked != PINGS)
        finish("ack_seq did not follow every PING");
    if (s.run_n < WANT_CONSECUTIVE)
        finish("STATE stream broke during the PING phase");
    finish(0);
}
#else
typedef int linkprobe_not_built_in_this_configuration;
#endif
