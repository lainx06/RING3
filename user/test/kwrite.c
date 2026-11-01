/* K2: write to a kernel address -> #PF (vector 14), program killed, kernel continues. */
#include "libu.h"

int main(void)
{
    u_puts("kwrite: writing to 0x00100000\n");
    *(volatile uint32_t *)0x00100000u = 0xDEADBEEF;
    u_puts("kwrite: STILL ALIVE (bad)\n");
    return 7;
}
