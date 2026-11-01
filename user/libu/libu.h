#ifndef RING3_LIBU_H
#define RING3_LIBU_H
#include <stddef.h>
#include <stdint.h>

#define SYS_EXIT 0
#define SYS_CONS_WRITE 1
#define SYS_UREAD 2
#define SYS_UPTIME 10
#define EFAULT 14

static inline int syscall2(uint32_t nr, uint32_t a, uint32_t b)
{
    int r;
    __asm__ volatile("int $0x80" : "=a"(r) : "a"(nr), "b"(a), "c"(b) : "memory");
    return r;
}

void u_exit(int code) __attribute__((noreturn));
int u_write(const char *s, size_t n);
int u_puts(const char *s);
void u_puthex(uint32_t v);
void u_putdec(int v);
int u_uread(void *buf, size_t n);
uint32_t u_uptime(void);
size_t u_strlen(const char *s);
#endif
