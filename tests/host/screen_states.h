/* The host test snapshots of the status screen (T-08 / T-14 / T-18): the design spec's three mockups with the
 * card's corrections (real call names, fan 0 or 50-80, E-stop never shows EXEC KILLED), T-14's cell panel
 * states, and T-18's `simboot` (the simulated cell right after boot, for the screendump comparison). Included
 * by tests/host/screen_test.c (text cells) and tests/host/gfx_test.c (pixels): one set of fixtures. */
#ifndef RING3_TEST_SCREEN_STATES_H
#define RING3_TEST_SCREEN_STATES_H
#include "policy.h"
#include "screen.h"
#include <stdio.h>
#include <string.h>

#define HZ 100u
#define T(h, m, s) (((h) * 3600u + (m) * 60u + (s)) * HZ)

static void con(struct screen_model *m, const char *const *lines, int n)
{
    for (int i = 0; i < SCR_CON_ROWS; i++)
        m->con[i][0] = 0;
    for (int i = 0; i < n; i++)
        snprintf(m->con[SCR_CON_ROWS - n + i], SCR_CON_COLS + 1, "%s", lines[i]);
}

static void dec(struct screen_model *m, uint32_t tick, uint32_t req, uint32_t nr, uint32_t target, uint8_t code,
                uint8_t reason)
{
    struct scr_decision *d = &m->dec[m->ndec++];
    d->tick = tick;
    d->req = req;
    d->nr = nr;
    d->target = target;
    d->code = code;
    d->reason = reason;
}

static void base(struct screen_model *m)
{
    memset(m, 0, sizeof *m);
    m->hz = HZ;
    m->free_kib = 14920;
    m->link = SCR_LINK_OK;
    m->have_tele = 1;
    m->servo = 90;
}

static void idle(struct screen_model *m)
{
    base(m);
    m->now_tick = T(0, 2, 14);
    m->exec_state = SCR_EXEC_IDLE;
    m->logged = 41;
    dec(m, T(0, 0, 0), 0, SCR_NO_CALL, 0, DEC_BOARD_BOOT, R_BOARD_BOOT);
    dec(m, T(0, 2, 10), 6, SYS_CELL_STATUS, 0, DEC_ALLOW, R_NONE);
    dec(m, T(0, 2, 11), 6, SYS_CELL_SERVO, 90, DEC_ALLOW, R_NONE);
    dec(m, T(0, 2, 12), 6, SYS_CELL_FAN, 0, DEC_ALLOW, R_NONE);
    static const char *const l[] = {
        "agentsh: action 3 fan_set(0) -> 0",
        "OUTCOME DONE: gauge at 90, fan off",
        "EXECUTED 3 operation(s):",
        "END pid=6 EXIT ticks=388 uptime=13300",
        "ring3> ",
    };
    con(m, l, 5);
    m->fan = 0;
    m->dist_mm = 320;
    m->tele_age_ms = 10;
}

static void ask(struct screen_model *m)
{
    base(m);
    m->now_tick = T(0, 3, 9) + 50;
    m->exec_state = SCR_EXEC_RUNNING;
    m->req = 7;
    m->tainted = 1;
    m->pending = 1;
    m->logged = 58;
    dec(m, T(0, 3, 5), 7, SYS_OBJ_READ, OBJ_MAINT_NOTES, DEC_ALLOW, R_NONE);
    dec(m, T(0, 3, 6), 7, SYS_CELL_FAN, 100, DEC_DENY_LIMIT, R_FAN_RANGE);
    dec(m, T(0, 3, 7), 7, SYS_CELL_STATUS, 0, DEC_ALLOW, R_NONE);
    dec(m, T(0, 3, 9), 7, SYS_CELL_FAN, 60, DEC_CONFIRM_PENDING, R_TAINT);
    static const char *const l[] = {
        "agentsh: request \"read the operator notes, then set the fan to 60%\"",
        "agentsh: action 1 read_object(1) -> 216",
        "agentsh: action 2 fan_set(100) -> -200 (ESAFETY)",
        "agentsh: action 3 cell_status(0) -> 16",
        "CONFIRM: agentsh wants cell_fan(60) [tainted]. Allow? [y/N] ",
    };
    con(m, l, 5);
    m->fan = 0;
    m->dist_mm = 220;
    m->tele_age_ms = 40;
}

static void ask_wipe(struct screen_model *m)
{
    ask(m);
    m->now_tick = T(0, 3, 9) + 10;          /* 100 ms after the ASK: rows 3 and 4 coloured, row 5 not yet */
}

static void estop(struct screen_model *m)
{
    base(m);
    m->now_tick = T(0, 3, 12) + 20;
    m->exec_state = SCR_EXEC_RUNNING;       /* correction 3: the E-stop does not kill the executor */
    m->req = 7;
    m->tainted = 1;
    m->logged = 66;
    dec(m, T(0, 3, 7), 7, SYS_CELL_STATUS, 0, DEC_ALLOW, R_NONE);
    dec(m, T(0, 3, 9), 7, SYS_CELL_FAN, 60, DEC_CONFIRM_PENDING, R_TAINT);
    dec(m, T(0, 3, 10), 7, SYS_CELL_FAN, 60, DEC_CONFIRM_YES, R_ANSWER);
    dec(m, T(0, 3, 12), 7, SCR_NO_CALL, 60, DEC_AUTO_HALT, R_ESTOP);
    static const char *const l[] = {
        "agentsh: action 3 cell_status(0) -> 16",
        "CONFIRM: agentsh wants cell_fan(60) [tainted]. Allow? [y/N] y",
        "agentsh: action 4 fan_set(60) -> 0",
        "",
    };
    con(m, l, 4);
    m->fan = 0;
    m->dist_mm = 120;
    m->estop_pressed = 1;
    m->estop_latched = 1;
    m->tele_age_ms = 30;
}

/* T-14: the fan at 60 % at a fixed rotor frame (60 % = 8 ticks per frame; tick / 8 = 1877, frame 1). */
static void fan60(struct screen_model *m)
{
    base(m);
    m->now_tick = T(0, 2, 30) + 16;
    m->exec_state = SCR_EXEC_IDLE;
    m->logged = 47;
    dec(m, T(0, 0, 0), 0, SCR_NO_CALL, 0, DEC_BOARD_BOOT, R_BOARD_BOOT);
    dec(m, T(0, 2, 10), 6, SYS_CELL_STATUS, 0, DEC_ALLOW, R_NONE);
    dec(m, T(0, 2, 11), 6, SYS_CELL_SERVO, 120, DEC_ALLOW, R_NONE);
    dec(m, T(0, 2, 12), 6, SYS_CELL_FAN, 60, DEC_ALLOW, R_NONE);
    static const char *const l[] = {
        "agentsh: action 2 servo_set(120) -> 0",
        "agentsh: action 3 fan_set(60) -> 0",
        "OUTCOME DONE: gauge at 120, fan at 60%",
        "END pid=6 EXIT ticks=402 uptime=15000",
        "ring3> ",
    };
    con(m, l, 5);
    m->fan = 60;
    m->servo = 120;
    m->dist_mm = 250;
    m->tele_age_ms = 20;
}

static void presence(struct screen_model *m)
{
    idle(m);
    m->dist_mm = 70;
}

static void noecho(struct screen_model *m)
{
    idle(m);
    m->dist_mm = DIST_NO_ECHO;
}

/* No accepted frame for 900 ms: the last values stay, drawn grey, the rotor frozen. */
static void stale(struct screen_model *m)
{
    fan60(m);
    m->link = SCR_LINK_STALE;
    m->tele_age_ms = 900;
}

static void nocell(struct screen_model *m)
{
    memset(m, 0, sizeof *m);
    m->hz = HZ;
    m->now_tick = 26;
    m->free_kib = 14936;
    m->link = SCR_LINK_NO_CELL;
    m->dist_mm = DIST_NO_ECHO;
    static const char *const l[] = { "ring3> " };
    con(m, l, 1);
}

/* T-18: the simulated cell right after boot (tests/sim_cell.py defaults: fan 0, servo parked at 90, 300 mm,
 * E-stop released), no request yet: the pictures the QEMU screendump must match pixel for pixel. */
static void simboot(struct screen_model *m)
{
    base(m);
    m->now_tick = 180;
    m->free_kib = 14752;
    m->logged = 3;
    dec(m, 26, 0, SCR_NO_CALL, 0, DEC_BOARD_BOOT, R_BOARD_BOOT);
    static const char *const l[] = { "ring3> " };
    con(m, l, 1);
    m->fan = 0;
    m->servo = 90;
    m->dist_mm = 300;
    m->tele_age_ms = 20;
}

#endif
