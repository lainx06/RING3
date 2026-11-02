#ifndef RING3_UACCESS_H
#define RING3_UACCESS_H
#include <stdint.h>
/* Returns 0 if [ptr, ptr+len) is a valid user range, else -EFAULT. Order (D-41a, R3, R5): len cap,
 * ptr >= USER_BASE, ptr < USER_TOP, len <= USER_TOP - ptr, then a walk of every spanned page requiring P|U
 * (plus W when `write`) at both the PDE and the PTE. len == 0 still validates the page at ptr. */
int uaccess_check(uint32_t ptr, uint32_t len, int write);
/* R6: copies a NUL-terminated user string of at most `max` characters into dst (dst holds max + 1 bytes),
 * validating each new page before its first byte is read. Returns the length, -EINVAL if no NUL within max,
 * -EFAULT if a page is not readable user memory. */
int uaccess_copy_string(uint32_t ptr, char *dst, uint32_t max);
#endif
