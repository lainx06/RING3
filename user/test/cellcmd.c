/* cellcmd: device syscalls scripted from the console line (test image, T-04):
 *   run cellcmd fan 60; wait 50; servo 120; status; read 1; halt
 * Commands (';'-separated): fan N | servo N | halt | lcd N TEXT | status | read N | wait N (ticks; an action
 * boundary every 20 so the per-action quota does not end the program) | sys N (raw syscall number N with
 * zero arguments: there is no E-stop reset syscall, INV-13) | end. Each prints
 * "cellcmd: <cmd> -> <result>"; `status` decodes the 16-byte record. The policy decides every call. */
#include "libu.h"

static const char *skip(const char *s)
{
    while (*s == ' ')
        s++;
    return s;
}

static int num(const char **sp, int *ok)
{
    const char *s = skip(*sp);
    int neg = 0, v = 0;
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
    *sp = s;
    return neg ? -v : v;
}

static int is(const char *s, const char *w, const char **rest)
{
    size_t n = u_strlen(w);
    for (size_t i = 0; i < n; i++)
        if (s[i] != w[i])
            return 0;
    if (s[n] != 0 && s[n] != ' ' && s[n] != ';')
        return 0;
    *rest = s + n;
    return 1;
}

static void wait_ticks(uint32_t n)
{
    uint32_t t0 = u_uptime(), last = t0;
    while (u_uptime() - t0 < n) {
        if (u_uptime() - last >= 20) {
            u_test_boundary();
            last = u_uptime();
        }
    }
}

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void show(const char *label, int v)
{
    u_puts(label);
    u_putdec(v);
}

int main(void)
{
    const char *s = (const char *)USER_ARG_ADDR;
    static uint8_t rec[16];
    static uint8_t big[2048];
    for (;;) {
        s = skip(s);
        if (*s == ';') {
            s++;
            continue;
        }
        if (*s == 0)
            break;
        const char *cmd = s;
        const char *rest;
        int ok = 1, r;
        if (is(cmd, "fan", &rest)) {
            int d = num(&rest, &ok);
            r = ok ? u_cell_fan(d) : -EINVAL;
            u_puts("cellcmd: fan ");
            u_putdec(d);
        } else if (is(cmd, "servo", &rest)) {
            int d = num(&rest, &ok);
            r = ok ? u_cell_servo(d) : -EINVAL;
            u_puts("cellcmd: servo ");
            u_putdec(d);
        } else if (is(cmd, "halt", &rest)) {
            r = u_cell_halt();
            u_puts("cellcmd: halt");
        } else if (is(cmd, "read", &rest)) {
            int id = num(&rest, &ok);
            r = ok ? u_obj_read(id, big, sizeof big) : -EINVAL;
            u_puts("cellcmd: read ");
            u_putdec(id);
        } else if (is(cmd, "wait", &rest)) {
            int n = num(&rest, &ok);
            if (ok && n > 0)
                wait_ticks((uint32_t)n);
            r = 0;
            u_puts("cellcmd: wait ");
            u_putdec(n);
        } else if (is(cmd, "lcd", &rest)) {
            int line = num(&rest, &ok);
            static char text[64];
            uint32_t n = 0;
            rest = skip(rest);
            while (rest[n] && rest[n] != ';' && n < sizeof text - 1) {
                text[n] = rest[n];
                n++;
            }
            text[n] = 0;
            rest += n;
            r = ok ? u_cell_lcd(line, text) : -EINVAL;
            u_puts("cellcmd: lcd ");
            u_putdec(line);
            u_puts(" \"");
            u_puts(text);
            u_puts("\"");
        } else if (is(cmd, "status", &rest)) {
            r = u_cell_status(rec);
            u_puts("cellcmd: status");
            if (r == 16) {
                show(" dist=", rd16(rec));
                show(" fan=", rec[2]);
                show(" servo=", rec[3]);
                show(" estop=", rec[4]);
                show(" flags=", rec[5]);
                show(" fresh=", rec[6]);
                show(" latched=", rec[7]);
                show(" age_ms=", (int)rd32(rec + 8));
                show(" seq=", rd16(rec + 12));
                show(" cmd_fan=", rec[14]);
                show(" cmd_servo=", rec[15]);
            }
        } else if (is(cmd, "sys", &rest)) {
            int nr = num(&rest, &ok);
            r = ok ? syscall4((uint32_t)nr, 0, 0, 0, 0) : -EINVAL;
            u_puts("cellcmd: sys ");
            u_putdec(nr);
        } else if (is(cmd, "end", &rest)) {
            break;
        } else {
            u_puts("cellcmd: unknown command\n");
            return 2;
        }
        u_puts(" -> ");
        u_putdec(r);
        u_puts("\n");
        s = rest;
    }
    u_puts("cellcmd: done\n");
    return 0;
}
