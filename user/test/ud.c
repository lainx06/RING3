/* INV-6: invalid opcode -> #UD (vector 6). */
#include "libu.h"
int main(void)
{
    u_puts("ud: executing ud2\n");
    __asm__ volatile("ud2");
    u_puts("ud: STILL ALIVE (bad)\n");
    return 7;
}
