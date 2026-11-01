#ifndef RING3_UACCESS_H
#define RING3_UACCESS_H
#include <stdint.h>
/* Returns 0 if [ptr, ptr+len) is a valid user range (every page P|U, plus W when write), else -EFAULT.
 * Order (D-41a): len cap, ptr >= USER_BASE, ptr <= USER_TOP, len <= USER_TOP - ptr, page walk. */
int uaccess_check(uint32_t ptr, uint32_t len, int write);
#endif
