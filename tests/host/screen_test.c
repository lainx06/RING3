/* Host build of the status screen renderer (T-08): renders fixed snapshots and writes the 25x80 cells
 * (little-endian u16: CP437 char | attribute << 8) of each to stdout, one state after another. The states are
 * the design spec's three mockups with the card's corrections (real call names, fan 0 or 50-80, E-stop never
 * shows EXEC KILLED) and T-14's cell panel states; tests/test_t08_screen.py and tests/test_t14_panel.py hold the
 * expected text and colours.
 *
 *   screen_test idle | ask | estop | ask-wipe | nocell | fan60 | presence | noecho | stale | simboot
 *   screen_test servo=<deg>          the idle state with the reported angle <deg>
 *   screen_test fan-count <ticks>    per case: frame changes over <ticks> consecutive ticks (text)
 */
#include <stdlib.h>

#include "screen_states.h"

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
                   { "stale", stale },       { "simboot", simboot } };
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
