/* INV-3 syscall fuzzer: 10,000 calls with random numbers and arguments from a seeded xorshift generator.
 * Console capability only, so nothing can prompt. Every result must be a valid value or an error code; the
 * kernel must not panic and uptime must advance. Calls exit only at the end. */
#include "libu.h"

static uint32_t st = 0x2545F491u;
static uint32_t rnd(void)
{
    st ^= st << 13;
    st ^= st >> 17;
    st ^= st << 5;
    return st;
}

static char valid[64];

int main(void)
{
    for (unsigned i = 0; i < sizeof valid; i++)
        valid[i] = '.';
    uint32_t t0 = u_uptime();
    int bad = 0, ok = 0, eperm = 0, efault = 0, einval = 0, enosys = 0, esafety = 0, other = 0;
    for (int i = 0; i < 10000; i++) {
        uint32_t r = rnd();
        uint32_t nr = (r & 1) ? (rnd() % 14) : rnd();
        if (nr == 0 || (nr >= 100 && nr <= 102))
            nr = 10;                                     /* no exit before the end; no test hooks */
        uint32_t args[4];
        for (int k = 0; k < 4; k++) {
            switch (rnd() % 8) {
            case 0: args[k] = (uint32_t)(uintptr_t)valid; break;
            case 1: args[k] = 0; break;
            case 2: args[k] = 0x00100000u; break;
            case 3: args[k] = USER_TOP - (rnd() % 32); break;
            case 4: args[k] = rnd() % 8; break;
            case 5: args[k] = rnd() % 5000; break;
            case 6: args[k] = (uint32_t)(uintptr_t)valid + rnd() % 4096; break;
            default: args[k] = rnd(); break;
            }
        }
        if (nr == SYS_CONS_WRITE && args[0] == (uint32_t)(uintptr_t)valid)
            args[1] %= 65;                               /* keep the transcript readable */
        int res = syscall4(nr, args[0], args[1], args[2], args[3]);
        if (res >= 0) ok++;
        else if (res == -EPERM) eperm++;
        else if (res == -EFAULT) efault++;
        else if (res == -EINVAL) einval++;
        else if (res == -ENOSYS) enosys++;
        else if (res == -ESAFETY) esafety++;
        else if (res == -EACCES || res == -ECANCELED || res == -ENOENT || res == -ETIMEDOUT) other++;
        else bad++;
    }
    uint32_t t1 = u_uptime();
    u_puts("\nfuzz: calls=10000 ok=");
    u_putdec(ok);
    u_puts(" eperm=");
    u_putdec(eperm);
    u_puts(" efault=");
    u_putdec(efault);
    u_puts(" einval=");
    u_putdec(einval);
    u_puts(" enosys=");
    u_putdec(enosys);
    u_puts(" esafety=");
    u_putdec(esafety);
    u_puts(" other=");
    u_putdec(other);
    u_puts(" bad=");
    u_putdec(bad);
    u_puts(" ticks=");
    u_putdec((int)(t1 - t0));
    u_puts("\n");
    return bad ? 1 : (t1 > t0 ? 0 : 2);
}
