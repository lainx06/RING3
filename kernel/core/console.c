/* COM1 console: polled line input, the `ring3> ` command loop, and console lines as policy events (D-28).
 * The confirmation prompt (confirm.c) shares the line buffer: while a prompt is open, lines go to it. */
#include "console.h"
#include "agentlink.h"
#include "audit.h"
#include "confirm.h"
#include "device.h"
#include "exec.h"
#include "io.h"
#include "kernel.h"
#include "kstring.h"
#include "objstore.h"
#include "pit.h"
#include "printk.h"
#include "qemu.h"
#include "serial.h"
#include "vga.h"

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
            if (len) {
                len--;
                vga_backspace();
            }
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
        agentlink_poll();
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
    agentlink_print_status();
    device_print_status();
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
    struct pol_event ev = { EV_CONSOLE, sub, 0, 0, ticks_now(), 0, { (int32_t)exec_confirm_ticks(), 0, 0, 0 } };
    struct pol_decision d;
    cli();
    audit_feed(&ev, &d);
    sti();
    audit_flush(1);
    if (sub != CONSOLE_ESTOP_RESET)
        return;
    /* T-04: `estop reset` opens a confirmation (INV-05); on y the policy re-checks the button and the
     * telemetry at that tick and only then does the kernel send ESTOP_RESET on COM3. */
    if (d.action == ACT_PROMPT) {
        cli();
        confirm_run(&ev, &d);
        sti();
    }
    int r = 0;
    if (d.action == ACT_EXEC)
        r = device_estop_reset();
    device_show(PEND_ESTOP_RESET, &d);
    audit_flush(1);
    printk("estop reset: %s %s%s\n", policy_code_name(d.code), policy_reason_name(d.reason),
           d.action != ACT_EXEC ? "" : r == 0 ? " (ESTOP_RESET sent)" : " (no COM3 device: nothing sent)");
}

/* A plain-English line is a request: agentsh (ring 3, the production capability set) carries it to the
 * bridge over COM2 and performs what comes back through system calls (ARCHITECTURE §5). */
static void request(const char *text, int variant)
{
    if (*text == 0) {
        printk("request: empty line\n");
        return;
    }
    if (!agentlink_present()) {
        printk("request not started: no COM2 device (start QEMU with a second -serial for the bridge)\n");
        return;
    }
    printk("REQUEST %s\n", text);
    if (!exec_request_agent(text, variant))
        printk("request not started\n");
}

static void dispatch(const char *l)
{
    const char *rest;
    if (*l == 0)
        return;
    if (word(l, "help", &rest)) {
        printk("commands: help status audit objects 'estop reset' exit; any other line is a request for the agent"
#ifdef RING3_TEST
               "; test: 'run <prog> [args]' 'agent-test <text>' 'agent-nocap <text>' 'tele <mm> <estop> [bad]'"
               " 'teletick <mm> <estop> <n>|off' 'tape on|off' 'confirmrace on|off' 'cellmute <ticks>' panic"
#endif
               "\n");
        feed_console(CONSOLE_OTHER);
    } else if (word(l, "status", &rest)) {
        feed_console(CONSOLE_OTHER);
        cmd_status();
    } else if (word(l, "screen", &rest)) {
        feed_console(CONSOLE_OTHER);
        vga_print_stats();
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
        audit_flush(1);
        printk("audit: dropped=%u\n", audit_dropped());     /* F5: any drop fails every QEMU test */
        printk("KERNEL ALIVE uptime=%u\n", ticks_now());
        qemu_exit(QEMU_EXIT_PASS);
        printk("(no exit device; halting)\n");
        for (;;)
            hlt();
#ifdef RING3_TEST
    } else if (word(l, "run", &rest)) {
        feed_console(CONSOLE_OTHER);
        /* `run <prog> [args...]`: the rest of the line reaches the program at USER_ARG_ADDR (cellcmd) */
        char name[32];
        uint32_t n = 0;
        while (rest[n] && rest[n] != ' ' && n < sizeof name - 1) {
            name[n] = rest[n];
            n++;
        }
        name[n] = 0;
        const char *arg = rest + n;
        while (*arg == ' ')
            arg++;
        if (!exec_request_by_name(name, *arg ? arg : 0))
            printk("run: unknown program '%s'\n", name);
    } else if (word(l, "cellmute", &rest)) {
        feed_console(CONSOLE_OTHER);
        int ok = 1;
        const char *p = rest;
        int32_t ticks = parse_int(&p, &ok);
        if (!ok || ticks < 0) {
            printk("cellmute: usage cellmute <ticks> (0 = off)\n");
            return;
        }
        device_mute((uint32_t)ticks);
        printk("cellmute %d ticks (no COM3 frames sent meanwhile)\n", ticks);
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
    } else if (word(l, "teletick", &rest)) {
        feed_console(CONSOLE_OTHER);
        if (word(rest, "off", &rest)) {
            audit_set_tick_telemetry(0, 0, 0);
            printk("teletick off\n");
            return;
        }
        int ok = 1;
        const char *p = rest;
        int32_t mm = parse_int(&p, &ok), estop = parse_int(&p, &ok), every = parse_int(&p, &ok);
        if (!ok || every <= 0) {
            printk("teletick: usage teletick <distance_mm> <estop> <every_n_ticks> | off\n");
            return;
        }
        audit_set_tick_telemetry(mm, estop, (uint32_t)every);
        printk("teletick every %d\n", every);
    } else if (word(l, "confirmrace", &rest)) {
        feed_console(CONSOLE_OTHER);
        int on = word(rest, "on", &rest);
        confirm_set_race(on);
        printk("confirm race %s\n", on ? "on" : "off");
    } else if (word(l, "panic", &rest)) {
        feed_console(CONSOLE_OTHER);
        printk("deliberate CPL-0 fault (test): write to unmapped 0xFFFFF000\n");
        *(volatile uint32_t *)0xFFFFF000u = 1;
    } else if (word(l, "agent-test", &rest)) {
        request(rest, 1);
    } else if (word(l, "agent-nocap", &rest)) {
        request(rest, 2);
#endif
    } else {
        request(l, 0);
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
