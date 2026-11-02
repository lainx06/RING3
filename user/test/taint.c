/* INV-15 / A6 taint demo: a scratch write is allowed, reading maint_notes taints, then the same write needs
 * a human answer (the harness answers), and audit_log is still denied. */
#include "libu.h"
int main(void)
{
    static char buf[2100];
    int fails = 0;
    fails += u_expect("taint: write scratch_0 before", u_obj_write(OBJ_SCRATCH_0, "before", 6), 6);
    int n = u_obj_read(OBJ_MAINT_NOTES, buf, sizeof buf);
    u_puts("taint: maint_notes=");
    if (n > 0)
        u_write(buf, (size_t)n);
    u_puts("taint: now tainted; writing scratch_0 (prompt expected)\n");
    int r = u_obj_write(OBJ_SCRATCH_0, "after", 5);
    u_puts("taint: write scratch_0 after=");
    u_putdec(r);
    u_puts("\n");
    fails += u_expect("taint: write audit_log", u_obj_write(OBJ_AUDIT_LOG, "erase", 5), -EACCES);
    fails += u_expect("taint: read cell_spec still allowed", u_obj_read(OBJ_CELL_SPEC, buf, 64) > 0, 1);
    u_puts(fails ? "taint: FAIL\n" : "taint: done\n");
    return fails;
}
