/* COM2 link to the bridge: see agentlink.h. Sizes come from abi/link_abi.h (D-52); the policy events from
 * kernel/policy/policy.h (D-53). No ACTION/TEXT/FINAL/STOP body byte is ever read here (INV-02). */
#include "agentlink.h"
#include "audit.h"
#include "cobs.h"
#include "crc16.h"
#include "exec.h"
#include "io.h"
#include "kstring.h"
#include "link_abi.h"
#include "pic.h"
#include "pit.h"
#include "printk.h"

#define COM2_PORT 0x2F8
#define COM2_IRQ 3
#define REG_DATA 0
#define REG_IER 1
#define REG_FCR 2
#define REG_LCR 3
#define REG_MCR 4
#define REG_LSR 5
#define REG_SCR 7
#define LSR_DR 0x01
#define LSR_THRE 0x20

#define RX_RING 4096u                              /* (prov.) bytes between IRQ and main context */
#define QUEUE_SLOTS 8u                             /* (prov.) accepted bridge frames waiting for ring 3 */
#define QUEUE_BODY_MAX LINK_FINAL_MAX              /* 201: the largest bridge->kernel body */
#define HELLO_INTERVAL (5u * POLICY_HZ)            /* D-53: re-send HELLO every 5 s until the bridge speaks */
#define HEARTBEAT_INTERVAL POLICY_HZ               /* 1 s */
#define TX_SPIN_MAX 4000000u                       /* polled TX bound per byte; past it the frame is dropped */

_Static_assert(LINK_EVENT_LEN == sizeof(struct pol_event), "EVENT frame is struct pol_event byte for byte");

static int present;
static volatile uint8_t rx[RX_RING];
static volatile uint32_t rx_head, rx_tail;         /* head: IRQ writes; tail: main context reads */
static volatile int rx_stalled;                    /* IRQ 3 masked because the ring was full */
static uint8_t raw[LINK_MAX_ENCODED];
static uint32_t raw_len;
static int raw_over;
static uint32_t frames_dropped, frames_ok, tx_frames, tx_dropped;
static uint16_t tx_seq;
static int connected;
static uint32_t last_hello, last_hb;

struct qframe {
    uint8_t type;
    uint16_t seq, len;
    uint8_t body[QUEUE_BODY_MAX];
};
static struct qframe queue[QUEUE_SLOTS];
static uint32_t q_head, q_tail;

int agentlink_present(void) { return present; }
int agentlink_connected(void) { return connected; }
uint32_t agentlink_frames_dropped(void) { return frames_dropped; }
int agentlink_queued(void) { return (int)(q_head - q_tail); }

/* ---- transmit (polled, whole frame with interrupts off) ---------------------------------------------- */
static int tx_byte(uint8_t b)
{
    for (uint32_t spin = 0; spin < TX_SPIN_MAX; spin++) {
        if (inb(COM2_PORT + REG_LSR) & LSR_THRE) {
            outb(COM2_PORT + REG_DATA, b);
            return 0;
        }
    }
    return -1;
}

static uint8_t tx_dec[LINK_MAX_DECODED];
static uint8_t tx_enc[LINK_MAX_ENCODED];

int agentlink_send(uint8_t type, const uint8_t *body, uint32_t len)
{
    if (!present)
        return -ENOSYS;
    if (len > LINK_MAX_BODY)
        return -EINVAL;
    uint32_t fl = irq_save();                      /* the static buffers and the UART are ours until restore */
    tx_dec[LINK_OFF_TYPE] = type;
    link_put_u16(tx_dec + LINK_OFF_SEQ, tx_seq);
    link_put_u16(tx_dec + LINK_OFF_LEN, (uint16_t)len);
    memcpy(tx_dec + LINK_OFF_BODY, body, len);
    link_put_u16(tx_dec + LINK_HDR_LEN + len, crc16_ccitt_false(tx_dec, LINK_HDR_LEN + len));
    size_t e = cobs_encode(tx_dec, LINK_MIN_DECODED + len, tx_enc, sizeof tx_enc);
    int r = 0;
    if (e == 0) {
        r = -EINVAL;
    } else {
        tx_seq++;
        for (size_t i = 0; i < e && r == 0; i++)
            if (tx_byte(tx_enc[i]))
                r = -ETIMEDOUT;
        if (r == 0 && tx_byte(LINK_DELIM))
            r = -ETIMEDOUT;
        if (r == 0)
            tx_frames++;
        else
            tx_dropped++;
    }
    irq_restore(fl);
    return r;
}

static void send_hello(void)
{
    uint8_t b[LINK_HELLO_LEN];
    memset(b, 0, sizeof b);
    link_put_u16(b + LINK_HELLO_OFF_ABI, LINK_ABI_VERSION);
    b[LINK_HELLO_OFF_VARIANT] = policy_is_permissive() ? LINK_VARIANT_PERMISSIVE : LINK_VARIANT_DEFAULT;
    const char *build = BUILD_HASH;
    for (uint32_t i = 0; i < LINK_HELLO_BUILD_LEN && build[i]; i++)
        b[LINK_HELLO_OFF_BUILD + i] = (uint8_t)build[i];
    const char *hex = audit_policy_hash();         /* 64 hex chars; the first 16 bytes travel */
    for (uint32_t i = 0; i < LINK_HELLO_POLICY_LEN && hex[2 * i] && hex[2 * i + 1]; i++) {
        uint8_t v = 0;
        for (int k = 0; k < 2; k++) {
            char c = hex[2 * i + k];
            uint8_t d = c >= 'a' ? (uint8_t)(c - 'a' + 10) : c >= 'A' ? (uint8_t)(c - 'A' + 10) : (uint8_t)(c - '0');
            v = (uint8_t)(v << 4 | d);
        }
        b[LINK_HELLO_OFF_POLICY + i] = v;
    }
    last_hello = ticks_now();
    agentlink_send(LINK_T_HELLO, b, sizeof b);
}

static void send_heartbeat(void)
{
    uint8_t b[LINK_HEARTBEAT_LEN];
    memset(b, 0, sizeof b);
    link_put_u32(b + LINK_HB_OFF_TICK, ticks_now());
    link_put_u32(b + LINK_HB_OFF_DROPPED, frames_dropped);
    b[LINK_HB_OFF_PROCS] = exec_current_pid() ? 1 : 0;
    last_hb = ticks_now();
    agentlink_send(LINK_T_HEARTBEAT, b, sizeof b);
}

void agentlink_send_event(const struct pol_event *ev)
{
    uint8_t b[LINK_EVENT_LEN];
    b[LINK_EV_OFF_KIND] = ev->kind;
    b[LINK_EV_OFF_SUB] = ev->sub;
    b[LINK_EV_OFF_FLAGS] = ev->flags;
    b[LINK_EV_OFF_RSV] = 0;
    link_put_u32(b + LINK_EV_OFF_TICK, ev->tick);
    link_put_u32(b + LINK_EV_OFF_NR, ev->nr);
    for (int i = 0; i < LINK_EV_NARGS; i++)
        link_put_u32(b + LINK_EV_OFF_A0 + 4 * i, (uint32_t)ev->a[i]);
    agentlink_send(LINK_T_EVENT, b, sizeof b);
}

void agentlink_send_audit(uint32_t seq, const struct pol_event *ev, const struct pol_decision *d,
                          int32_t result, uint8_t tainted)
{
    uint8_t b[LINK_AUDIT_LEN];
    memset(b, 0, sizeof b);
    link_put_u32(b + LINK_AUD_OFF_SEQ, seq);
    link_put_u32(b + LINK_AUD_OFF_TICK, ev->tick);
    b[LINK_AUD_OFF_KIND] = ev->kind;
    b[LINK_AUD_OFF_NR] = ev->kind == EV_SYSCALL ? (uint8_t)ev->nr : 0;
    b[LINK_AUD_OFF_CODE] = d->code;
    b[LINK_AUD_OFF_REASON] = d->reason;
    link_put_u32(b + LINK_AUD_OFF_TARGET, d->target);
    link_put_u32(b + LINK_AUD_OFF_RESULT, (uint32_t)result);
    b[LINK_AUD_OFF_TAINTED] = tainted ? 1 : 0;
    b[LINK_AUD_OFF_FLAGS] = policy_is_permissive() ? LINK_AUDF_PERMISSIVE : 0;
    agentlink_send(LINK_T_AUDIT, b, sizeof b);
}

/* ---- receive: IRQ side ------------------------------------------------------------------------------ */
void agentlink_on_irq(void)
{
    if (!present)
        return;
    while (inb(COM2_PORT + REG_LSR) & LSR_DR) {
        uint32_t next = (rx_head + 1) % RX_RING;
        if (next == rx_tail) {
            /* Ring full: leave the byte in the UART FIFO and mask the line. QEMU stops feeding the FIFO
             * when it is full (back-pressure to the socket), so nothing is lost; agentlink_poll() unmasks
             * once the ring is drained (the level-triggered interrupt then fires again). */
            pic_mask(COM2_IRQ);
            rx_stalled = 1;
            return;
        }
        rx[rx_head] = inb(COM2_PORT + REG_DATA);
        rx_head = next;
    }
}

/* ---- receive: main context -------------------------------------------------------------------------- */
static void feed(struct pol_event *ev)
{
    struct pol_decision d;
    uint32_t fl = irq_save();
    audit_feed(ev, &d);
    irq_restore(fl);
    if (d.action == ACT_KILL)
        exec_flag_kill();                          /* carried out when the executor's syscall returns (F1) */
}

static void drop(uint8_t sub, int32_t len)
{
    frames_dropped++;
    struct pol_event ev = { EV_LINK_DROP, sub, 0, 0, ticks_now(), 0, { len, 0, 0, 0 } };
    feed(&ev);
}

static int body_bounds_ok(uint8_t type, uint16_t len)
{
    switch (type) {
    case LINK_T_ACTION: return len == LINK_ACTION_LEN;
    case LINK_T_TEXT:   return len >= LINK_TEXT_MIN && len <= LINK_TEXT_MAX;
    case LINK_T_FINAL:  return len >= LINK_FINAL_MIN && len <= LINK_FINAL_MAX;
    case LINK_T_STOP:   return len == LINK_STOP_LEN;
    default:            return -1;                 /* not a bridge->kernel type */
    }
}

static uint8_t dec[LINK_MAX_ENCODED];              /* > LINK_MAX_DECODED so an oversize decode is visible */

static void end_of_frame(void)
{
    uint32_t n = raw_len;
    raw_len = 0;
    if (raw_over) {
        raw_over = 0;
        drop(LINKDROP_OVERSIZE, (int32_t)LINK_MAX_ENCODED + 1);
        return;
    }
    if (n == 0)
        return;                                    /* back-to-back delimiters: a resync, not a frame */
    size_t m = cobs_decode(raw, n, dec, sizeof dec);
    if (m == (size_t)-1) {
        drop(LINKDROP_COBS, (int32_t)n);
        return;
    }
    if (m > LINK_MAX_DECODED) {
        drop(LINKDROP_OVERSIZE, (int32_t)m);
        return;
    }
    if (m < LINK_MIN_DECODED) {
        drop(LINKDROP_LEN, (int32_t)m);
        return;
    }
    uint16_t len = link_get_u16(dec + LINK_OFF_LEN);
    if (m != (size_t)LINK_MIN_DECODED + len) {
        drop(LINKDROP_LEN, (int32_t)m);
        return;
    }
    if (crc16_ccitt_false(dec, LINK_HDR_LEN + len) != link_get_u16(dec + LINK_HDR_LEN + len)) {
        drop(LINKDROP_CRC, (int32_t)m);
        return;
    }
    uint8_t type = dec[LINK_OFF_TYPE];
    int ok = body_bounds_ok(type, len);
    if (ok < 0) {
        drop(LINKDROP_TYPE, type);
        return;
    }
    if (!ok) {
        drop(LINKDROP_BODY_LEN, len);
        return;
    }
    /* A valid bridge frame. Queue it for ring 3 without looking inside. */
    connected = 1;
    if (q_head - q_tail >= QUEUE_SLOTS) {
        drop(LINKDROP_QUEUE_FULL, type);
        return;
    }
    frames_ok++;
    struct qframe *q = &queue[q_head % QUEUE_SLOTS];
    q->type = type;
    q->seq = link_get_u16(dec + LINK_OFF_SEQ);
    q->len = len;
    memcpy(q->body, dec + LINK_OFF_BODY, len);
    q_head++;
    if (type == LINK_T_ACTION) {
        struct pol_event ev = { EV_ACTION, 0, 0, 0, ticks_now(), 0, { (int32_t)q->seq, 0, 0, 0 } };
        feed(&ev);                                 /* D-53: counted and quota-restarting in ring 0 */
    }
}

void agentlink_poll(void)
{
    if (!present)
        return;
    for (;;) {
        uint32_t fl = irq_save();
        if (rx_tail == rx_head) {
            if (rx_stalled) {
                rx_stalled = 0;
                pic_unmask(COM2_IRQ);
            }
            irq_restore(fl);
            break;
        }
        uint8_t b = rx[rx_tail];
        rx_tail = (rx_tail + 1) % RX_RING;
        irq_restore(fl);
        if (b == LINK_DELIM) {
            end_of_frame();
        } else if (raw_over) {
            /* discarding up to the next delimiter */
        } else if (raw_len >= LINK_MAX_ENCODED) {
            raw_over = 1;
        } else {
            raw[raw_len++] = b;
        }
    }
    uint32_t now = ticks_now();
    if (!connected && now - last_hello >= HELLO_INTERVAL)
        send_hello();
    if (now - last_hb >= HEARTBEAT_INTERVAL)
        send_heartbeat();
}

int agentlink_recv(uint8_t *dst, uint32_t cap)
{
    if (q_head == q_tail)
        return 0;
    const struct qframe *q = &queue[q_tail % QUEUE_SLOTS];
    uint32_t n = LINK_HDR_LEN + q->len;
    if (cap < n)
        return -EINVAL;
    dst[LINK_OFF_TYPE] = q->type;
    link_put_u16(dst + LINK_OFF_SEQ, q->seq);
    link_put_u16(dst + LINK_OFF_LEN, q->len);
    memcpy(dst + LINK_OFF_BODY, q->body, q->len);
    q_tail++;
    return (int)n;
}

void agentlink_flush_queue(void)
{
    while (q_tail != q_head) {
        uint8_t type = queue[q_tail % QUEUE_SLOTS].type;
        q_tail++;
        drop(LINKDROP_FLUSHED, type);
    }
}

/* ---- init ------------------------------------------------------------------------------------------- */
void agentlink_init(void)
{
    /* Presence: the scratch register reads back only when a 16550 is there (a missing port reads 0xFF). */
    outb(COM2_PORT + REG_SCR, 0x5A);
    if (inb(COM2_PORT + REG_SCR) != 0x5A) {
        present = 0;
        return;
    }
    outb(COM2_PORT + REG_SCR, 0xA5);
    if (inb(COM2_PORT + REG_SCR) != 0xA5) {
        present = 0;
        return;
    }
    present = 1;
    outb(COM2_PORT + REG_IER, 0x00);
    outb(COM2_PORT + REG_LCR, 0x80);               /* DLAB on */
    outb(COM2_PORT + REG_DATA, 0x01);              /* divisor 1 -> 115200 */
    outb(COM2_PORT + REG_IER, 0x00);
    outb(COM2_PORT + REG_LCR, 0x03);               /* 8N1, DLAB off */
    outb(COM2_PORT + REG_FCR, 0xC7);               /* FIFO on, clear both, 14-byte threshold */
    outb(COM2_PORT + REG_MCR, 0x0B);               /* DTR, RTS, OUT2 (OUT2 gates the IRQ line) */
    while (inb(COM2_PORT + REG_LSR) & LSR_DR)
        (void)inb(COM2_PORT + REG_DATA);           /* anything buffered before boot */
    outb(COM2_PORT + REG_IER, 0x01);               /* received data available */
    pic_unmask(COM2_IRQ);
    send_hello();
    last_hb = ticks_now();
}

void agentlink_print_status(void)
{
    printk("link: com2=%u connected=%u rx_frames=%u frames_dropped=%u queued=%u tx_frames=%u tx_dropped=%u "
           "stalled=%u\n",
           (unsigned)present, (unsigned)connected, frames_ok, frames_dropped, (unsigned)(q_head - q_tail),
           tx_frames, tx_dropped, (unsigned)rx_stalled);
}
