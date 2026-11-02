/* Device rules in QEMU (pure policy; no driver, so an allowed motion returns -ENOSYS). The harness injects
 * telemetry through the test hook and answers the first-fan prompt. */
#include "libu.h"
static void wait_ticks(uint32_t n)
{
    uint32_t t0 = u_uptime(), last = t0;
    while (u_uptime() - t0 < n) {
        if (u_uptime() - last >= 20) {               /* idle time counts as action boundaries (R2) */
            u_test_boundary();
            last = u_uptime();
        }
    }
}
int main(void)
{
    int fails = 0;
    fails += u_expect("fan: no telemetry fan 60", u_cell_fan(60), -ESAFETY);
    u_test_tele(300, 0, 1);                                   /* clear, fresh */
    fails += u_expect("fan: servo 200 (arg)", u_cell_servo(200), -EINVAL);
    fails += u_expect("fan: fan 30 (arg, D-47)", u_cell_fan(30), -EINVAL);
    fails += u_expect("fan: fan -1 (arg)", u_cell_fan(-1), -EINVAL);
    fails += u_expect("fan: fan 90 (limit)", u_cell_fan(90), -ESAFETY);
    fails += u_expect("fan: fan 70 first step (limit)", u_cell_fan(70), -ESAFETY);
    u_puts("fan: fan 60 (first fan: prompt expected)\n");
    fails += u_expect("fan: fan 60", u_cell_fan(60), -ENOSYS);
    fails += u_expect("fan: fan 80 same second (rate)", u_cell_fan(80), -ESAFETY);
    fails += u_expect("fan: servo 120 (step 30)", u_cell_servo(120), -ENOSYS);
    fails += u_expect("fan: servo 150 too soon (rate)", u_cell_servo(150), -ESAFETY);
    fails += u_expect("fan: fan 50 decrease", u_cell_fan(50), -ENOSYS);
    wait_ticks(25);                                           /* past the servo rate window */
    u_test_tele(50, 0, 1);                                    /* a hand: presence (also halts the fan) */
    fails += u_expect("fan: servo 100 with presence", u_cell_servo(100), -ESAFETY);
    wait_ticks(105);                                          /* past the fan rate window */
    u_test_tele(50, 0, 1);
    fails += u_expect("fan: fan 60 with presence", u_cell_fan(60), -ESAFETY);
    u_test_tele(0xFFFF, 0, 1);
    fails += u_expect("fan: fan 60 with no echo", u_cell_fan(60), -ESAFETY);
    fails += u_expect("fan: halt always", u_cell_halt(), -ENOSYS);
    u_test_tele(300, 1, 1);                                   /* E-stop pressed -> latch */
    fails += u_expect("fan: fan 60 under estop", u_cell_fan(60), -ESAFETY);
    fails += u_expect("fan: fan 0 under estop", u_cell_fan(0), -ENOSYS);
    u_test_tele(300, 0, 1);                                   /* released, still latched */
    fails += u_expect("fan: fan 60 still latched", u_cell_fan(60), -ESAFETY);
    u_puts(fails ? "fan: FAIL\n" : "fan: done\n");
    return fails;
}
