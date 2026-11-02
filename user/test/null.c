/* INV-6: null dereference -> #PF (vector 14). */
#include "libu.h"
int main(void)
{
    u_puts("null: reading address 0\n");
    volatile int v = *(volatile int *)0;
    (void)v;
    u_puts("null: STILL ALIVE (bad)\n");
    return 7;
}
