/* INV-4 object rules in QEMU: trusted reads don't taint, denied writes, bad ids, scratch round trip. */
#include "libu.h"
int main(void)
{
    static char buf[2100];
    int fails = 0;
    int n = u_obj_list(buf, sizeof buf);
    u_puts("objs: list=\n");
    if (n > 0)
        u_write(buf, (size_t)n);
    fails += u_expect("objs: read cell_spec>0", u_obj_read(OBJ_CELL_SPEC, buf, sizeof buf) > 0, 1);
    fails += u_expect("objs: write cell_spec", u_obj_write(OBJ_CELL_SPEC, "x", 1), -EACCES);
    fails += u_expect("objs: write audit_log", u_obj_write(OBJ_AUDIT_LOG, "x", 1), -EACCES);
    fails += u_expect("objs: read id 7", u_obj_read(7, buf, 16), -EINVAL);
    fails += u_expect("objs: read id -1", u_obj_read(-1, buf, 16), -EINVAL);
    fails += u_expect("objs: write 2049 bytes", u_obj_write(OBJ_SCRATCH_1, buf, 2049), -EINVAL);
    fails += u_expect("objs: write scratch_1", u_obj_write(OBJ_SCRATCH_1, "hello object", 12), 12);
    fails += u_expect("objs: write scratch_1 again (untainted)", u_obj_write(OBJ_SCRATCH_1, "x", 1), 1);
    fails += u_expect("objs: read audit_log>0 (trusted, no taint)", u_obj_read(OBJ_AUDIT_LOG, buf, sizeof buf) > 0, 1);
    fails += u_expect("objs: list too small", u_obj_list(buf, 8), -EINVAL);
    fails += u_expect("objs: read scratch_1 (untrusted: taints)", u_obj_read(OBJ_SCRATCH_1, buf, sizeof buf), 1);
    u_puts(fails ? "objs: FAIL\n" : "objs: all ok\n");
    return fails;
}
