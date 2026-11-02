#ifndef RING3_LIBU_H
#define RING3_LIBU_H
#include <stddef.h>
#include <stdint.h>

/* Syscall ABI (ARCHITECTURE §3): eax = number, ebx ecx edx esi = args, eax = result (negative = -errno). */
#define SYS_EXIT 0
#define SYS_CONS_WRITE 1
#define SYS_OBJ_LIST 2
#define SYS_OBJ_READ 3
#define SYS_OBJ_WRITE 4
#define SYS_CELL_STATUS 5
#define SYS_CELL_FAN 6
#define SYS_CELL_SERVO 7
#define SYS_CELL_HALT 8
#define SYS_CELL_LCD 9
#define SYS_UPTIME 10
#define SYS_AGENT_SEND 11
#define SYS_AGENT_RECV 12
/* test-image instrumentation */
#define SYS_TEST_UREAD 100
#define SYS_TEST_BOUNDARY 101
#define SYS_TEST_TELE 102

#define EPERM 1
#define ENOENT 2
#define EACCES 13
#define EFAULT 14
#define EINVAL 22
#define ENOSYS 38
#define ETIMEDOUT 110
#define ECANCELED 125
#define ESAFETY 200

#define OBJ_CELL_SPEC 0
#define OBJ_MAINT_NOTES 1
#define OBJ_SCRATCH_0 2
#define OBJ_SCRATCH_1 3
#define OBJ_AUDIT_LOG 6

#define USER_BASE 0x40000000u
#define USER_TOP 0x7FFFF000u
/* The request text the kernel places for agentsh (kernel/include/exec.h USER_ARG_ADDR), NUL-terminated. */
#define USER_ARG_ADDR (USER_TOP - 1024u)
#define USER_ARG_MAX 512u

static inline int syscall4(uint32_t nr, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    int r;
    __asm__ volatile("int $0x80" : "=a"(r) : "a"(nr), "b"(a), "c"(b), "d"(c), "S"(d) : "memory");
    return r;
}
static inline int syscall2(uint32_t nr, uint32_t a, uint32_t b) { return syscall4(nr, a, b, 0, 0); }

void u_exit(int code) __attribute__((noreturn));
int u_write(const char *s, size_t n);
int u_puts(const char *s);
void u_puthex(uint32_t v);
void u_putdec(int v);
int u_uread(void *buf, size_t n);
uint32_t u_uptime(void);
size_t u_strlen(const char *s);
int u_obj_list(char *buf, size_t n);
int u_obj_read(int id, void *buf, size_t n);
int u_obj_write(int id, const void *buf, size_t n);
int u_cell_fan(int duty);
int u_cell_servo(int deg);
int u_cell_halt(void);
int u_cell_lcd(int line, const char *text);
int u_cell_status(void *buf16);
/* COM2 link (CAP_AGENT_LINK): send a REQUEST or OBSERVATION body; receive one bridge frame as
 * type u8 | seq u16 | len u16 | body (cap >= 206), waiting at most timeout_ticks. */
int u_agent_send(uint32_t type, const void *body, size_t n);
int u_agent_recv(void *buf, size_t cap, uint32_t timeout_ticks);
int u_test_tele(int distance_mm, int estop, int valid);
int u_test_boundary(void);
/* prints "<label>=<r>" and returns 1 when r != expect */
int u_expect(const char *label, int r, int expect);
#endif
