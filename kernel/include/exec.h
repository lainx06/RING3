#ifndef RING3_EXEC_H
#define RING3_EXEC_H
#include "trap.h"
#include <stdint.h>

/* One ring-3 executor at a time, run from the kernel loop as a *request* (D-26, D-41, R7): EXEC_START event
 * -> fresh address space -> ring 3 -> EXEC_END event. Quotas are policy state driven by tick events (R2). */
struct uprog {
    const char *name;
    const uint8_t *image;      /* flat binary, D-41 layout */
    uint32_t image_len;        /* bytes of code+rodata+data (file image) */
    uint8_t prog;              /* capability profile (enum pol_prog), fixed at creation (INV-04) */
};

#define USER_BASE   0x40000000u
#define USER_TOP    0x7FFFF000u
#define USER_STACK_TOP   USER_TOP
#define USER_STACK_SIZE  (16u * 1024)
/* The request text for agentsh: a NUL-terminated string the kernel writes into the top stack page before
 * entry (mirrored in user/libu/libu.h); the initial stack pointer sits below it. */
#define USER_ARG_ADDR    (USER_TOP - 1024u)
#define USER_ARG_MAX     512u

/* Production quotas (prov.), fixed in code. Test images may shorten them through boot options. */
#define QUOTA_ACTION_TICKS   500u     /* 5 s per action */
#define QUOTA_REQUEST_TICKS  6000u    /* 60 s per request */
#define CONFIRM_TIMEOUT_TICKS 3000u   /* 30 s for a human answer */

enum exec_result { EXEC_EXIT = 0, EXEC_FAULT = 1, EXEC_LIMIT = 2, EXEC_REFUSED = 3 /* EXEC_START denied: nothing ran */ };

struct exec_outcome {
    enum exec_result kind;
    int exit_code;            /* EXEC_EXIT */
    uint32_t vector, eip;     /* EXEC_FAULT */
    uint32_t ticks_used;
};

/* Runs `p` as one request; `arg` (may be 0) is copied to USER_ARG_ADDR for the program (agentsh's request). */
struct exec_outcome exec_request(const struct uprog *p, const char *arg);
/* The production executor on a console request line; `variant` selects a test-image build of agentsh
 * (0 production, 1 AGENTSH_TEST with the crash op, 2 AGENTSH_TEST + no action cap); test images only. */
int exec_request_agent(const char *text, int variant);
const char *exec_current_name(void);
int exec_current_pid(void);
void exec_action_boundary(void);                 /* agent_recv returned: the action quota restarts (R2) */
void exec_flag_kill(void);                       /* a LIMIT decided in main context on the executor's behalf */

/* Trap-path entry points. */
void exec_on_user_fault(struct trap_frame *f) __attribute__((noreturn));
void exec_on_exit(int code) __attribute__((noreturn));
/* LIMIT decided by the policy on a tick: from ring 3 -> EOI, abandon now; from a kernel wait -> flag. */
void exec_kill_limit(struct trap_frame *f);
int exec_kill_pending(void);
void exec_abandon_limit(void) __attribute__((noreturn));    /* from a kernel wait on the program's behalf */

#ifdef RING3_TEST
int exec_request_by_name(const char *name);      /* 0 if unknown */
void exec_set_quotas(uint32_t action, uint32_t request, uint32_t confirm);
void exec_inject_telemetry(int32_t distance_mm, int32_t estop, int valid);
#endif
#endif
