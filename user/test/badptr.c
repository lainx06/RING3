/* A5 / INV-3: every bad user pointer gets -EFAULT without a kernel page fault. */
#include "libu.h"

#define USER_BASE 0x40000000u
#define USER_TOP 0x7FFFF000u
#define STACK_BOTTOM (USER_TOP - 16u * 1024)

struct tc {
    const char *name;
    uint32_t ptr, len;
    int write;
};

static const struct tc cases[] = {
    { "kernel address 0x00100000", 0x00100000u, 16, 0 },
    { "above USER_TOP 0xC0000000", 0xC0000000u, 16, 0 },
    { "unmapped user address 0x50000000", 0x50000000u, 16, 0 },
    { "mapped stack page running into the unmapped page above it", USER_TOP - 16, 64, 0 },
    { "wrap-around length", USER_TOP - 8, 0xFFFFFFF0u, 0 },
    { "uread into own read-only code page", USER_BASE + 16, 16, 1 },
    { "guard page below the stack", STACK_BOTTOM - 32, 16, 0 },
    { "buffer starting in the guard page running into the stack", STACK_BOTTOM - 8, 32, 0 },
};

int main(void)
{
    char ok[16];
    int fails = 0;
    /* sanity: a valid buffer works both ways */
    if (u_uread(ok, sizeof ok) != (int)sizeof ok || ok[0] != (char)0xA5 || ok[15] != (char)0xA5) {
        u_puts("badptr: valid uread FAILED\n");
        fails++;
    }
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        int r = cases[i].write ? syscall2(SYS_UREAD, cases[i].ptr, cases[i].len)
                               : syscall2(SYS_CONS_WRITE, cases[i].ptr, cases[i].len);
        u_puts("badptr case ");
        u_putdec((int)i + 1);
        u_puts(r == -EFAULT ? " EFAULT ok: " : " UNEXPECTED: ");
        if (r != -EFAULT) {
            u_puts("r=");
            u_putdec(r);
            u_puts(" ");
            fails++;
        }
        u_puts(cases[i].name);
        u_puts("\n");
    }
    u_puts(fails ? "badptr: FAIL\n" : "badptr: all cases EFAULT\n");
    return fails ? 1 : 0;
}
