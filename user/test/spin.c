/* K3: infinite loop; the timer IRQ arrives while CPL = 3 and the kernel ends the program with LIMIT. */
#include "libu.h"

int main(void)
{
    u_puts("spin: looping forever\n");
    for (;;)
        ;
}
