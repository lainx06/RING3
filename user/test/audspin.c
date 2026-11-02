/* F1 (D-50): spins on audited syscalls, so a quota tick is likely to land while the kernel is in ring 0
 * draining the audit ring. The request must still end with LIMIT. If the kill is lost, the next call is
 * refused (-EPERM, the request is "killing") and the program exits on its own: END EXIT instead of LIMIT. */
#include "libu.h"
int main(void)
{
    u_puts("audspin: audited syscalls forever\n");
    for (;;) {
        if (u_write("a\n", 2) < 0)
            return 3;                                  /* still alive after a LIMIT decision */
    }
}
