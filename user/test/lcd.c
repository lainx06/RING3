/* R6: bounded NUL-terminated string copies for cell_lcd. */
#include "libu.h"
int main(void)
{
    static char unterminated[64];
    int fails = 0;
    for (unsigned i = 0; i < sizeof unterminated; i++)
        unterminated[i] = 'A';
    fails += u_expect("lcd: valid text", u_cell_lcd(0, "hello"), -ENOSYS);
    fails += u_expect("lcd: 16 chars", u_cell_lcd(1, "0123456789abcdef"), -ENOSYS);
    fails += u_expect("lcd: 17 chars", u_cell_lcd(1, "0123456789abcdefg"), -EINVAL);
    fails += u_expect("lcd: unterminated", u_cell_lcd(0, unterminated), -EINVAL);
    fails += u_expect("lcd: line 2", u_cell_lcd(2, "x"), -EINVAL);
    fails += u_expect("lcd: unmapped text", syscall2(SYS_CELL_LCD, 0, 0x50000000u), -EFAULT);
    fails += u_expect("lcd: kernel text", syscall2(SYS_CELL_LCD, 0, 0x00100000u), -EFAULT);
    for (uint32_t p = USER_TOP - 4; p < USER_TOP; p++)
        *(volatile char *)(uintptr_t)p = 'A';             /* above the initial stack pointer, no NUL */
    fails += u_expect("lcd: text running past USER_TOP", syscall2(SYS_CELL_LCD, 0, USER_TOP - 4), -EFAULT);
    fails += u_expect("lcd: control char", u_cell_lcd(0, "a\tb"), -EINVAL);
    u_puts(fails ? "lcd: FAIL\n" : "lcd: done\n");
    return fails;
}
