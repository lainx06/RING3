/* INV-6: hlt in ring 3 (IOPL = 0) -> #GP (vector 13). */
#include "libu.h"
int main(void)
{
    u_puts("hlt: executing hlt\n");
    __asm__ volatile("hlt");
    u_puts("hlt: STILL ALIVE (bad)\n");
    return 7;
}
