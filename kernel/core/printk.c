#include "printk.h"
#include "serial.h"
#include <stdint.h>

struct out {
    char *buf;      /* NULL -> serial */
    size_t cap;
    size_t len;
};

static void put(struct out *o, char c)
{
    if (o->buf) {
        if (o->len + 1 < o->cap)
            o->buf[o->len] = c;
    } else {
        serial_putc(c);
    }
    o->len++;
}

static void put_num(struct out *o, uint32_t v, unsigned base, int neg, int width, char pad, int upper)
{
    char tmp[12];
    int n = 0;
    const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    do {
        tmp[n++] = digits[v % base];
        v /= base;
    } while (v);
    int total = n + (neg ? 1 : 0);
    if (neg && pad == '0')
        put(o, '-');
    for (int i = total; i < width; i++)
        put(o, pad);
    if (neg && pad != '0')
        put(o, '-');
    while (n)
        put(o, tmp[--n]);
}

static void vformat(struct out *o, const char *fmt, va_list ap)
{
    for (; *fmt; fmt++) {
        if (*fmt != '%') {
            put(o, *fmt);
            continue;
        }
        fmt++;
        char pad = ' ';
        int width = 0;
        if (*fmt == '0') {
            pad = '0';
            fmt++;
        }
        while (*fmt >= '0' && *fmt <= '9')
            width = width * 10 + (*fmt++ - '0');
        switch (*fmt) {
        case 's': {
            const char *s = va_arg(ap, const char *);
            if (!s)
                s = "(null)";
            while (*s)
                put(o, *s++);
            break;
        }
        case 'c':
            put(o, (char)va_arg(ap, int));
            break;
        case 'd': {
            int v = va_arg(ap, int);
            uint32_t mag = v < 0 ? (uint32_t)(-(int64_t)v) : (uint32_t)v;
            put_num(o, mag, 10, v < 0, width, pad, 0);
            break;
        }
        case 'u':
            put_num(o, va_arg(ap, uint32_t), 10, 0, width, pad, 0);
            break;
        case 'x':
            put_num(o, va_arg(ap, uint32_t), 16, 0, width, pad, 0);
            break;
        case 'X':
            put_num(o, va_arg(ap, uint32_t), 16, 0, width, pad, 1);
            break;
        case 'p':
            put(o, '0');
            put(o, 'x');
            put_num(o, (uint32_t)(uintptr_t)va_arg(ap, void *), 16, 0, 8, '0', 0);
            break;
        case '%':
            put(o, '%');
            break;
        case 0:
            return;
        default:
            put(o, '%');
            put(o, *fmt);
            break;
        }
    }
}

void vprintk(const char *fmt, va_list ap)
{
    struct out o = { 0, 0, 0 };
    vformat(&o, fmt, ap);
}

void printk(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintk(fmt, ap);
    va_end(ap);
}

int kvsnprintf(char *buf, size_t n, const char *fmt, va_list ap)
{
    struct out o = { buf, n, 0 };
    vformat(&o, fmt, ap);
    if (n)
        buf[o.len < n ? o.len : n - 1] = 0;
    return (int)o.len;
}

int ksnprintf(char *buf, size_t n, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = kvsnprintf(buf, n, fmt, ap);
    va_end(ap);
    return r;
}
