/* T-18 / INV-06: a ring-3 write into the graphics framebuffer window (QEMU standard VGA BAR0, kernel-only
 * mapping with gfx=1, unmapped otherwise) -> #PF (vector 14), the program is killed, the kernel continues. */
#include "libu.h"

int main(void)
{
    u_puts("fbpoke: writing to the framebuffer window 0xFD000000\n");
    *(volatile uint32_t *)0xFD000000u = 0x00FF00FFu;
    u_puts("fbpoke: STILL ALIVE (bad)\n");
    return 7;
}
