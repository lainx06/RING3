/* Host build of the pixel screen renderer (T-18): renders the fixed snapshots of screen_states.h with the
 * 8x16 font given as a hex fixture (the glyphs the kernel read from VGA plane 2, dumped by the test image's
 * `gfxfont` command) and writes the 1024x768 frame to stdout as 4 bytes per pixel, little-endian 0x00RRGGBB
 * (memory order B, G, R, 0: what the framebuffer holds). tests/test_t18_gfx.py hashes it, compares regions
 * with QEMU's screendump and writes the PNGs.
 *
 *   gfx_test <font.hex> <state>              one frame (states as screen_test, plus simboot)
 *   gfx_test <font.hex> <state> tick=<n>     the state at kernel tick n (the rotor turns with the tick)
 *   gfx_test <font.hex> <state> incremental  the state drawn after `idle`: the damaged rectangles as text
 *                                            ("rect x y w h" lines), then the frame
 *   gfx_test <font.hex> <state> budget=<n>   like incremental, with the pixel budget n per call: the calls
 *                                            repeat until nothing is pending ("call" lines between them)
 *   gfx_test <font.hex> fan-count <ticks>    per case: rotor angle changes over <ticks> consecutive ticks
 */
#include "gfx.h"
#include <stdlib.h>

#include "screen_states.h"

static struct gfx_font font;
static uint32_t frame[GFX_W * GFX_H];

static int hexval(int c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

/* "F xx <32 hex digits>" per glyph, as the kernel prints it. */
static int load_font(const char *path)
{
    FILE *fp = fopen(path, "r");
    if (!fp)
        return 0;
    char line[80];
    int seen = 0;
    while (fgets(line, sizeof line, fp)) {
        unsigned ch;
        if (sscanf(line, "F %x ", &ch) != 1 || ch > 255 || strlen(line) < 37)
            continue;
        for (int r = 0; r < 16; r++) {
            int hi = hexval(line[5 + 2 * r]), lo = hexval(line[6 + 2 * r]);
            if (hi < 0 || lo < 0)
                return 0;
            font.rows[ch][r] = (uint8_t)(hi << 4 | lo);
        }
        seen++;
    }
    fclose(fp);
    return seen == 256;
}

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
        uint32_t changes = 0, last = gfx_fan_angle(&m), t0 = m.now_tick, total = 0;
        for (uint32_t t = 1; t <= ticks; t++) {
            m.now_tick = t0 + t;
            uint32_t a = gfx_fan_angle(&m);
            if (a >= GFX_FAN_PERIOD_DEG)
                return 3;
            changes += a != last;
            total += (a + GFX_FAN_PERIOD_DEG - last) % GFX_FAN_PERIOD_DEG;
            last = a;
        }
        printf("%s %u %u\n", cases[i].name, changes, total);
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
    if (argc < 3 || !load_font(argv[1])) {
        fprintf(stderr, "usage: gfx_test <font.hex> <state> [tick=<n>|incremental] | fan-count <ticks>\n");
        return 2;
    }
    if (argc == 4 && strcmp(argv[2], "fan-count") == 0)
        return fan_count((uint32_t)atoi(argv[3]));
    const char *name = argv[2], *opt = argc > 3 ? argv[3] : "";
    for (unsigned i = 0; i < sizeof states / sizeof states[0]; i++) {
        if (strcmp(name, states[i].name) != 0)
            continue;
        static struct screen_model m, before;
        static struct gfx_rect rects[GFX_MAX_RECTS];
        states[i].fill(&m);
        if (strncmp(opt, "tick=", 5) == 0)
            m.now_tick = (uint32_t)atoi(opt + 5);
        uint32_t pending = 0;
        if (strcmp(opt, "incremental") == 0 || strncmp(opt, "budget=", 7) == 0) {
            uint32_t budget = opt[0] == 'b' ? (uint32_t)atoi(opt + 7) : 0;
            idle(&before);
            gfx_render(&before, 0, &font, frame, rects, &pending, 0);
            for (int call = 0; call < 64; call++) {
                uint32_t n = gfx_render(&m, &before, &font, frame, rects, &pending, budget);
                for (uint32_t k = 0; k < n && k < GFX_MAX_RECTS; k++)
                    printf("rect %u %u %u %u\n", rects[k].x, rects[k].y, rects[k].w, rects[k].h);
                before = m;                                 /* the driver's prev: the last snapshot */
                if (!pending)
                    break;
                printf("call\n");
            }
            printf("end\n");
            fflush(stdout);
        } else {
            uint32_t n = gfx_render(&m, 0, &font, frame, rects, &pending, 0);
            if (n != 1 || rects[0].w != GFX_W || rects[0].h != GFX_H)
                return 3;
        }
        for (uint32_t k = 0; k < GFX_W * GFX_H; k++) {
            uint32_t px = frame[k];
            putchar((int)(px & 0xFF));
            putchar((int)(px >> 8 & 0xFF));
            putchar((int)(px >> 16 & 0xFF));
            putchar(0);
        }
        return 0;
    }
    fprintf(stderr, "unknown state %s\n", name);
    return 2;
}
