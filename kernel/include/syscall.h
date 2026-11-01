#ifndef RING3_SYSCALL_H
#define RING3_SYSCALL_H
#include "trap.h"

#define SYS_EXIT       0
#define SYS_CONS_WRITE 1
#define SYS_UREAD      2     /* test-only: kernel writes a pattern into a user buffer */
#define SYS_UPTIME     10

#define EPERM   1
#define EACCES  13
#define EFAULT  14
#define EINVAL  22
#define ENOSYS  38

#define MAX_SYSCALL_BUF 4096u

void syscall_dispatch(struct trap_frame *f);
#endif
