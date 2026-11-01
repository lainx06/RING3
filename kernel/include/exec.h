#ifndef RING3_EXEC_H
#define RING3_EXEC_H
#include "trap.h"
#include <stdint.h>

/* One ring-3 program at a time, run from the kernel loop (D-26, D-41). */
struct uprog {
    const char *name;
    const uint8_t *image;      /* flat binary, D-41 layout */
    uint32_t image_len;        /* bytes of code+rodata+data (file image) */
    uint32_t ro_len;           /* code + rodata bytes (page-aligned), mapped read-only */
    uint32_t mem_len;          /* code + rodata + data + bss bytes (page-aligned) */
};

#define USER_BASE   0x40000000u
#define USER_TOP    0x7FFFF000u
#define USER_STACK_TOP   USER_TOP
#define USER_STACK_SIZE  (16u * 1024)
#define TICK_BUDGET 100u        /* (prov.) */

enum exec_result { EXEC_EXIT = 0, EXEC_FAULT = 1, EXEC_LIMIT = 2 };

struct exec_outcome {
    enum exec_result kind;
    int exit_code;            /* EXEC_EXIT */
    uint32_t vector, eip;     /* EXEC_FAULT */
    uint32_t ticks_used;
};

/* Runs `p` in ring 3 and returns when it exits, faults or exceeds its tick budget. */
struct exec_outcome exec_run(const struct uprog *p, int pid);

/* Called from the trap dispatcher; neither returns to the caller (they return to exec_run's saved context). */
void exec_on_user_fault(struct trap_frame *f) __attribute__((noreturn));
void exec_on_tick_user(struct trap_frame *f);     /* returns only while under budget */
void exec_on_exit(int code) __attribute__((noreturn));
int exec_current_pid(void);
#endif
