/* int 0x80 dispatcher (ARCHITECTURE §3): eax = number, ebx ecx edx esi = args, eax = result (negative =
 * -errno). Order per call (R3, R5): uaccess on every user pointer -> one policy event through decide() ->
 * prompt if CONFIRM_PENDING -> execute only on ALLOW/CONFIRM_YES. No syscall dereferences a user pointer
 * before uaccess passed (INV-3). Numbers without a driver yet (cell_*, agent_*) return -ENOSYS after the
 * decision. */
#include "syscall.h"
#include "audit.h"
#include "confirm.h"
#include "exec.h"
#include "kstring.h"
#include "objstore.h"
#include "pit.h"
#include "printk.h"
#include "serial.h"
#include "uaccess.h"

#define CELL_STATUS_BYTES 16u        /* fixed struct size reserved for T-04 */
#define SYSCALL_DRAIN_MAX 4u         /* audit records printed per syscall return (F5): >= 1 produced per call */

static int32_t do_cons_write(uint32_t buf, uint32_t len)
{
    char tmp[128];
    for (uint32_t off = 0; off < len; off += sizeof tmp) {
        uint32_t n = len - off < sizeof tmp ? len - off : sizeof tmp;
        memcpy(tmp, (const void *)(uintptr_t)(buf + off), n);
        serial_write(tmp, n);
    }
    return (int32_t)len;
}

static int32_t do_obj_list(uint32_t buf, uint32_t len)
{
    char tmp[512];
    int n = objstore_list(tmp, sizeof tmp);
    if (n < 0)
        return n;
    if ((uint32_t)n > len)
        return -EINVAL;
    memcpy((void *)(uintptr_t)buf, tmp, (uint32_t)n);
    return n;
}

static int32_t do_obj_read(uint32_t id, uint32_t buf, uint32_t len)
{
    return (int32_t)objstore_read(id, (void *)(uintptr_t)buf, len);
}

static int32_t do_obj_write(uint32_t id, uint32_t buf, uint32_t len)
{
    static uint8_t tmp[OBJ_DATA_MAX];
    memcpy(tmp, (const void *)(uintptr_t)buf, len);
    objstore_write(id, tmp, len);
    return (int32_t)len;
}

static int32_t execute(uint32_t nr, uint32_t b, uint32_t c, uint32_t d)
{
    switch (nr) {
    case SYS_CONS_WRITE: return do_cons_write(b, c);
    case SYS_OBJ_LIST:   return do_obj_list(b, c);
    case SYS_OBJ_READ:   return do_obj_read(b, c, d);
    case SYS_OBJ_WRITE:  return do_obj_write(b, c, d);
    case SYS_UPTIME:     return (int32_t)ticks_now();
    default:             return -ENOSYS;        /* cell_*, agent_*: drivers arrive in T-04 / T-03b */
    }
}

static int logged_pid;

static void dispatch(struct trap_frame *f)
{
    uint32_t nr = f->eax, b = f->ebx, c = f->ecx, d = f->edx, s = f->esi;
    int pid = exec_current_pid();
    if (pid != logged_pid) {                    /* log the CPL seen at the first syscall of each program */
        logged_pid = pid;
        printk("SYSCALL pid=%d nr=%u cpl=%d\n", pid, nr, f->cs & 3);
    }
#ifdef RING3_TEST
    /* Test-image instrumentation (not in production builds, not executor authority). */
    if (nr == SYS_TEST_UREAD) {
        int r = uaccess_check(b, c, 1);
        if (r == 0)
            memset((void *)(uintptr_t)b, 0xA5, c);
        f->eax = (uint32_t)(r < 0 ? r : (int)c);
        return;
    }
    if (nr == SYS_TEST_BOUNDARY) {
        exec_action_boundary();
        f->eax = 0;
        return;
    }
    if (nr == SYS_TEST_TELE) {
        exec_inject_telemetry((int32_t)b, (int32_t)c, (int)d);
        f->eax = 0;
        return;
    }
#endif
    struct pol_event ev = { EV_SYSCALL, 0, 0, 0, ticks_now(), nr, { (int32_t)b, (int32_t)c, (int32_t)d, (int32_t)s } };
    char text[LCD_TEXT_MAX + 1];
    int ua = 0;
    switch (nr) {
    case SYS_CONS_WRITE:
    case SYS_AGENT_SEND:  ua = uaccess_check(b, c, 0); break;
    case SYS_OBJ_LIST:
    case SYS_AGENT_RECV:  ua = uaccess_check(b, c, 1); break;
    case SYS_OBJ_READ:    ua = uaccess_check(c, d, 1); break;
    case SYS_OBJ_WRITE:   ua = uaccess_check(c, d, 0); break;
    case SYS_CELL_STATUS: ua = uaccess_check(b, CELL_STATUS_BYTES, 1); break;
    case SYS_CELL_LCD: {
        int r = uaccess_copy_string(c, text, LCD_TEXT_MAX);
        if (r == -EINVAL) {
            ev.flags |= EVF_STR_BAD;
        } else if (r < 0) {
            ua = r;
        } else {
            for (int i = 0; i < r; i++)
                if (text[i] < 32 || text[i] > 126)
                    ev.flags |= EVF_STR_BAD;     /* printable ASCII only on the panel */
        }
        break;
    }
    default:
        break;
    }
    if (ua == 0)
        ev.flags |= EVF_UACCESS_OK;

    struct pol_decision dec;
    int idx = audit_feed(&ev, &dec);
    if (dec.action == ACT_PROMPT) {
        confirm_run(&ev, &dec);                  /* may not return (LIMIT); flushes before the prompt */
        idx = -1;
    }
    if (dec.action != ACT_EXEC) {
        /* A call that is not executed never reports a non-negative result (F4, D-52): DEC_NONE from a stray
         * answer or any silent decision reads as a cancelled call. */
        f->eax = (uint32_t)(dec.result < 0 ? dec.result : -ECANCELED);
        audit_drain(SYSCALL_DRAIN_MAX);
        return;
    }
    if (nr == SYS_EXIT)
        exec_on_exit((int)b);                    /* does not return; EXEC_END flushes everything */
    int32_t r = execute(nr, b, c, d);
    audit_set_result(idx, r);
    f->eax = (uint32_t)r;
    audit_drain(SYSCALL_DRAIN_MAX);              /* F5: bounded, polled, interrupts stay off */
}

void syscall_dispatch(struct trap_frame *f)
{
    dispatch(f);
    /* F1 (D-50): a quota tick that arrived while this call was in ring 0 (a confirmation wait, a drain)
     * could only flag the kill. The single ring-0 -> ring-3 return path for a syscall is here, so the
     * request ends here instead of resuming the program (R2, INV-07). */
    if (exec_kill_pending())
        exec_abandon_limit();                    /* does not return */
}
