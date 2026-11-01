/* A5: privileged instruction in ring 3 (IOPL = 0) -> #GP (vector 13). */
#include "libu.h"

int main(void)
{
    u_puts("priv: executing cli\n");
    __asm__ volatile("cli");
    u_puts("priv: STILL ALIVE (bad)\n");
    return 7;
}
