/* Host build of the status screen renderer (T-08): renders fixed snapshots and writes the 25x80 cells
 * (little-endian u16: CP437 char | attribute << 8) of each to stdout, one state after another. The states are
 * the design spec's three mockups with the card's corrections (real call names, fan 0 or 50-80, E-stop never
 * shows EXEC KILLED) and T-14's cell panel states; tests/test_t08_screen.py and tests/test_t14_panel.py hold the
 * expected text and colours.
 *
 *   screen_test idle | ask | estop | ask-wipe | nocell | fan60 | presence | noecho | stale   one state
 *   screen_test servo=<deg>          the idle state with the reported angle <deg>
 *   screen_test fan-count <ticks>    per case: frame changes over <ticks> consecutive ticks (text)
 */
#include "policy.h"
#include "screen.h"
#include <stdio.h>
#include <stdlib.h>
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

/* Rotor frame changes over `ticks` consecutive ticks, per case: "<case> <changes>" lines. */
static int fan_count(uint32_t ticks)
{
    static const struct {
        const char *name;
        uint8_t duty, latched, link;
    } cases[] = { { "duty0", 0, 0, SCR_LINK_OK },        { "duty40", 40, 0, SCR_LINK_OK },
                  { "duty60", 60, 0, SCR_LINK_OK },      { "duty80", 80, 0, SCR_LINK_OK },
                  { "duty80-latched", 80, 1, SCR_LINK_OK }, { "duty80-stale", 80, 0, SCR_LINK_STALE },
                  { "duty80-lost", 80, 0, SCR_LINK_LOST } };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        static struct screen_model m;
        idle(&m);
        m.fan = cases[i].duty;
        m.estop_latched = cases[i].latched;
        m.link = cases[i].link;
        uint32_t changes = 0, last = screen_fan_frame(&m);
        uint32_t t0 = m.now_tick;
        for (uint32_t t = 1; t <= ticks; t++) {
            m.now_tick = t0 + t;
            uint32_t f = screen_fan_frame(&m);
            if (f >= SCR_FAN_FRAMES)
                return 3;
            changes += f != last;
            last = f;
        }
        printf("%s %u\n", cases[i].name, changes);
    }
    return 0;
}

int main(int argc, char **argv)
{
    static const struct {
        const char *name;
        void (*fill)(struct screen_model *);
    } states[] = { { "idle", idle },         { "ask", ask },     { "estop", estop },   { "ask-wipe", ask_wipe },
                   { "nocell", nocell },     { "fan60", fan60 }, { "presence", presence }, { "noecho", noecho },
                   { "stale", stale } };
    if (argc == 3 && strcmp(argv[1], "fan-count") == 0)
        return fan_count((uint32_t)atoi(argv[2]));
    if (argc != 2) {
        fprintf(stderr, "usage: screen_test <state> | servo=<deg> | fan-count <ticks>\n");
        return 2;
    }
    int servo = -1;
    const char *name = argv[1];
    if (strncmp(name, "servo=", 6) == 0) {
        servo = atoi(name + 6);
        name = "idle";
    }
    for (unsigned i = 0; i < sizeof states / sizeof states[0]; i++) {
        if (strcmp(name, states[i].name) != 0)
            continue;
        static struct screen_model m;
        static uint16_t cells[SCR_ROWS][SCR_COLS];
        states[i].fill(&m);
        if (servo >= 0)
            m.servo = (uint8_t)servo;
        screen_render(&m, cells);
        for (int r = 0; r < SCR_ROWS; r++)
            for (int c = 0; c < SCR_COLS; c++) {
                putchar(cells[r][c] & 0xFF);
                putchar(cells[r][c] >> 8);
            }
        return 0;
    }
    fprintf(stderr, "unknown state %s\n", argv[1]);
    return 2;
}
