/* COM1 console: polled line input, the `ring3> ` command loop, and console lines as policy events (D-28).
 * The confirmation prompt (confirm.c) shares the line buffer: while a prompt is open, lines go to it. */
#include "console.h"
#include "audit.h"
#include "exec.h"
#include "io.h"
#include "kernel.h"
#include "kstring.h"
#include "objstore.h"
#include "pit.h"
#include "printk.h"
#include "qemu.h"
#include "serial.h"

static char line[CONSOLE_LINE_MAX + 1];
static uint32_t len;
static int ready;

void console_poll(void)
{
    int c;
    while ((c = serial_getc_nonblock()) >= 0) {
        if (ready)
            continue;                                /* a finished line waits to be taken: drop extras */
        if (c == '\r' || c == '\n') {
            line[len] = 0;
            ready = 1;
            serial_putc('\n');
        } else if (c == 8 || c == 127) {
            if (len)
                len--;
        } else if (c >= 32 && c < 127 && len < CONSOLE_LINE_MAX) {
            line[len++] = (char)c;
            serial_putc((char)c);
        }
    }
}

int console_line_ready(void) { return ready; }

void console_take_line(char *dst, uint32_t cap)
{
    strncpy(dst, line, cap - 1);
    dst[cap - 1] = 0;
    len = 0;
    ready = 0;
}

void console_drain(void)
{
    while (serial_getc_nonblock() >= 0)
        ;
    len = 0;
    ready = 0;
}

/* Blocks (interrupts on, hlt between polls) until a line is available. */
static void read_line(char *dst, uint32_t cap)
{
    for (;;) {
        audit_flush(1);
        console_poll();
        if (ready) {
            console_take_line(dst, cap);
            return;
        }
        sti();
        hlt();
    }
}

static int word(const char *s, const char *w, const char **rest)
{
    uint32_t n = (uint32_t)strlen(w);
    if (strncmp(s, w, n) != 0 || (s[n] != 0 && s[n] != ' '))
        return 0;
    s += n;
    while (*s == ' ')
        s++;
    *rest = s;
    return 1;
}

#ifdef RING3_TEST
static int32_t parse_int(const char **sp, int *ok)
{
    const char *s = *sp;
    int neg = 0;
    int32_t v = 0;
    if (*s == '-') {
        neg = 1;
        s++;
    }
    if (*s < '0' || *s > '9') {
        *ok = 0;
        return 0;
    }
    while (*s >= '0' && *s <= '9')
        v = v * 10 + (*s++ - '0');
    while (*s == ' ')
        s++;
    *sp = s;
    return neg ? -v : v;
}
#endif

static void cmd_status(void)
{
    const struct pol_state *s = audit_state();
    printk("status: fan=%u servo=%u estop_latched=%u tele_valid=%u distance=%u in_request=%u tainted=%u "
           "pending=%u seq=%u dropped=%u uptime=%u\n",
           s->fan_duty, s->servo_deg, s->estop_latched, s->tele_valid, s->distance_mm, s->in_request,
           s->tainted, s->pending, s->seq, audit_dropped(), ticks_now());
}

static void cmd_audit(void)
{
    static char buf[OBJ_DATA_MAX + 1];
    uint32_t n = objstore_read(OBJ_AUDIT_LOG, buf, OBJ_DATA_MAX);
    buf[n] = 0;
    printk("audit_log (%u bytes, newest lines kept):\n%s", n, buf);
}

static void cmd_objects(void)
{
    char buf[512];
    int n = objstore_list(buf, sizeof buf);
    if (n > 0)
        printk("%s", buf);
}

static void feed_console(uint8_t sub)
{
    struct pol_event ev = { EV_CONSOLE, sub, 0, 0, ticks_now(), 0, { 0, 0, 0, 0 } };
    struct pol_decision d;
    cli();
    audit_feed(&ev, &d);
    sti();
    audit_flush(1);
    if (sub == CONSOLE_ESTOP_RESET)
        printk("estop reset: %s %s\n", policy_code_name(d.code), policy_reason_name(d.reason));
}

static void dispatch(const char *l)
{
    const char *rest;
    if (*l == 0)
        return;
    if (word(l, "help", &rest)) {
        printk("commands: help status audit objects 'estop reset' exit"
#ifdef RING3_TEST
               " 'run <prog>' 'tele <mm> <estop> [bad]' 'tape on|off' panic"
#endif
               "\n");
        feed_console(CONSOLE_OTHER);
    } else if (word(l, "status", &rest)) {
        feed_console(CONSOLE_OTHER);
        cmd_status();
    } else if (word(l, "audit", &rest)) {
        feed_console(CONSOLE_OTHER);
        cmd_audit();
    } else if (word(l, "objects", &rest)) {
        feed_console(CONSOLE_OTHER);
        cmd_objects();
    } else if (word(l, "estop", &rest) && word(rest, "reset", &rest)) {
        feed_console(CONSOLE_ESTOP_RESET);
    } else if (word(l, "exit", &rest)) {
        feed_console(CONSOLE_OTHER);
        printk("KERNEL ALIVE uptime=%u\n", ticks_now());
        qemu_exit(QEMU_EXIT_PASS);
        printk("(no exit device; halting)\n");
        for (;;)
            hlt();
#ifdef RING3_TEST
    } else if (word(l, "run", &rest)) {
        feed_console(CONSOLE_OTHER);
        if (!exec_request_by_name(rest))
            printk("run: unknown program '%s'\n", rest);
    } else if (word(l, "tele", &rest)) {
        feed_console(CONSOLE_OTHER);
        int ok = 1;
        const char *p = rest;
        int32_t mm = parse_int(&p, &ok), estop = parse_int(&p, &ok);
        if (!ok) {
            printk("tele: usage tele <distance_mm> <estop> [bad]\n");
            return;
        }
        exec_inject_telemetry(mm, estop, !word(p, "bad", &p));
    } else if (word(l, "tape", &rest)) {
        feed_console(CONSOLE_OTHER);
        int off = word(rest, "off", &rest);
        audit_set_tape(!off);
        printk("tape %s\n", off ? "off" : "on");
    } else if (word(l, "panic", &rest)) {
        feed_console(CONSOLE_OTHER);
        printk("deliberate CPL-0 fault (test): write to unmapped 0xFFFFF000\n");
        *(volatile uint32_t *)0xFFFFF000u = 1;
#endif
    } else {
        feed_console(CONSOLE_OTHER);
        printk("unknown command: %s\n", l);
    }
}

void console_run(void)
{
    char l[CONSOLE_LINE_MAX + 1];
    for (;;) {
        audit_flush(1);
        printk("ring3> ");
        read_line(l, sizeof l);
        dispatch(l);
    }
}
