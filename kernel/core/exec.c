#include "exec.h"
#include "panic.h"
void exec_on_user_fault(struct trap_frame *f) { panic_trap(f); }
void exec_on_tick_user(struct trap_frame *f) { (void)f; }
