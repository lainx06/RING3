/* The pixel status screen renderer: see gfx.h. Layout: the text screen's regions (status bar, decision
 * banner, console window, the cell panel, the decision ticker) at 1024x768. Integer maths only; the shapes
 * are filled polygons, discs, rings and spans. Colours follow the text screen's meaning (D-66a: black,
 * amber, light cyan; inverse boxes for ASK and the alarm colours), one word for every state. */
#include "gfx.h"
#include "policy.h"
#include "words.h"

/* ---- colours (0x00RRGGBB) ---------------------------------------------------------------------------- */
#define C_BG 0x000000u
#define C_AMBER 0xFFB000u       /* frame, rules, labels */
#define C_TEXT 0xE8E8E8u
#define C_CYAN 0x55FFFFu        /* information */
#define C_ASK_BG 0xFFD000u
#define C_ASK_FG 0x000000u
#define C_ALARM_BG 0xAA0000u
#define C_ALARM_FG 0xFFFFFFu
#define C_DIM 0x555555u
#define C_GREY 0xAAAAAAu
#define C_LRED 0xFF5555u
#define C_DRED 0xAA0000u
#define C_BROWN 0xAA5500u
#define C_WHITE 0xFFFFFFu
#define NO_BG 0xFFFFFFFFu       /* text without a background box */

/* ---- layout (pixels) ---------------------------------------------------------------------------------- */
#define GLYPH_W 8
#define GLYPH_H 16
#define INNER_X 4
#define INNER_W (GFX_W - 8)
#define RULE1_Y 88              /* under the status bar */
#define RULE2_Y 190             /* under the banner */
#define RULE3_Y 290             /* under the console */
#define RULE4_Y 652             /* under the cell panel */
#define TITLE_Y 12
#define CLOCK_X 832
#define STATUS_Y 52
#define BANNER_Y 92             /* three 32 px strips */
#define CONSOLE_Y 210           /* SCR_CON_ROWS rows of 16 px */
#define PANEL_Y 292
#define SEP1_X 304              /* fan | gauge */
#define SEP2_X 688              /* gauge | sensor */
#define FAN_CX 152
#define FAN_CY 440
#define FAN_R 68
#define FAN_HUB_R 14
#define FAN_LABEL_Y 556
#define FAN_WORD_Y 596
#define GAUGE_CX 496
#define GAUGE_CY 532
#define GAUGE_R 150
#define GAUGE_BAND 6            /* arc thickness */
#define GAUGE_MARK_R0 128
#define GAUGE_NEEDLE_R 120
#define GAUGE_LABEL_Y 556
#define SENSOR_X 704
#define SENSOR_TEXT_Y 310
#define SENSOR_WORD_Y 350
#define BEAM_X 720
#define BEAM_Y 390
#define BEAM_W 280              /* 0..SCR_DIST_SPAN_MM over 280 px */
#define BEAM_H 16
#define LAMP_CX 724
#define LAMP_CY 484
#define LAMP_R 18
#define LAMP_TEXT_X 752
#define TICKER_Y 676
#define TICKER_ROW_H 20

/* The damage rectangles, one per region (x, y, w, h). */
#define R_CLOCK CLOCK_X, TITLE_Y, 176, 32
#define R_STATUS_L INNER_X, STATUS_Y, 396, 32
#define R_STATUS_R 400, STATUS_Y, (GFX_W - INNER_X - 400), 32
#define R_BANNER INNER_X, BANNER_Y, INNER_W, 96
#define R_CONSOLE INNER_X, CONSOLE_Y, INNER_W, (SCR_CON_ROWS * GLYPH_H)
#define R_FAN (FAN_CX - FAN_R), (FAN_CY - FAN_R), (2 * FAN_R), (2 * FAN_R)
#define R_FAN_TEXT INNER_X, FAN_LABEL_Y, (SEP1_X - INNER_X), 72
#define R_GAUGE_TEXT (SEP1_X + 2), GAUGE_LABEL_Y, (SEP2_X - SEP1_X - 4), 32
#define R_SENSOR_TEXT (SEP2_X + 2), SENSOR_TEXT_Y, (GFX_W - INNER_X - SEP2_X - 2), (BEAM_Y - 4 - SENSOR_TEXT_Y)
#define R_BEAM (SEP2_X + 2), (BEAM_Y - 4), (GFX_W - INNER_X - SEP2_X - 2), 44
#define R_LAMP (SEP2_X + 2), (LAMP_CY - LAMP_R - 6), (GFX_W - INNER_X - SEP2_X - 2), (2 * LAMP_R + 10)
#define AGE_Y (LAMP_CY + 22)
#define R_AGE LAMP_TEXT_X, AGE_Y, (GFX_W - INNER_X - LAMP_TEXT_X), GLYPH_H
#define NEEDLE_CLEAR_R 132      /* the needle region: a disc inside the arc (inner radius 144) */
#define R_NEEDLE (GAUGE_CX - NEEDLE_CLEAR_R - 2), (GAUGE_CY - NEEDLE_CLEAR_R - 2), (2 * NEEDLE_CLEAR_R + 4), (NEEDLE_CLEAR_R + 12)
#define R_TICKER INNER_X, TICKER_Y, INNER_W, (SCR_DECISIONS * TICKER_ROW_H)

/* ---- small helpers (freestanding: no libc) ------------------------------------------------------------ */
static int differ(const void *a, const void *b, uint32_t n)
{
    const uint8_t *p = a, *q = b;
    for (uint32_t i = 0; i < n; i++)
        if (p[i] != q[i])
            return 1;
    return 0;
}

static uint32_t slen(const char *s)
{
    uint32_t n = 0;
    while (s[n])
        n++;
    return n;
}

/* sin(a) for a = 0..90 degrees, x 1024 (rounded). */
static const uint16_t sin_q10[91] = {
    0,    18,   36,   54,   71,   89,   107,  125,  143,  160,  178,  195,  213,  230,  248,  265,  282,  299,
    316,  333,  350,  367,  384,  400,  416,  433,  449,  465,  481,  496,  512,  527,  543,  558,  573,  587,
    602,  616,  630,  644,  658,  672,  685,  698,  711,  724,  737,  749,  761,  773,  784,  796,  807,  818,
    828,  839,  849,  859,  868,  878,  887,  896,  904,  912,  920,  928,  935,  943,  949,  956,  962,  968,
    974,  979,  984,  989,  994,  998,  1002, 1005, 1008, 1011, 1014, 1016, 1018, 1020, 1022, 1023, 1023, 1024,
    1024,
};

static int isin(int deg)
{
    deg %= 360;
    if (deg < 0)
        deg += 360;
    if (deg <= 90)
        return sin_q10[deg];
    if (deg <= 180)
        return sin_q10[180 - deg];
    if (deg <= 270)
        return -(int)sin_q10[deg - 180];
    return -(int)sin_q10[360 - deg];
}

static int icos(int deg) { return isin(deg + 90); }

static int isqrt(uint32_t v)
{
    uint32_t r = 0, bit = 1u << 30;
    while (bit > v)
        bit >>= 2;
    while (bit) {
        if (v >= r + bit) {
            v -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    return (int)r;
}

/* ---- primitives (all clipped to the screen) ----------------------------------------------------------- */
static void hspan(uint32_t *fb, int x0, int x1, int y, uint32_t c)
{
    if (y < 0 || y >= GFX_H)
        return;
    if (x0 < 0)
        x0 = 0;
    if (x1 >= GFX_W)
        x1 = GFX_W - 1;
    if (x1 < x0)
        return;
    uint32_t *p = fb + (uint32_t)y * GFX_W + (uint32_t)x0;
    uint32_t n = (uint32_t)(x1 - x0 + 1);
#if defined(__i386__) || defined(__x86_64__)
    __asm__ volatile("rep stosl" : "+D"(p), "+c"(n) : "a"(c) : "memory");   /* the hot store loop */
#else
    for (uint32_t i = 0; i < n; i++)
        p[i] = c;
#endif
}

static void fill(uint32_t *fb, int x, int y, int w, int h, uint32_t c)
{
    for (int r = y; r < y + h; r++)
        hspan(fb, x, x + w - 1, r, c);
}

static void pixel(uint32_t *fb, int x, int y, uint32_t c)
{
    if (x >= 0 && x < GFX_W && y >= 0 && y < GFX_H)
        fb[(uint32_t)y * GFX_W + (uint32_t)x] = c;
}

static void disc(uint32_t *fb, int cx, int cy, int r, uint32_t c)
{
    for (int dy = -r; dy <= r; dy++) {
        int hw = isqrt((uint32_t)(r * r - dy * dy));
        hspan(fb, cx - hw, cx + hw, cy + dy, c);
    }
}

/* A ring between radius r_in and r_out (both inclusive edges), two spans per scanline. */
static void ring(uint32_t *fb, int cx, int cy, int r_out, int r_in, uint32_t c)
{
    for (int dy = -r_out; dy <= r_out; dy++) {
        int xo = isqrt((uint32_t)(r_out * r_out - dy * dy));
        int xi = dy > -r_in && dy < r_in ? isqrt((uint32_t)(r_in * r_in - dy * dy)) : -1;
        if (xi < 0) {
            hspan(fb, cx - xo, cx + xo, cy + dy, c);
        } else {
            hspan(fb, cx - xo, cx - xi - 1, cy + dy, c);
            hspan(fb, cx + xi + 1, cx + xo, cy + dy, c);
        }
    }
}

/* Filled polygon, even-odd scanline fill; n <= 24 points. */
static void poly(uint32_t *fb, const int *xs, const int *ys, int n, uint32_t c)
{
    int ymin = ys[0], ymax = ys[0];
    for (int i = 1; i < n; i++) {
        if (ys[i] < ymin)
            ymin = ys[i];
        if (ys[i] > ymax)
            ymax = ys[i];
    }
    for (int y = ymin; y < ymax; y++) {
        int xc[24], k = 0;
        for (int i = 0; i < n; i++) {
            int j = i + 1 == n ? 0 : i + 1;
            int y0 = ys[i], y1 = ys[j];
            if (y0 == y1)
                continue;
            if ((y >= y0 && y < y1) || (y >= y1 && y < y0)) {
                int x = xs[i] + (y - y0) * (xs[j] - xs[i]) / (y1 - y0);
                int p = k;
                while (p > 0 && xc[p - 1] > x) {         /* insertion keeps the crossings sorted */
                    xc[p] = xc[p - 1];
                    p--;
                }
                xc[p] = x;
                if (k < 24)
                    k++;
            }
        }
        for (int i = 0; i + 1 < k; i += 2)
            hspan(fb, xc[i], xc[i + 1], y, c);
    }
}

/* Text: glyphs scaled by `scale`; `bg` paints the cell (an inverse box), NO_BG leaves what is under it. */
static void glyph(uint32_t *fb, const struct gfx_font *f, int x, int y, uint8_t ch, uint32_t fg, uint32_t bg,
                  int scale)
{
    const uint8_t *rows = f->rows[ch];
    if (x < 0 || y < 0 || x + GLYPH_W * scale > GFX_W || y + GLYPH_H * scale > GFX_H) {
        for (int r = 0; r < GLYPH_H; r++)                    /* at an edge: the slow, clipped path */
            for (int cx = 0; cx < GLYPH_W; cx++) {
                int on = rows[r] >> (7 - cx) & 1;
                if (on || bg != NO_BG)
                    fill(fb, x + cx * scale, y + r * scale, scale, scale, on ? fg : bg);
            }
        return;
    }
    /* inside the screen: straight stores (the glyph loop is the hot path of every text region) */
    uint32_t *p = fb + (uint32_t)y * GFX_W + (uint32_t)x;
    for (int r = 0; r < GLYPH_H; r++) {
        uint8_t bits = rows[r];
        for (int s = 0; s < scale; s++) {
            uint32_t *q = p;
            for (int cx = 0; cx < GLYPH_W; cx++) {
                if (bits & (0x80u >> cx)) {
                    for (int k = 0; k < scale; k++)
                        q[k] = fg;
                } else if (bg != NO_BG) {
                    for (int k = 0; k < scale; k++)
                        q[k] = bg;
                }
                q += scale;
            }
            p += GFX_W;
        }
    }
}

static int text(uint32_t *fb, const struct gfx_font *f, int x, int y, const char *s, uint32_t fg, uint32_t bg,
                int scale)
{
    for (; *s; s++) {
        glyph(fb, f, x, y, (uint8_t)*s, fg, bg, scale);
        x += GLYPH_W * scale;
    }
    return x;
}

/* Text clipped to `width` pixels: longer text ends in "..." (the text screen's rule). */
static int text_clip(uint32_t *fb, const struct gfx_font *f, int x, int y, int width, const char *s, uint32_t fg,
                     uint32_t bg, int scale)
{
    int cells = width / (GLYPH_W * scale), n = (int)slen(s);
    if (n <= cells)
        return text(fb, f, x, y, s, fg, bg, scale);
    int keep = cells > 3 ? cells - 3 : 0;
    for (int i = 0; i < keep; i++)
        glyph(fb, f, x + i * GLYPH_W * scale, y, (uint8_t)s[i], fg, bg, scale);
    for (int i = 0; i < 3 && keep + i < cells; i++)
        glyph(fb, f, x + (keep + i) * GLYPH_W * scale, y, '.', fg, bg, scale);
    return x + cells * GLYPH_W * scale;
}

static int text_centred(uint32_t *fb, const struct gfx_font *f, int cx, int y, const char *s, uint32_t fg,
                        uint32_t bg, int scale)
{
    int w = (int)slen(s) * GLYPH_W * scale;
    return text(fb, f, cx - w / 2, y, s, fg, bg, scale);
}

/* ---- bounded formatting -------------------------------------------------------------------------------- */
struct buf {
    char *s;
    uint32_t cap, n;
};

static void b_str(struct buf *b, const char *s)
{
    while (*s && b->n + 1 < b->cap)
        b->s[b->n++] = *s++;
    b->s[b->n] = 0;
}

static void b_ch(struct buf *b, char ch)
{
    if (b->n + 1 < b->cap)
        b->s[b->n++] = ch;
    b->s[b->n] = 0;
}

static void b_num(struct buf *b, uint32_t v, int width)
{
    char t[11];
    int k = 0;
    do {
        t[k++] = (char)('0' + v % 10);
        v /= 10;
    } while (v);
    for (int i = k; i < width; i++)
        b_ch(b, '0');
    while (k)
        b_ch(b, t[--k]);
}

static void b_clock(struct buf *b, uint32_t tick, uint32_t hz)
{
    uint32_t sec = hz ? tick / hz : 0;
    b_num(b, sec / 3600 % 100, 2);
    b_ch(b, ':');
    b_num(b, sec / 60 % 60, 2);
    b_ch(b, ':');
    b_num(b, sec % 60, 2);
}

#define BUF(name, size) char name##_s[size]; struct buf name = { name##_s, size, 0 }; name##_s[0] = 0

static void b_call(struct buf *b, uint32_t nr, uint32_t target)
{
    if (nr == SCR_NO_CALL)
        return;
    if (nr == PEND_ESTOP_RESET) {
        b_str(b, "estop reset");
        return;
    }
    b_str(b, policy_syscall_name(nr));
    if (nr == SYS_CELL_FAN || nr == SYS_CELL_SERVO || nr == SYS_CELL_LCD) {
        b_ch(b, '(');
        b_num(b, target, 1);
        b_ch(b, ')');
    } else if (nr == SYS_OBJ_READ || nr == SYS_OBJ_WRITE) {
        const struct pol_object_rule *o = policy_object(target);
        b_ch(b, '(');
        if (o)
            b_str(b, o->name);
        else
            b_num(b, target, 1);
        b_ch(b, ')');
    }
}

static void b_detail(struct buf *b, const struct scr_decision *d)
{
    switch (d->code) {
    case DEC_ALLOW:
    case DEC_CONFIRM_YES:
        b_call(b, d->nr, d->target);
        return;
    case DEC_AUTO_HALT:
    case DEC_BOARD_BOOT:
        b_str(b, ui_reason_words(d->reason));
        return;
    default:
        b_call(b, d->nr, d->target);
        if (b->n)
            b_str(b, ": ");
        b_str(b, ui_reason_words(d->reason));
        return;
    }
}

/* The verdict's colours: ASK / DECLINED in the ask box, adverse verdicts and AUTO HALT in the alarm colours,
 * everything else as information. Returns the foreground, writes the background. */
static uint32_t verdict_colours(uint8_t code, uint32_t *bg)
{
    switch (code) {
    case DEC_CONFIRM_PENDING:
    case DEC_CONFIRM_NO:
        *bg = C_ASK_BG;
        return C_ASK_FG;
    case DEC_AUTO_HALT:
        *bg = C_ALARM_BG;
        return C_ALARM_FG;
    default:
        if (ui_verdict_adverse(code)) {
            *bg = C_ALARM_BG;
            return C_ALARM_FG;
        }
        *bg = C_BG;
        return C_CYAN;
    }
}

/* ---- the rotor -------------------------------------------------------------------------------------------- */
uint32_t gfx_fan_deg_per_tick(uint8_t duty)
{
    return duty == 0 ? 0 : duty < 30 ? 2 : duty < 50 ? 3 : duty < 60 ? 4 : duty < 70 ? 5 : duty < 80 ? 6 : 7;
}

static int stale(const struct screen_model *m) { return m->link == SCR_LINK_STALE; }

static int fan_frozen(const struct screen_model *m)
{
    return !m->have_tele || m->fan == 0 || m->estop_latched || stale(m) || m->link == SCR_LINK_LOST;
}

uint32_t gfx_fan_angle(const struct screen_model *m)
{
    if (fan_frozen(m))
        return 0;
    return m->now_tick * gfx_fan_deg_per_tick(m->fan) % GFX_FAN_PERIOD_DEG;
}

/* Point at polar (deg, r) around (cx, cy); the dial's convention: 0 degrees on the left, 90 up, 180 right. */
static void polar(int cx, int cy, int deg, int r, int *x, int *y)
{
    *x = cx - (r * icos(deg) + 512) / 1024;
    *y = cy - (r * isin(deg) + 512) / 1024;
}

/* A bar from radius r0 to r1 along `deg`, `hw` pixels each side of the centre line (a 4-point polygon). */
static void bar(uint32_t *fb, int cx, int cy, int deg, int r0, int r1, int hw, uint32_t c)
{
    int px = (hw * isin(deg) + 512) / 1024, py = (hw * icos(deg) + 512) / 1024;   /* perpendicular */
    int ax, ay, bx, by;
    polar(cx, cy, deg, r0, &ax, &ay);
    polar(cx, cy, deg, r1, &bx, &by);
    int xs[4] = { ax + px, bx + px, bx - px, ax - px }, ys[4] = { ay + py, by + py, by - py, ay - py };
    poly(fb, xs, ys, 4, c);
}

/* One swept blade with its root at `deg` (the rotor turns clockwise on the screen as the angle grows). */
static void blade(uint32_t *fb, int deg, uint32_t c)
{
    /* seven stations from the hub to just inside the housing; the blade sweeps 28 degrees and widens outward */
    const int r0 = FAN_HUB_R + 2, r1 = FAN_R - 4, span = r1 - r0;
    int xs[16], ys[16], n = 0;
    for (int i = 0; i < 7; i++) {
        int r = r0 + span * i / 6, sweep = (r - r0) * 28 / span, hw = 10 + (r - r0) * 18 / span;
        polar(FAN_CX, FAN_CY, deg + sweep - hw, r, &xs[n], &ys[n]);
        n++;
    }
    polar(FAN_CX, FAN_CY, deg + 28, FAN_R, &xs[n], &ys[n]);
    n++;
    for (int i = 6; i >= 0; i--) {
        int r = r0 + span * i / 6, sweep = (r - r0) * 28 / span, hw = 10 + (r - r0) * 18 / span;
        polar(FAN_CX, FAN_CY, deg + sweep + hw, r, &xs[n], &ys[n]);
        n++;
    }
    poly(fb, xs, ys, n, c);
}

static void fan_picture(const struct screen_model *m, uint32_t *fb)
{
    int frozen = fan_frozen(m);
    uint32_t blades = frozen ? C_DIM : C_CYAN, hub = frozen ? C_GREY : C_WHITE;
    int rot = (int)gfx_fan_angle(m);
    fill(fb, R_FAN, C_BG);
    ring(fb, FAN_CX, FAN_CY, FAN_R, FAN_R - 3, 0x202020u);   /* the housing */
    for (int b = 0; b < 3; b++)
        blade(fb, rot + b * 120, blades);
    disc(fb, FAN_CX, FAN_CY, FAN_HUB_R, hub);
    disc(fb, FAN_CX, FAN_CY, 4, C_BG);
}

static void fan_text(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    int frozen = fan_frozen(m);
    fill(fb, R_FAN_TEXT, C_BG);
    BUF(fv, 20);
    b_str(&fv, "FAN ");
    if (m->have_tele) {
        b_num(&fv, m->fan, 3);
        b_ch(&fv, '%');
    } else {
        b_str(&fv, "---");
    }
    text_centred(fb, f, FAN_CX, FAN_LABEL_Y, fv.s, C_TEXT, NO_BG, 2);
    const char *w = !m->have_tele                 ? "NO DATA"
                    : m->estop_latched            ? "HALTED (E-STOP)"
                    : stale(m)                    ? "STALE"
                    : m->link == SCR_LINK_LOST    ? "HALTED (LINK)"
                    : m->fan == 0                 ? "STOPPED"
                                                  : "TURNING";
    int plain = !m->have_tele || (m->fan == 0 && !m->estop_latched && !stale(m) && m->link != SCR_LINK_LOST);
    uint32_t fg = plain ? C_TEXT : frozen ? C_ALARM_FG : C_CYAN, bg = plain ? NO_BG : frozen ? C_ALARM_BG : NO_BG;
    text_centred(fb, f, FAN_CX, FAN_WORD_Y, w, fg, bg, 2);
}

/* ---- the dial ------------------------------------------------------------------------------------------- */
/* The dial's face, drawn once with the chrome: the arc (dark red outside the servo range, brown inside),
 * the marks at 30 / 90 / 150, the numerals and the base line. */
static void gauge_face(const struct gfx_font *f, uint32_t *fb)
{
    for (int dy = 0; dy <= GAUGE_R; dy++) {
        int ri = GAUGE_R - GAUGE_BAND, y = GAUGE_CY - dy;
        int xo = isqrt((uint32_t)(GAUGE_R * GAUGE_R - dy * dy));
        int xi = dy > ri ? -1 : isqrt((uint32_t)(ri * ri - dy * dy));   /* -1: the full chord is band */
        for (int x = GAUGE_CX - xo; x <= GAUGE_CX + xo; x++) {
            int dx = x - GAUGE_CX, adx = dx < 0 ? -dx : dx;
            if (adx <= xi)
                continue;                                    /* inside the arc */
            /* below the 30 / 150 degree rays: up < |dx| tan 30 (tan 30 = 0.577) */
            pixel(fb, x, y, dy * 1000 < adx * 577 ? C_DRED : C_BROWN);
        }
    }
    bar(fb, GAUGE_CX, GAUGE_CY, SERVO_MIN_DEG, GAUGE_MARK_R0, GAUGE_R, 2, C_LRED);
    bar(fb, GAUGE_CX, GAUGE_CY, SERVO_MAX_DEG, GAUGE_MARK_R0, GAUGE_R, 2, C_LRED);
    bar(fb, GAUGE_CX, GAUGE_CY, 90, GAUGE_MARK_R0, GAUGE_R, 2, C_AMBER);
    static const struct {
        int deg;
        const char *label;
    } marks[3] = { { SERVO_MIN_DEG, "30" }, { 90, "90" }, { SERVO_MAX_DEG, "150" } };
    for (int i = 0; i < 3; i++) {
        int x, y;
        polar(GAUGE_CX, GAUGE_CY, marks[i].deg, GAUGE_R + 14, &x, &y);
        text_centred(fb, f, x, y - 8, marks[i].label, i == 1 ? C_AMBER : C_LRED, NO_BG, 1);
    }
    hspan(fb, GAUGE_CX - GAUGE_R - 10, GAUGE_CX + GAUGE_R + 10, GAUGE_CY + 2, C_DIM);   /* the base line */
}

/* The needle to the reported angle (grey when stale, none without telemetry) and the pivot; the disc it
 * sweeps is cleared first (it stays inside the arc, so the face is never touched). */
static void gauge_needle(const struct screen_model *m, uint32_t *fb)
{
    /* the upper half of the disc the needle sweeps, and the rows of the pivot below the centre line */
    for (int dy = -NEEDLE_CLEAR_R; dy <= 8; dy++) {
        int hw = dy <= 0 ? isqrt((uint32_t)(NEEDLE_CLEAR_R * NEEDLE_CLEAR_R - dy * dy)) : NEEDLE_CLEAR_R;
        hspan(fb, GAUGE_CX - hw, GAUGE_CX + hw, GAUGE_CY + dy, C_BG);
    }
    hspan(fb, GAUGE_CX - NEEDLE_CLEAR_R, GAUGE_CX + NEEDLE_CLEAR_R, GAUGE_CY + 2, C_DIM);   /* base line */
    if (m->have_tele) {
        int deg = m->servo > 180 ? 180 : m->servo;
        uint32_t c = stale(m) ? C_DIM : C_CYAN;
        int tx, ty;
        polar(GAUGE_CX, GAUGE_CY, deg, GAUGE_NEEDLE_R, &tx, &ty);
        int px = (4 * isin(deg) + 512) / 1024, py = (4 * icos(deg) + 512) / 1024;
        int xs[3] = { GAUGE_CX + px, tx, GAUGE_CX - px }, ys[3] = { GAUGE_CY + py, ty, GAUGE_CY - py };
        poly(fb, xs, ys, 3, c);
    }
    disc(fb, GAUGE_CX, GAUGE_CY, 7, C_WHITE);
}

static void gauge_text(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    fill(fb, R_GAUGE_TEXT, C_BG);
    BUF(sv, 32);
    b_str(&sv, "SERVO ");
    if (m->have_tele) {
        b_num(&sv, m->servo, 3);
        b_ch(&sv, (char)0xF8);                               /* CP437 degree sign, from the same font */
    } else {
        b_str(&sv, "---");
    }
    b_str(&sv, " (");
    b_num(&sv, SERVO_MIN_DEG, 1);
    b_ch(&sv, '-');
    b_num(&sv, SERVO_MAX_DEG, 1);
    b_ch(&sv, ')');
    text_centred(fb, f, GAUGE_CX, GAUGE_LABEL_Y, sv.s, C_TEXT, NO_BG, 2);
}

/* ---- the sensor ------------------------------------------------------------------------------------------ */
static void sensor_text(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    int t = m->have_tele, echo = t && m->dist_mm != DIST_NO_ECHO, near = echo && m->dist_mm < PRESENCE_MM;
    fill(fb, R_SENSOR_TEXT, C_BG);
    BUF(dv, 20);
    b_str(&dv, "DIST ");
    if (!t) {
        b_str(&dv, "---");
    } else if (!echo) {
        b_str(&dv, "NO ECHO");
    } else {
        b_num(&dv, m->dist_mm, 1);
        b_str(&dv, " mm");
    }
    text(fb, f, SENSOR_X, SENSOR_TEXT_Y, dv.s, C_TEXT, NO_BG, 2);
    const char *w = !t ? "NO DATA" : stale(m) ? "STALE" : !echo ? "NO ECHO" : near ? "PRESENCE" : "CLEAR";
    uint32_t fg = !t ? C_TEXT : stale(m) ? C_ALARM_FG : !echo || near ? C_ASK_FG : C_CYAN;
    uint32_t bg = !t ? NO_BG : stale(m) ? C_ALARM_BG : !echo || near ? C_ASK_BG : NO_BG;
    text(fb, f, SENSOR_X, SENSOR_WORD_Y, w, fg, bg, 2);
}

/* The beam: from the sensor head to the reading, fading; the object at the reading; dots beyond. */
static void sensor_beam(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    int t = m->have_tele, echo = t && m->dist_mm != DIST_NO_ECHO, near = echo && m->dist_mm < PRESENCE_MM;
    fill(fb, R_BEAM, C_BG);
    uint32_t beam = stale(m) ? C_DIM : near ? C_ASK_BG : C_CYAN;
    int n = 0;
    if (echo)
        n = m->dist_mm >= SCR_DIST_SPAN_MM ? BEAM_W : (int)(m->dist_mm * BEAM_W / SCR_DIST_SPAN_MM);
    fill(fb, SENSOR_X, BEAM_Y - 2, 12, BEAM_H + 4, t ? C_WHITE : C_DIM);           /* the sensor head */
    uint32_t shade[BEAM_W];
    for (int i = 0; i < n; i++) {
        uint32_t a = 255u - (uint32_t)i * 170u / (uint32_t)(n ? n : 1);           /* 255 -> 85 */
        shade[i] = ((beam >> 16 & 0xFF) * a >> 8) << 16 | ((beam >> 8 & 0xFF) * a >> 8) << 8 |
                   ((beam & 0xFF) * a >> 8);
    }
    for (int r = 0; r < BEAM_H; r++) {
        uint32_t *row = fb + (uint32_t)(BEAM_Y + r) * GFX_W + BEAM_X;
        for (int i = 0; i < n; i++)
            row[i] = shade[i];
    }
    if (echo && n < BEAM_W)
        fill(fb, BEAM_X + n, BEAM_Y - 2, 6, BEAM_H + 4, stale(m) ? C_DIM : C_WHITE);  /* the object */
    for (int i = echo ? n + 8 : 0; i < BEAM_W; i += 8)
        fill(fb, BEAM_X + i, BEAM_Y + BEAM_H / 2 - 1, 2, 2, C_DIM);
    int mark = BEAM_X + (int)(PRESENCE_MM * BEAM_W / SCR_DIST_SPAN_MM);
    fill(fb, mark - 1, BEAM_Y + BEAM_H + 2, 2, 8, C_LRED);
    BUF(th, 16);
    b_num(&th, PRESENCE_MM, 1);
    b_str(&th, " mm");
    text(fb, f, mark + 4, BEAM_Y + BEAM_H + 6, th.s, C_LRED, NO_BG, 1);
    BUF(sp, 16);
    b_num(&sp, SCR_DIST_SPAN_MM, 1);
    b_str(&sp, " mm");
    text(fb, f, BEAM_X + BEAM_W - (int)slen(sp.s) * GLYPH_W, BEAM_Y + BEAM_H + 6, sp.s, C_DIM, NO_BG, 1);
}

/* ---- the E-stop lamp, link and age ------------------------------------------------------------------------- */
static void lamp(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    int t = m->have_tele, lit = m->estop_pressed || m->estop_latched;
    fill(fb, R_LAMP, C_BG);
    disc(fb, LAMP_CX, LAMP_CY, LAMP_R + 2, C_GREY);
    disc(fb, LAMP_CX, LAMP_CY, LAMP_R, lit ? C_LRED : 0x303030u);
    if (lit)
        disc(fb, LAMP_CX - 5, LAMP_CY - 6, 4, C_WHITE);       /* a highlight: the lamp is on */
    const char *es = m->estop_pressed && m->estop_latched ? "E-STOP PRESSED, LATCHED"
                     : m->estop_pressed                   ? "E-STOP PRESSED"
                     : m->estop_latched                   ? "E-STOP LATCHED"
                     : t                                  ? "E-STOP RELEASED"
                                                          : "E-STOP -";
    text(fb, f, LAMP_TEXT_X, LAMP_CY - 20, es, lit ? C_ALARM_FG : C_TEXT, lit ? C_ALARM_BG : NO_BG, 1);
    static const char *const link_words[] = { "LINK no cell (COM3 absent)", "LINK waiting for the board",
                                              "LINK ok", "LINK STALE", "LINK LOST (board watchdog)" };
    uint8_t lk = m->link <= SCR_LINK_LOST ? m->link : SCR_LINK_NO_CELL;
    uint32_t lfg = lk == SCR_LINK_OK ? C_CYAN : lk >= SCR_LINK_STALE ? C_ALARM_FG : C_TEXT;
    text(fb, f, LAMP_TEXT_X, LAMP_CY - 2, link_words[lk], lfg, lk >= SCR_LINK_STALE ? C_ALARM_BG : NO_BG, 1);
}

static void age(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    int t = m->have_tele;
    fill(fb, R_AGE, C_BG);
    BUF(ag, 40);
    b_str(&ag, "TELEMETRY AGE ");
    if (t) {
        b_num(&ag, m->tele_age_ms, 1);
        b_str(&ag, " ms");
    } else {
        b_str(&ag, "-");
    }
    text(fb, f, LAMP_TEXT_X, AGE_Y, ag.s, C_CYAN, NO_BG, 1);
}

/* ---- status bar, banner, console, ticker ----------------------------------------------------------------- */
static void clock_text(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    fill(fb, R_CLOCK, C_BG);
    BUF(b, 32);
    b_str(&b, "UP ");
    b_clock(&b, m->now_tick, m->hz);
    text(fb, f, CLOCK_X, TITLE_Y, b.s, C_CYAN, NO_BG, 2);
}

static void status_exec(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    fill(fb, R_STATUS_L, C_BG);
    BUF(e, 40);
    uint32_t fg = C_TEXT, bg = NO_BG;
    if (m->exec_state == SCR_EXEC_RUNNING) {
        b_str(&e, "EXEC RUNNING REQ ");
        b_num(&e, m->req, 1);
    } else if (m->exec_state == SCR_EXEC_KILLED) {
        b_str(&e, "EXEC KILLED REQ ");
        b_num(&e, m->req, 1);
        b_str(&e, m->kill_kind == SCR_KILL_FAULT ? " FAULT" : " LIMIT");
        fg = C_ALARM_FG;
        bg = C_ALARM_BG;
    } else {
        b_str(&e, "EXEC IDLE");
    }
    text(fb, f, 16, STATUS_Y, e.s, fg, bg, 2);
}

static void status_figures(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb)
{
    fill(fb, R_STATUS_R, C_BG);
    BUF(fr, 24);
    b_str(&fr, "FREE ");
    b_num(&fr, m->free_kib, 1);
    b_str(&fr, " KiB");
    text(fb, f, 400, STATUS_Y, fr.s, C_CYAN, NO_BG, 2);
    BUF(l, 24);
    b_str(&l, "LOGGED ");
    b_num(&l, m->logged, 1);
    text(fb, f, 640, STATUS_Y, l.s, C_CYAN, NO_BG, 2);
    text(fb, f, 824, STATUS_Y, m->tainted ? "TAINTED: YES" : "TAINTED: NO", m->tainted ? C_ASK_FG : C_CYAN,
         m->tainted ? C_ASK_BG : NO_BG, 2);
}

/* The wipe phase: how many of the banner's three strips carry the verdict colours (0 ms: 1, 70: 2, 140: 3). */
static int wipe_phase(const struct screen_model *m)
{
    if (!m->ndec)
        return 0;
    const struct scr_decision *d = &m->dec[m->ndec - 1];
    uint32_t ms = m->hz ? (m->now_tick - d->tick) * 1000u / m->hz : SCR_WIPE_ROW5_MS;
    return ms >= SCR_WIPE_ROW5_MS ? 3 : ms >= SCR_WIPE_ROW4_MS ? 2 : 1;
}

/* Strip `k` (0..2) of the banner: the label line, the centred verdict word, the detail line. */
static void banner_strip(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb, int k)
{
    const struct scr_decision *d = m->ndec ? &m->dec[m->ndec - 1] : 0;
    const char *word = d ? ui_verdict_word(d->code) : 0;
    uint32_t bg = C_BG, fg = word ? verdict_colours(d->code, &bg) : C_TEXT;
    int coloured = word && wipe_phase(m) > k;
    int y = BANNER_Y + k * 32;
    fill(fb, INNER_X, y, INNER_W, 32, coloured ? bg : C_BG);
    if (!coloured)
        fg = C_TEXT;
    if (k == 0) {
        BUF(r1, 64);
        b_str(&r1, "LAST KERNEL DECISION: ");
        b_str(&r1, word ? word : "-");
        text(fb, f, 16, y, r1.s, fg, NO_BG, 2);
    } else if (!word) {
        return;
    } else if (k == 1) {
        text_centred(fb, f, GFX_W / 2, y, word, fg, NO_BG, 2);
    } else {
        BUF(r3, 96);
        b_detail(&r3, d);
        if (d->code == DEC_CONFIRM_PENDING && m->pending)
            b_str(&r3, "; waiting for y/N");
        text_clip(fb, f, 16, y, INNER_W - 24, r3.s, fg, NO_BG, 2);
    }
}

static int starts(const char *s, const char *p)
{
    while (*p)
        if (*s++ != *p++)
            return 0;
    return 1;
}

static void console_row(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb, int i)
{
    const char *s = m->con[i];
    int y = CONSOLE_Y + i * GLYPH_H;
    fill(fb, 16, y, SCR_CON_COLS * GLYPH_W, GLYPH_H, C_BG);
    if (starts(s, "CONFIRM:")) {
        fill(fb, 16, y, SCR_CON_COLS * GLYPH_W, GLYPH_H, C_ASK_BG);
        text(fb, f, 16, y, s, C_ASK_FG, C_ASK_BG, 1);
    } else if (starts(s, "ring3> ") || (starts(s, "ring3>") && s[6] == 0)) {
        text(fb, f, 16, y, "ring3>", C_CYAN, NO_BG, 1);
        text(fb, f, 16 + 6 * GLYPH_W, y, s + 6, C_TEXT, NO_BG, 1);
    } else {
        text(fb, f, 16, y, s, C_TEXT, NO_BG, 1);
    }
}

#define TICKER_W 648            /* 16 px in, the time and request, the word box, 47 characters of detail */

static void ticker_row(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb, int row)
{
    uint32_t n = m->ndec > SCR_DECISIONS ? SCR_DECISIONS : m->ndec;
    int y = TICKER_Y + row * TICKER_ROW_H;
    fill(fb, INNER_X, y - 1, TICKER_W, TICKER_ROW_H, C_BG);
    if ((uint32_t)row < SCR_DECISIONS - n)
        return;                                              /* an empty row above the first decision */
    const struct scr_decision *d = &m->dec[row - (int)(SCR_DECISIONS - n)];
    BUF(tr, 24);
    b_clock(&tr, d->tick, m->hz);
    b_str(&tr, "  REQ ");
    if (d->req)
        b_num(&tr, d->req, 1);
    else
        b_ch(&tr, '-');
    text(fb, f, 16, y, tr.s, C_CYAN, NO_BG, 1);
    const char *w = ui_verdict_word(d->code);
    uint32_t bg, fg = verdict_colours(d->code, &bg);
    fill(fb, 160, y - 1, 12 * GLYPH_W, GLYPH_H + 2, bg);
    text(fb, f, 160, y, w ? w : "-", fg, bg == C_BG ? NO_BG : bg, 1);
    BUF(dt, 96);
    b_detail(&dt, d);
    text_clip(fb, f, 272, y, INNER_X + TICKER_W - 272, dt.s, C_TEXT, NO_BG, 1);
}

/* The parts that never change: the frame, the rules, the labels, the separators, the dial's face. */
static void chrome(const struct gfx_font *f, uint32_t *fb)
{
    fill(fb, 0, 0, GFX_W, GFX_H, C_BG);
    fill(fb, 2, 2, GFX_W - 4, 2, C_AMBER);
    fill(fb, 2, GFX_H - 4, GFX_W - 4, 2, C_AMBER);
    fill(fb, 2, 2, 2, GFX_H - 4, C_AMBER);
    fill(fb, GFX_W - 4, 2, 2, GFX_H - 4, C_AMBER);
    static const int rules[4] = { RULE1_Y, RULE2_Y, RULE3_Y, RULE4_Y };
    for (int i = 0; i < 4; i++)
        fill(fb, INNER_X, rules[i], INNER_W, 2, C_AMBER);
    text(fb, f, 16, TITLE_Y, "RING3 / KERNEL EXECUTIVE", C_AMBER, NO_BG, 2);
    text(fb, f, 16, RULE2_Y + 4, "CONSOLE", C_AMBER, NO_BG, 1);
    text(fb, f, 16, RULE3_Y + 4, "LIVE CELL", C_AMBER, NO_BG, 1);
    text(fb, f, 16, RULE4_Y + 4, "LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST", C_AMBER, NO_BG, 1);
    fill(fb, SEP1_X, PANEL_Y + 8, 2, RULE4_Y - PANEL_Y - 16, C_AMBER);
    fill(fb, SEP2_X, PANEL_Y + 8, 2, RULE4_Y - PANEL_Y - 16, C_AMBER);
    gauge_face(f, fb);
}

/* ---- the render: regions, pending bits, the per-tick budget ---------------------------------------------
 * Each region is redrawn when its inputs changed. The driver keeps the `pending` bits between ticks: a region
 * whose redraw does not fit the tick's pixel budget stays pending and is drawn on a later tick, highest
 * priority first (the decision banner before the rotor), so one tick never does more than the budget of
 * work while every change still reaches the screen a tick or two later. */
enum region {
    RG_STATUS_L, RG_STATUS_R, RG_BANNER0, RG_BANNER1, RG_BANNER2,
    RG_CON0, RG_CON1, RG_CON2, RG_CON3, RG_CON4,
    RG_TICK0, RG_TICK1, RG_TICK2, RG_TICK3,
    RG_FAN_TEXT, RG_GAUGE_TEXT, RG_GAUGE, RG_SENSOR_TEXT, RG_BEAM, RG_LAMP, RG_AGE, RG_CLOCK, RG_FAN,
    RG_COUNT
};

#define CHANGED(field) (!prev || differ(&m->field, &prev->field, sizeof m->field))

static uint32_t changed_regions(const struct screen_model *m, const struct screen_model *prev)
{
    uint32_t bits = 0;
    uint32_t sec = m->hz ? m->now_tick / m->hz : 0, psec = prev && prev->hz ? prev->now_tick / prev->hz : 0;
    if (!prev || sec != psec)
        bits |= 1u << RG_CLOCK;
    if (CHANGED(exec_state) || CHANGED(kill_kind) || CHANGED(req))
        bits |= 1u << RG_STATUS_L;
    if (CHANGED(free_kib) || CHANGED(logged) || CHANGED(tainted))
        bits |= 1u << RG_STATUS_R;
    int dec_changed = CHANGED(ndec) || CHANGED(dec);
    int phase = wipe_phase(m), pphase = prev ? wipe_phase(prev) : -1;
    for (int k = 0; k < 3; k++)
        if (dec_changed || (k == 2 && CHANGED(pending)) || (phase > k) != (pphase > k))
            bits |= 1u << (RG_BANNER0 + k);
    for (int i = 0; i < SCR_CON_ROWS; i++)
        if (CHANGED(con[i]))
            bits |= 1u << (RG_CON0 + i);
    if (dec_changed)
        bits |= 0xFu << RG_TICK0;
    int cell_changed = CHANGED(have_tele) || CHANGED(link) || CHANGED(estop_latched) || CHANGED(fan);
    if (cell_changed || gfx_fan_angle(m) != gfx_fan_angle(prev))
        bits |= 1u << RG_FAN;
    if (cell_changed)
        bits |= 1u << RG_FAN_TEXT;
    if (CHANGED(have_tele) || CHANGED(link) || CHANGED(servo))
        bits |= 1u << RG_GAUGE | 1u << RG_GAUGE_TEXT;
    if (CHANGED(have_tele) || CHANGED(link) || CHANGED(dist_mm))
        bits |= 1u << RG_SENSOR_TEXT | 1u << RG_BEAM;
    if (CHANGED(have_tele) || CHANGED(link) || CHANGED(estop_pressed) || CHANGED(estop_latched))
        bits |= 1u << RG_LAMP;
    if (CHANGED(have_tele) || CHANGED(tele_age_ms))
        bits |= 1u << RG_AGE;
    return bits;
}

/* Draws one region; returns its rectangle. */
static struct gfx_rect draw_region(const struct screen_model *m, const struct gfx_font *f, uint32_t *fb,
                                   enum region r)
{
    struct gfx_rect rc = { 0, 0, 0, 0 };
#define RECT(...) RECT_(__VA_ARGS__)                         /* the R_* macros expand to four values */
#define RECT_(X, Y, W, H) (rc.x = (uint16_t)(X), rc.y = (uint16_t)(Y), rc.w = (uint16_t)(W), rc.h = (uint16_t)(H))
    switch (r) {
    case RG_STATUS_L:
        status_exec(m, f, fb);
        RECT(R_STATUS_L);
        break;
    case RG_STATUS_R:
        status_figures(m, f, fb);
        RECT(R_STATUS_R);
        break;
    case RG_BANNER0:
    case RG_BANNER1:
    case RG_BANNER2:
        banner_strip(m, f, fb, r - RG_BANNER0);
        RECT(INNER_X, BANNER_Y + (r - RG_BANNER0) * 32, INNER_W, 32);
        break;
    case RG_CON0:
    case RG_CON1:
    case RG_CON2:
    case RG_CON3:
    case RG_CON4:
        console_row(m, f, fb, r - RG_CON0);
        RECT(16, CONSOLE_Y + (r - RG_CON0) * GLYPH_H, SCR_CON_COLS * GLYPH_W, GLYPH_H);
        break;
    case RG_TICK0:
    case RG_TICK1:
    case RG_TICK2:
    case RG_TICK3:
        ticker_row(m, f, fb, r - RG_TICK0);
        RECT(INNER_X, TICKER_Y + (r - RG_TICK0) * TICKER_ROW_H - 1, TICKER_W, TICKER_ROW_H);
        break;
    case RG_FAN_TEXT:
        fan_text(m, f, fb);
        RECT(R_FAN_TEXT);
        break;
    case RG_GAUGE_TEXT:
        gauge_text(m, f, fb);
        RECT(R_GAUGE_TEXT);
        break;
    case RG_GAUGE:
        gauge_needle(m, fb);
        RECT(R_NEEDLE);
        break;
    case RG_SENSOR_TEXT:
        sensor_text(m, f, fb);
        RECT(R_SENSOR_TEXT);
        break;
    case RG_BEAM:
        sensor_beam(m, f, fb);
        RECT(R_BEAM);
        break;
    case RG_LAMP:
        lamp(m, f, fb);
        RECT(R_LAMP);
        break;
    case RG_AGE:
        age(m, f, fb);
        RECT(R_AGE);
        break;
    case RG_CLOCK:
        clock_text(m, f, fb);
        RECT(R_CLOCK);
        break;
    case RG_FAN:
        fan_picture(m, fb);
        RECT(R_FAN);
        break;
    default:
        break;
    }
#undef RECT
#undef RECT_
    return rc;
}

/* The cost of a region in pixel units: its area drawn plus its area copied; the rotor adds its blades. */
static uint32_t region_cost(enum region r)
{
    switch (r) {
    case RG_STATUS_L: return 2u * 396 * 32;
    case RG_STATUS_R: return 2u * (GFX_W - INNER_X - 400) * 32;
    case RG_BANNER0: case RG_BANNER1: case RG_BANNER2: return 2u * INNER_W * 32;
    case RG_CON0: case RG_CON1: case RG_CON2: case RG_CON3: case RG_CON4: return 2u * SCR_CON_COLS * GLYPH_W * GLYPH_H;
    case RG_TICK0: case RG_TICK1: case RG_TICK2: case RG_TICK3: return 2u * TICKER_W * TICKER_ROW_H;
    case RG_FAN_TEXT: return 2u * (SEP1_X - INNER_X) * 72;
    case RG_GAUGE_TEXT: return 2u * (SEP2_X - SEP1_X - 4) * 32;
    case RG_GAUGE: return 2u * (2 * NEEDLE_CLEAR_R + 4) * (NEEDLE_CLEAR_R + 12);
    case RG_SENSOR_TEXT: return 2u * (GFX_W - INNER_X - SEP2_X - 2) * (BEAM_Y - 4 - SENSOR_TEXT_Y);
    case RG_BEAM: return 2u * (GFX_W - INNER_X - SEP2_X - 2) * 44;
    case RG_LAMP: return 2u * (GFX_W - INNER_X - SEP2_X - 2) * (2 * LAMP_R + 6);
    case RG_AGE: return 2u * (GFX_W - INNER_X - LAMP_TEXT_X) * GLYPH_H;
    case RG_CLOCK: return 2u * 176 * 32;
    case RG_FAN: return 3u * 4 * FAN_R * FAN_R;
    default: return 0;
    }
}

uint32_t gfx_render(const struct screen_model *m, const struct screen_model *prev, const struct gfx_font *f,
                    uint32_t *fb, struct gfx_rect *out, uint32_t *pending, uint32_t budget)
{
    if (!prev) {
        chrome(f, fb);
        for (int r = 0; r < RG_COUNT; r++)
            draw_region(m, f, fb, (enum region)r);
        *pending = 0;
        out[0].x = 0;
        out[0].y = 0;
        out[0].w = GFX_W;
        out[0].h = GFX_H;
        return 1;
    }
    uint32_t carried = *pending;                             /* skipped by an earlier call: these go first */
    *pending |= changed_regions(m, prev);
    uint32_t n = 0, spent = 0;
    for (int pass = 0; pass < 2; pass++)
        for (int r = 0; r < RG_COUNT && n < GFX_MAX_RECTS; r++) {
            uint32_t bit = 1u << r;
            if (!(*pending & bit) || (pass == 0) != ((carried & bit) != 0))
                continue;
            uint32_t cost = region_cost((enum region)r);
            if (budget && spent && spent + cost > budget)
                continue;                                    /* next tick; at least one region per tick */
            out[n++] = draw_region(m, f, fb, (enum region)r);
            *pending &= ~bit;
            spent += cost;
        }
    return n;
}
