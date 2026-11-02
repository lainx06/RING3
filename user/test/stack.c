/* INV-6: unbounded recursion hits the unmapped guard page below the 16 KiB stack -> #PF (vector 14). */
#include "libu.h"
static int depth;
static volatile int stop = 1 << 30;
static int recurse(int n)
{
    volatile char pad[256];
    pad[0] = (char)n;
    depth = n;
    if (n >= stop)
        return pad[0];
    return recurse(n + 1) + pad[0];
}
int main(void)
{
    u_puts("stack: recursing until the guard page\n");
    int r = recurse(1);
    u_puts("stack: STILL ALIVE (bad)\n");
    return r;
}
