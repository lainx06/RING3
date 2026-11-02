#include "libu.h"

void u_exit(int code)
{
    for (;;)
        syscall2(SYS_EXIT, (uint32_t)code, 0);
}

int u_write(const char *s, size_t n) { return syscall2(SYS_CONS_WRITE, (uint32_t)(uintptr_t)s, (uint32_t)n); }
int u_uread(void *buf, size_t n) { return syscall2(SYS_TEST_UREAD, (uint32_t)(uintptr_t)buf, (uint32_t)n); }
uint32_t u_uptime(void) { return (uint32_t)syscall2(SYS_UPTIME, 0, 0); }
int u_obj_list(char *buf, size_t n) { return syscall2(SYS_OBJ_LIST, (uint32_t)(uintptr_t)buf, (uint32_t)n); }
int u_obj_read(int id, void *buf, size_t n)
{
    return syscall4(SYS_OBJ_READ, (uint32_t)id, (uint32_t)(uintptr_t)buf, (uint32_t)n, 0);
}
int u_obj_write(int id, const void *buf, size_t n)
{
    return syscall4(SYS_OBJ_WRITE, (uint32_t)id, (uint32_t)(uintptr_t)buf, (uint32_t)n, 0);
}
int u_cell_fan(int duty) { return syscall2(SYS_CELL_FAN, (uint32_t)duty, 0); }
int u_cell_servo(int deg) { return syscall2(SYS_CELL_SERVO, (uint32_t)deg, 0); }
int u_cell_halt(void) { return syscall2(SYS_CELL_HALT, 0, 0); }
int u_cell_lcd(int line, const char *text)
{
    return syscall2(SYS_CELL_LCD, (uint32_t)line, (uint32_t)(uintptr_t)text);
}
int u_cell_status(void *buf16) { return syscall2(SYS_CELL_STATUS, (uint32_t)(uintptr_t)buf16, 0); }
int u_agent_send(uint32_t type, const void *body, size_t n)
{
    return syscall4(SYS_AGENT_SEND, (uint32_t)(uintptr_t)body, (uint32_t)n, type, 0);
}
int u_agent_recv(void *buf, size_t cap, uint32_t timeout_ticks)
{
    return syscall4(SYS_AGENT_RECV, (uint32_t)(uintptr_t)buf, (uint32_t)cap, timeout_ticks, 0);
}
int u_test_tele(int distance_mm, int estop, int valid)
{
    return syscall4(SYS_TEST_TELE, (uint32_t)distance_mm, (uint32_t)estop, (uint32_t)valid, 0);
}
int u_test_boundary(void) { return syscall2(SYS_TEST_BOUNDARY, 0, 0); }

size_t u_strlen(const char *s)
{
    size_t n = 0;
    while (s[n])
        n++;
    return n;
}

int u_puts(const char *s) { return u_write(s, u_strlen(s)); }

void u_puthex(uint32_t v)
{
    char b[11] = "0x00000000";
    for (int i = 9; i >= 2; i--, v >>= 4)
        b[i] = "0123456789abcdef"[v & 15];
    u_write(b, 10);
}

void u_putdec(int v)
{
    char b[12];
    int n = 0, neg = v < 0;
    uint32_t m = neg ? (uint32_t)(-v) : (uint32_t)v;
    do {
        b[n++] = (char)('0' + m % 10);
        m /= 10;
    } while (m);
    if (neg)
        b[n++] = '-';
    for (int i = 0; i < n / 2; i++) {
        char t = b[i];
        b[i] = b[n - 1 - i];
        b[n - 1 - i] = t;
    }
    u_write(b, (size_t)n);
}

int u_expect(const char *label, int r, int expect)
{
    u_puts(label);
    u_puts("=");
    u_putdec(r);
    if (r != expect) {
        u_puts(" UNEXPECTED (wanted ");
        u_putdec(expect);
        u_puts(")\n");
        return 1;
    }
    u_puts(" ok\n");
    return 0;
}

/* clang may emit calls to these for struct/array initialisation */
void *memset(void *d, int c, size_t n)
{
    uint8_t *p = d;
    while (n--)
        *p++ = (uint8_t)c;
    return d;
}
void *memcpy(void *d, const void *s, size_t n)
{
    uint8_t *p = d;
    const uint8_t *q = s;
    while (n--)
        *p++ = *q++;
    return d;
}
