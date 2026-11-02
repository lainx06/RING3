#include "confirm.h"
#include "audit.h"
#include "console.h"
#include "exec.h"
#include "io.h"
#include "kstring.h"
#include "pit.h"
#include "printk.h"

static void print_args(const struct pol_event *c)
{
    const struct pol_object_rule *o;
    switch (c->nr) {
    case SYS_OBJ_WRITE:
        o = policy_object((uint32_t)c->a[0]);
        printk("%s, %d bytes", o ? o->name : "?", c->a[2]);
        break;
    case SYS_CELL_FAN:
    case SYS_CELL_SERVO:
        printk("%d", c->a[0]);
        break;
    case SYS_CELL_LCD:
        printk("line %d", c->a[0]);
        break;
    default:
        printk("%d, %d", c->a[0], c->a[1]);
        break;
    }
}

static void idle_ticks(uint32_t n)
{
    uint32_t t0 = ticks_now();
    while (ticks_now() - t0 < n) {
        sti();
        hlt();
    }
}

void confirm_run(const struct pol_event *call, struct pol_decision *d)
{
    /* R10: type-ahead can't answer a prompt. Drain the FIFO and the line buffer, give QEMU a moment to push
     * anything still queued on the host side, drain again, then print. */
    console_drain();
    idle_ticks(2);
    console_drain();
    audit_flush(1);
    printk("CONFIRM: %s wants %s(", exec_current_name(), policy_syscall_name(call->nr));
    print_args(call);
    printk(")%s. Allow? [y/N] ", audit_state()->tainted ? " [tainted]" : "");

    char line[CONSOLE_LINE_MAX + 1];
    for (;;) {
        audit_flush(1);
        if (exec_kill_pending())
            exec_abandon_limit();                     /* request quota ran out while waiting (R2) */
        console_poll();
        if (console_line_ready()) {
            console_take_line(line, sizeof line);
            int yes = strncmp(line, "y", 2) == 0 || strncmp(line, "yes", 4) == 0;
            struct pol_event ev = { EV_CONFIRM, yes ? ANSWER_YES : ANSWER_NO, 0, 0, ticks_now(), 0,
                                    { 0, 0, 0, 0 } };
            cli();
            audit_feed(&ev, d);
            sti();
            return;
        }
        if (!audit_state()->pending) {                /* the tick path timed the prompt out */
            printk("\n");
            d->code = DEC_CONFIRM_NO;
            d->reason = R_TIMEOUT;
            d->action = ACT_NONE;
            d->result = -ECANCELED;
            d->audit = 0;                             /* already audited by the tick decision */
            return;
        }
        sti();
        hlt();
    }
}
