/* int 0x80 dispatcher: eax = number, ebx ecx edx esi = args, eax = result (negative = -errno). No syscall
 * dereferences a user pointer before uaccess_check (INV-3). */
#include "syscall.h"
#include "exec.h"
#include "kstring.h"
#include "pit.h"
#include "printk.h"
#include "serial.h"
#include "uaccess.h"

static int sys_cons_write(uint32_t buf, uint32_t len)
{
    int r = uaccess_check(buf, len, 0);
    if (r < 0)
        return r;
    char tmp[128];
    for (uint32_t off = 0; off < len; off += sizeof tmp) {
        uint32_t n = len - off < sizeof tmp ? len - off : sizeof tmp;
        memcpy(tmp, (const void *)(uintptr_t)(buf + off), n);
        serial_write(tmp, n);
    }
    return (int)len;
}

/* Test-only: the kernel writes a pattern into a user buffer (exercises the W check). */
static int sys_uread(uint32_t buf, uint32_t len)
{
    int r = uaccess_check(buf, len, 1);
    if (r < 0)
        return r;
    memset((void *)(uintptr_t)buf, 0xA5, len);
    return (int)len;
}

static int logged_pid;

void syscall_dispatch(struct trap_frame *f)
{
    uint32_t nr = f->eax;
    int pid = exec_current_pid();
    if (pid != logged_pid) {                    /* log the CPL seen at the first syscall of each program */
        logged_pid = pid;
        printk("SYSCALL pid=%d nr=%u cpl=%d\n", pid, nr, f->cs & 3);
    }
    switch (nr) {
    case SYS_EXIT:
        exec_on_exit((int)f->ebx);            /* does not return */
    case SYS_CONS_WRITE:
        f->eax = (uint32_t)sys_cons_write(f->ebx, f->ecx);
        break;
    case SYS_UREAD:
        f->eax = (uint32_t)sys_uread(f->ebx, f->ecx);
        break;
    case SYS_UPTIME:
        f->eax = ticks_now();
        break;
    default:
        f->eax = (uint32_t)-ENOSYS;
        break;
    }
}
