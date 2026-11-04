/* The kernel's 80x25 VGA text status screen (T-08, D-66, D-66a, D-67): a pure renderer. `screen_render()` turns
 * a plain snapshot of existing kernel and cell state into 2000 VGA text cells (CP437 character | attribute
 * << 8). No I/O, no globals, no clock: the time is a field of the snapshot. Built for the kernel and, unchanged,
 * for the host golden tests (tests/host/screen_test.c), like decide().
 *
 * Every word shown is backed by a field below; the verdict words and reason phrases come from words.c, the
 * table the LCD panel uses. Nothing here decides anything. */
#ifndef RING3_UI_SCREEN_H
#define RING3_UI_SCREEN_H
#include <stdint.h>

#define SCR_ROWS 25
#define SCR_COLS 80
#define SCR_CON_ROWS 5          /* console window: screen rows 7-11 (T-14: the cell panel took 2 rows) */
#define SCR_CON_COLS 76         /* columns 3-78 */
#define SCR_DECISIONS 4         /* the ticker: rows 21-24 */
#define SCR_NO_CALL 0xFFFFFFFFu /* scr_decision.nr when the decision is not about a call */
#define SCR_DIST_SPAN_MM 400u   /* sensor beam: 0-400 mm, longer = full beam (T-08 correction 5) */
/* The banner colours reach rows 3, 4 and 5 at ~0, 70 and 140 ms after the decision (motion table). */
#define SCR_WIPE_ROW4_MS 70u
#define SCR_WIPE_ROW5_MS 140u

enum scr_exec { SCR_EXEC_IDLE = 0, SCR_EXEC_RUNNING = 1, SCR_EXEC_KILLED = 2 };
enum scr_kill { SCR_KILL_NONE = 0, SCR_KILL_LIMIT = 1, SCR_KILL_FAULT = 2 };
enum scr_link { SCR_LINK_NO_CELL = 0, SCR_LINK_WAITING = 1, SCR_LINK_OK = 2, SCR_LINK_STALE = 3,
                SCR_LINK_LOST = 4 /* the board's own link watchdog fired (STATE flag) */ };

/* One audited kernel decision, as the audit path recorded it. */
struct scr_decision {
    uint32_t tick;              /* kernel tick of the event */
    uint32_t req;               /* request id (executor pid), 0 = none */
    uint32_t nr;                /* syscall number, PEND_ESTOP_RESET (console reset), or SCR_NO_CALL */
    uint32_t target;            /* the decision's target: duty, degrees, object id, line */
    uint8_t code, reason;       /* DEC_*, R_* */
    uint8_t pad[2];
};

struct screen_model {
    uint32_t now_tick;          /* kernel uptime in ticks */
    uint32_t hz;                /* ticks per second (POLICY_HZ) */
    /* executor */
    uint8_t exec_state;         /* SCR_EXEC_* */
    uint8_t kill_kind;          /* SCR_KILL_* when exec_state == SCR_EXEC_KILLED */
    uint8_t tainted;            /* the policy's taint flag */
    uint8_t pending;            /* a confirmation is waiting for its answer */
    uint32_t req;               /* request id while running / of the killed request */
    uint32_t kill_vector;       /* SCR_KILL_FAULT: the CPU exception vector */
    uint32_t free_kib;          /* frame allocator: free frames x 4 KiB */
    uint32_t logged;            /* audit sequence: decisions logged since boot */
    /* decisions, oldest first; dec[ndec - 1] is the banner */
    uint32_t ndec;
    struct scr_decision dec[SCR_DECISIONS];
    /* console window: rows oldest first, NUL-terminated, the last row is the line being written */
    char con[SCR_CON_ROWS][SCR_CON_COLS + 1];
    /* the cell, as the board last reported it (STATE frame) and as the kernel sees the link */
    uint8_t link;               /* SCR_LINK_* */
    uint8_t have_tele;          /* a STATE frame of this boot has been accepted */
    uint8_t fan, servo;         /* reported duty %, reported commanded angle (deg) */
    uint16_t dist_mm;           /* 0xFFFF = no echo */
    uint8_t estop_pressed;      /* board: button pressed now */
    uint8_t estop_latched;      /* kernel latch (policy state) or the board's latch flag */
    uint32_t tele_age_ms;       /* since the last accepted frame */
};

/* Fills cells[row][col] (0-based) for the snapshot `m`. */
void screen_render(const struct screen_model *m, uint16_t cells[SCR_ROWS][SCR_COLS]);

/* T-14 cell panel: the fan rotor's frame (0..SCR_FAN_FRAMES-1) for the snapshot, from the reported duty and the
 * tick count only; 0 (the rest frame) while frozen (no telemetry, duty 0, E-stop latched, link STALE or LOST).
 * `screen_fan_ticks` is the fixed table: ticks per frame for a duty, 0 = still. */
#define SCR_FAN_FRAMES 4
uint32_t screen_fan_ticks(uint8_t duty);
uint32_t screen_fan_frame(const struct screen_model *m);

/* VGA attributes (foreground | background << 4); blinking is off, so backgrounds 8-15 are bright colours. */
#define SCR_ATTR(fg, bg) ((uint8_t)((fg) | ((bg) << 4)))
#define SCR_BLACK 0
#define SCR_DARK_RED 4
#define SCR_LIGHT_CYAN 11
#define SCR_LIGHT_RED 12
#define SCR_YELLOW 14
#define SCR_WHITE 15
#endif
