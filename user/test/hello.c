/* K1: a syscall from CPL 3, exit 0. Also writes and reads back a global (proves the writable data page). */
#include "libu.h"

int g_counter = 1;              /* .data */
int g_bss[4];                   /* .bss, must read as zero */

int main(void)
{
    u_puts("hello from ring 3\n");
    g_counter = 42;
    g_bss[2] = g_counter + 1;
    u_puts("hello: g_counter=");
    u_putdec(*(volatile int *)&g_counter);
    u_puts(" g_bss[2]=");
    u_putdec(*(volatile int *)&g_bss[2]);
    u_puts(" g_bss[0]=");
    u_putdec(*(volatile int *)&g_bss[0]);
    u_puts("\n");
    return (g_counter == 42 && g_bss[2] == 43 && g_bss[0] == 0) ? 0 : 1;
}
