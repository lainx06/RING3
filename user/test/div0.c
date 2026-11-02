/* INV-6: divide by zero -> #DE (vector 0). */
#include "libu.h"
int main(void)
{
    u_puts("div0: dividing by zero\n");
    volatile int zero = 0;
    volatile int r = 100 / zero;
    (void)r;
    u_puts("div0: STILL ALIVE (bad)\n");
    return 7;
}
