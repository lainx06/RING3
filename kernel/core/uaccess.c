/* User pointer validation (INV-3, D-41a, R3, R5, R6). Never dereferences anything before the page check. */
#include "uaccess.h"
#include "exec.h"
#include "paging.h"
#include "pmm.h"
#include "syscall.h"

static int page_ok(uint32_t page, uint32_t need)
{
    return (paging_lookup(page) & need) == need;     /* paging_lookup folds the PDE's P|U|W into the PTE */
}

int uaccess_check(uint32_t ptr, uint32_t len, int write)
{
    if (len > MAX_SYSCALL_BUF)
        return -EFAULT;
    if (ptr < USER_BASE)
        return -EFAULT;
    if (ptr >= USER_TOP)
        return -EFAULT;                 /* checked before the subtraction below so it cannot wrap */
    if (len > USER_TOP - ptr)
        return -EFAULT;
    uint32_t first = ptr & ~0xFFFu;
    uint32_t last = (len ? ptr + len - 1 : ptr) & ~0xFFFu;      /* R5: len == 0 validates the page at ptr */
    uint32_t need = PTE_P | PTE_U | (write ? PTE_W : 0);
    for (uint32_t page = first;; page += PAGE_SIZE) {
        if (!page_ok(page, need))
            return -EFAULT;
        if (page == last)
            break;
    }
    return 0;
}

int uaccess_copy_string(uint32_t ptr, char *dst, uint32_t max)
{
    uint32_t need = PTE_P | PTE_U;
    uint32_t checked_page = 0;
    for (uint32_t i = 0; i <= max; i++) {
        uint32_t p = ptr + i;
        if (p < ptr || p < USER_BASE || p >= USER_TOP)
            return -EFAULT;
        uint32_t page = p & ~0xFFFu;
        if (i == 0 || page != checked_page) {
            if (!page_ok(page, need))
                return -EFAULT;
            checked_page = page;
        }
        char c = *(const char *)(uintptr_t)p;
        if (c == 0) {
            dst[i] = 0;
            return (int)i;
        }
        if (i == max)
            return -EINVAL;             /* no NUL within max characters */
        dst[i] = c;
    }
    return -EINVAL;
}
