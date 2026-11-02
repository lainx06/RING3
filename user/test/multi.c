/* R2 / A3: crosses action boundaries forever (each boundary restarts the action quota) -> the request quota
 * ends it with LIMIT. */
#include "libu.h"
int main(void)
{
    u_puts("multi: action boundaries every 20 ticks\n");
    for (;;) {
        uint32_t t0 = u_uptime();
        while (u_uptime() - t0 < 20)
            ;
        u_test_boundary();
    }
}
