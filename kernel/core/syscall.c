#include "syscall.h"
#include "panic.h"
void syscall_dispatch(struct trap_frame *f) { panic("syscall before exec exists, eip=0x%08x", f->eip); }
