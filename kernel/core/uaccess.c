/* User pointer validation (INV-3, D-41a). Never dereferences anything itself. */
#include "uaccess.h"
#include "exec.h"
#include "paging.h"
#include "pmm.h"
#include "syscall.h"

int uaccess_check(uint32_t ptr, uint32_t len, int write)
{
    if (len > MAX_SYSCALL_BUF)
        return -EFAULT;
    if (ptr < USER_BASE)
        return -EFAULT;
    if (ptr > USER_TOP)
        return -EFAULT;                 /* checked before the subtraction below so it cannot wrap */
    if (len > USER_TOP - ptr)
        return -EFAULT;
    if (len == 0)
        return 0;
    uint32_t first = ptr & ~0xFFFu;
    uint32_t last = (ptr + len - 1) & ~0xFFFu;
    uint32_t need = PTE_P | PTE_U | (write ? PTE_W : 0);
    for (uint32_t page = first;; page += PAGE_SIZE) {
        if ((paging_lookup(page) & need) != need)
            return -EFAULT;
        if (page == last)
            break;
    }
    return 0;
}
