/* T-18 / INV-06: a ring-3 write into the graphics back buffer window (physical 16 MiB, kernel-only mapping
 * with gfx=1, unmapped otherwise) -> #PF (vector 14), the program is killed, the kernel continues. */
#include "libu.h"

int main(void)
{
    u_puts("bbpoke: writing to the back buffer window 0x01000000\n");
    *(volatile uint32_t *)0x01000000u = 0x00FF00FFu;
    u_puts("bbpoke: STILL ALIVE (bad)\n");
    return 7;
}
