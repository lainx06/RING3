/* INV-4: an executor with an empty capability set. It can't print, so the exit code carries the result:
 * 0 = every capability-guarded call returned -EPERM and the cap-free calls behaved. */
#include "libu.h"
int main(void)
{
    char buf[64];
    int bad = 0;
    if (u_write("x", 1) != -EPERM) bad |= 1;
    if (u_obj_list(buf, sizeof buf) != -EPERM) bad |= 2;
    if (u_obj_read(OBJ_CELL_SPEC, buf, sizeof buf) != -EPERM) bad |= 4;
    if (u_obj_write(OBJ_SCRATCH_0, buf, 4) != -EPERM) bad |= 8;
    if (u_cell_fan(60) != -EPERM) bad |= 16;
    if (u_cell_servo(100) != -EPERM) bad |= 32;
    if (u_cell_lcd(0, "hi") != -EPERM) bad |= 64;
    if (syscall2(SYS_CELL_STATUS, (uint32_t)(uintptr_t)buf, 0) != -EPERM) bad |= 128;
    if (syscall2(SYS_AGENT_SEND, (uint32_t)(uintptr_t)buf, 4) != -EPERM) bad |= 256;
    if (u_cell_halt() != -ENOSYS) bad |= 512;          /* always allowed; no driver yet */
    if (u_uptime() == 0) bad |= 1024;
    return bad;
}
