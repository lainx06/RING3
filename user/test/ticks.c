/* A5: the program after a fault or LIMIT still sees the timer advancing. */
#include "libu.h"

int main(void)
{
    uint32_t t0 = u_uptime(), t1;
    do
        t1 = u_uptime();
    while (t1 - t0 < 5);
    u_puts("ticks: t0=");
    u_putdec((int)t0);
    u_puts(" t1=");
    u_putdec((int)t1);
    u_puts("\n");
    return t1 > t0 ? 0 : 1;
}
