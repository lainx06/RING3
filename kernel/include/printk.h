#ifndef RING3_PRINTK_H
#define RING3_PRINTK_H
#include <stdarg.h>
#include <stddef.h>

/* Supports %s %c %d %u %x %p %% with optional '0' flag and width (e.g. %08x). No floats. */
void printk(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void vprintk(const char *fmt, va_list ap);
int ksnprintf(char *buf, size_t n, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
int kvsnprintf(char *buf, size_t n, const char *fmt, va_list ap);
#endif
