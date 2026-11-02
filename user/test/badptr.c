/* INV-3 / A4: every bad user pointer gets -EFAULT without a kernel page fault; every valid edge works.
 * Cases cover D-41 plus the skeptic's gaps: a buffer starting in the last mapped data page and running into
 * the unmapped gap below USER_TOP (both directions), null, len == 0 (R5), ptr == USER_TOP, a real wrap. */
#include "libu.h"

#define STACK_BOTTOM (USER_TOP - 16u * 1024)
extern char _mem_end[];          /* end of the writable data+bss pages (user/user.ld) */

struct tc {
    const char *name;
    uint32_t ptr, len;
    int write;
    int expect;                  /* 0 = -EFAULT, 1 = ok (returns len) */
};

static char ok[32];

int main(void)
{
    uint32_t mem_end = (uint32_t)(uintptr_t)_mem_end;
    const struct tc cases[] = {
        { "null pointer", 0, 16, 0, 0 },
        { "kernel address 0x00100000", 0x00100000u, 16, 0, 0 },
        { "above USER_TOP 0xC0000000", 0xC0000000u, 16, 0, 0 },
        { "unmapped user address 0x50000000", 0x50000000u, 16, 0, 0 },
        { "last data page running into the unmapped gap (read)", mem_end - 8, 32, 0, 0 },
        { "last data page running into the unmapped gap (write)", mem_end - 8, 32, 1, 0 },
        { "top stack page running past USER_TOP", USER_TOP - 16, 64, 0, 0 },
        { "ptr == USER_TOP, len 0 (R5)", USER_TOP, 0, 0, 0 },
        { "unmapped page, len 0 (R5)", 0x50000000u, 0, 0, 0 },
        { "length cap + 1", (uint32_t)(uintptr_t)ok, 4097, 0, 0 },
        { "wrap-around length", USER_TOP - 8, 0xFFFFFFF0u, 0, 0 },
        { "real wrap: USER_TOP - 8 + 4096", USER_TOP - 8, 4096, 0, 0 },
        { "uread into own read-only code page", USER_BASE + 16, 16, 1, 0 },
        { "guard page below the stack", STACK_BOTTOM - 32, 16, 0, 0 },
        { "guard page running into the stack", STACK_BOTTOM - 8, 32, 0, 0 },
        { "valid: top 16 bytes of the stack", USER_TOP - 16, 16, 0, 1 },
        { "valid: last 8 bytes of the data pages", mem_end - 8, 8, 1, 1 },
        { "valid: len 0 on a mapped page", (uint32_t)(uintptr_t)ok, 0, 0, 1 },
    };
    int fails = 0;
    for (unsigned i = 0; i < sizeof ok; i++)
        ok[i] = '.';
    if (u_uread(ok, 16) != 16 || ok[0] != (char)0xA5 || ok[15] != (char)0xA5 || ok[16] != '.') {
        u_puts("badptr: valid uread FAILED\n");
        fails++;
    }
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        int r = cases[i].write ? syscall2(SYS_TEST_UREAD, cases[i].ptr, cases[i].len)
                               : syscall2(SYS_CONS_WRITE, cases[i].ptr, cases[i].len);
        int good = cases[i].expect ? r == (int)cases[i].len : r == -EFAULT;
        if (cases[i].expect && good)
            u_puts("\n");
        u_puts("badptr case ");
        u_putdec((int)i + 1);
        u_puts(good ? (cases[i].expect ? " valid ok: " : " EFAULT ok: ") : " UNEXPECTED: ");
        if (!good) {
            u_puts("r=");
            u_putdec(r);
            u_puts(" ");
            fails++;
        }
        u_puts(cases[i].name);
        u_puts("\n");
    }
    u_puts(fails ? "badptr: FAIL\n" : "badptr: all cases ok\n");
    return fails ? 1 : 0;
}
