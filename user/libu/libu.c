#include "libu.h"

void u_exit(int code)
{
    for (;;)
        syscall2(SYS_EXIT, (uint32_t)code, 0);
}

int u_write(const char *s, size_t n) { return syscall2(SYS_CONS_WRITE, (uint32_t)(uintptr_t)s, (uint32_t)n); }
int u_uread(void *buf, size_t n) { return syscall2(SYS_UREAD, (uint32_t)(uintptr_t)buf, (uint32_t)n); }
uint32_t u_uptime(void) { return (uint32_t)syscall2(SYS_UPTIME, 0, 0); }

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
