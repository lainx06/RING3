/* The status screen renderer: see screen.h. Layout and colours follow docs/design/astra-ui-spec-raw.md with
 * the corrections in docs/tasks/T-08.md (one-based rows and columns in the comments, as in the spec). */
#include "screen.h"
#include "policy.h"
#include "words.h"

/* CP437 */
#define CH_TL 0xC9
#define CH_TR 0xBB
#define CH_BL 0xC8
#define CH_BR 0xBC
#define CH_H 0xCD
#define CH_V 0xBA
#define CH_LT 0xCC
#define CH_RT 0xB9
#define CH_BLOCK 0xDB
#define CH_UPPER 0xDF           /* upper half block */
#define CH_LOWER 0xDC           /* lower half block */
#define CH_SHADE3 0xB2          /* dark shade; 0xB1 medium, 0xB0 light */
#define CH_DOT 0xFA             /* middle dot */
#define CH_V1 0xB3              /* single vertical */
#define CH_H_DOWN1 0xD1         /* double horizontal, single down */
#define CH_UP_T 0xC1            /* single up, single horizontal */
#define CH_DEG 0xF8

#define A_FRAME SCR_ATTR(SCR_YELLOW, SCR_BLACK)
#define A_TEXT SCR_ATTR(SCR_WHITE, SCR_BLACK)
#define A_INFO SCR_ATTR(SCR_LIGHT_CYAN, SCR_BLACK)
#define A_ASK SCR_ATTR(SCR_BLACK, SCR_YELLOW)
#define A_ALARM SCR_ATTR(SCR_WHITE, SCR_DARK_RED)
#define A_MARK SCR_ATTR(SCR_LIGHT_RED, SCR_BLACK)


typedef uint16_t cells_t[SCR_COLS];

static void put(cells_t *c, int row, int col, uint8_t ch, uint8_t attr)
{
    if (row >= 1 && row <= SCR_ROWS && col >= 1 && col <= SCR_COLS)
        c[row - 1][col - 1] = (uint16_t)(ch | (uint16_t)attr << 8);
}

static uint32_t slen(const char *s)
{
    uint32_t n = 0;
    while (s[n])
        n++;
    return n;
}

/* Writes `s` from (row, col) through column `last`; text longer than the field ends in "...". Returns the
 * column after the last character written. */
static int text(cells_t *c, int row, int col, int last, const char *s, uint8_t attr)
{
    int width = last - col + 1;
    int n = (int)slen(s);
    int clip = n > width;
    int keep = clip ? width - 3 : n;
    for (int i = 0; i < keep; i++)
        put(c, row, col + i, (uint8_t)s[i], attr);
    if (clip)
        for (int i = 0; i < 3; i++)
            put(c, row, col + keep + i, '.', attr);
    return col + (clip ? width : n);
}

static void paint(cells_t *c, int row, int col, int last, uint8_t attr)
{
    for (int k = col; k <= last; k++)
        c[row - 1][k - 1] = (uint16_t)((c[row - 1][k - 1] & 0xFF) | (uint16_t)attr << 8);
}

/* ---- small formatting into a bounded buffer -------------------------------------------------------------- */
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

/* The call as the kernel names it (policy_syscall_name, object names from the policy's table). */
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

/* The detail after a verdict word: the call for ALLOW / CONFIRMED, call and reason for ASK / DENIED /
 * DECLINED, the reason for AUTO HALT and BOARD BOOT. */
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

static uint8_t verdict_attr(uint8_t code)
{
    switch (code) {
    case DEC_CONFIRM_PENDING:
    case DEC_CONFIRM_NO:
        return A_ASK;
    case DEC_AUTO_HALT:
        return A_ALARM;
    default:
        return ui_verdict_adverse(code) ? A_ALARM : A_INFO;
    }
}

/* ---- regions --------------------------------------------------------------------------------------------- */
static void rule(cells_t *c, int row, uint8_t left, uint8_t right, const char *label)
{
    put(c, row, 1, left, A_FRAME);
    for (int k = 2; k < SCR_COLS; k++)
        put(c, row, k, CH_H, A_FRAME);
    put(c, row, SCR_COLS, right, A_FRAME);
    if (label) {
        put(c, row, 3, ' ', A_FRAME);
        int end = text(c, row, 4, SCR_COLS - 2, label, A_FRAME);
        put(c, row, end, ' ', A_FRAME);
    }
}

static void header(const struct screen_model *m, cells_t *c)
{
    BUF(b, 32);
    b_str(&b, "UP ");
    b_clock(&b, m->now_tick, m->hz);
    text(c, 2, 3, 13, b.s, A_INFO);

    BUF(e, 40);
    uint8_t ea = A_TEXT;
    if (m->exec_state == SCR_EXEC_RUNNING) {
        b_str(&e, "EXEC RUNNING REQ ");
        b_num(&e, m->req, 1);
    } else if (m->exec_state == SCR_EXEC_KILLED) {
        b_str(&e, "EXEC KILLED REQ ");
        b_num(&e, m->req, 1);
        b_str(&e, m->kill_kind == SCR_KILL_FAULT ? " FAULT" : " LIMIT");
        ea = A_ALARM;
    } else {
        b_str(&e, "EXEC IDLE");
    }
    text(c, 2, 17, 41, e.s, ea);

    BUF(f, 24);
    b_str(&f, "FREE ");
    b_num(&f, m->free_kib, 1);
    b_str(&f, " KiB");
    text(c, 2, 43, 57, f.s, A_INFO);

    BUF(l, 24);
    b_str(&l, "LOGGED ");
    b_num(&l, m->logged, 1);
    text(c, 2, 60, 78, l.s, A_INFO);
}

static void banner(const struct screen_model *m, cells_t *c)
{
    const struct scr_decision *d = m->ndec ? &m->dec[m->ndec - 1] : 0;
    const char *word = d ? ui_verdict_word(d->code) : 0;
    BUF(r3, 64);
    b_str(&r3, "LAST KERNEL DECISION: ");
    b_str(&r3, word ? word : "-");
    text(c, 3, 3, 57, r3.s, A_TEXT);
    text(c, 3, 60, 78, m->tainted ? "TAINTED: YES" : "TAINTED: NO", m->tainted ? A_ASK : A_INFO);
    if (!word)
        return;
    int wl = (int)slen(word);
    text(c, 4, 3 + (SCR_CON_COLS - wl) / 2, 78, word, A_TEXT);
    BUF(r5, 96);
    b_detail(&r5, d);
    if (d->code == DEC_CONFIRM_PENDING && m->pending)
        b_str(&r5, "; waiting for y/N");
    text(c, 5, 3, 78, r5.s, A_TEXT);

    /* The wipe: banner colours reach row 3 at once, rows 4 and 5 at ~70 and ~140 ms, then hold. */
    uint8_t a = verdict_attr(d->code);
    uint32_t ms = m->hz ? (m->now_tick - d->tick) * 1000u / m->hz : SCR_WIPE_ROW5_MS;
    paint(c, 3, 3, 57, a);
    if (ms >= SCR_WIPE_ROW4_MS)
        paint(c, 4, 3, 78, a);
    if (ms >= SCR_WIPE_ROW5_MS)
        paint(c, 5, 3, 78, a);
}

static int starts(const char *s, const char *p)
{
    while (*p)
        if (*s++ != *p++)
            return 0;
    return 1;
}

static void console(const struct screen_model *m, cells_t *c)
{
    for (int i = 0; i < SCR_CON_ROWS; i++) {
        const char *s = m->con[i];
        int row = 7 + i;
        if (starts(s, "CONFIRM:")) {
            paint(c, row, 3, 78, A_ASK);
            text(c, row, 3, 78, s, A_ASK);
        } else if (starts(s, "ring3> ") || (starts(s, "ring3>") && s[6] == 0)) {
            text(c, row, 3, 8, "ring3>", A_INFO);
            text(c, row, 9, 78, s + 6, A_TEXT);
        } else {
            text(c, row, 3, 78, s, A_TEXT);
        }
    }
}

/* ---- the cell panel (T-14): pictures from the last accepted STATE frame ------------------------------------
 * Rows 13-19 hold 14 rows of pixels: a cell is a top and a bottom pixel, drawn with the CP437 half blocks.
 * Pixel value = VGA colour, 0 = empty. Only the fan, the gauge and the lamp are pixels; the beam is shades. */
#define PANEL_ROW 13
#define PANEL_PX_H 14
#define SEP1_COL 20             /* column separators */
#define SEP2_COL 44
#define FAN_COL 7               /* rotor: 9x5 cells (9x10 pixels) at columns 7-15, rows 13-17 */
#define FAN_PX_W 9
#define FAN_PX_H 10
#define GAUGE_COL 22            /* dial: 21x6 cells (21x12 pixels) at columns 22-42, rows 13-18 */
#define GAUGE_PX_W 21
#define GAUGE_PX_H 12
#define LAMP_COL 46             /* E-stop lamp: 5x2 cells (5x4 pixels) at columns 46-50, rows 17-18 */
#define LAMP_ROW 17
#define SENSOR_COL 46           /* sensor head; the beam is the 32 cells after it */
#define BEAM_CELLS 32
#define PANEL_PX_W (LAMP_COL + 4) /* pixel columns 1-50: the last pixel picture is the lamp */

#define C_DGREY 8
#define C_DRED 4
#define C_BROWN 6
#define C_GREY 7
#define A_DIM SCR_ATTR(C_DGREY, SCR_BLACK)

/* 3-blade rotor, 4 frames 30 degrees apart (it repeats every 120); bit x = pixel column x; the hub is apart. */
static const uint16_t fan_blades[SCR_FAN_FRAMES][FAN_PX_H] = {
    { 0x03C, 0x038, 0x038, 0x010, 0x000, 0x1EF, 0x1EF, 0x0C6, 0x086, 0x000 },
    { 0x070, 0x0F0, 0x0F0, 0x033, 0x00F, 0x00F, 0x063, 0x0E0, 0x0E0, 0x060 },
    { 0x000, 0x0C2, 0x0C6, 0x1EF, 0x1EF, 0x000, 0x010, 0x038, 0x038, 0x078 },
    { 0x00C, 0x00E, 0x00E, 0x18C, 0x1E0, 0x1E0, 0x198, 0x01E, 0x01E, 0x01C },
};
static const uint8_t lamp_px[4] = { 0x0E, 0x1F, 0x1F, 0x0E };

/* sin(a) for a = 0..90 degrees, x 1024 (rounded). */
static const uint16_t sin_q10[91] = {
    0,    18,   36,   54,   71,   89,   107,  125,  143,  160,  178,  195,  213,  230,  248,  265,  282,  299,
    316,  333,  350,  367,  384,  400,  416,  433,  449,  465,  481,  496,  512,  527,  543,  558,  573,  587,
    602,  616,  630,  644,  658,  672,  685,  698,  711,  724,  737,  749,  761,  773,  784,  796,  807,  818,
    828,  839,  849,  859,  868,  878,  887,  896,  904,  912,  920,  928,  935,  943,  949,  956,  962,  968,
    974,  979,  984,  989,  994,  998,  1002, 1005, 1008, 1011, 1014, 1016, 1018, 1020, 1022, 1023, 1023, 1024,
    1024,
};

uint32_t screen_fan_ticks(uint8_t duty)
{
    return duty == 0 ? 0 : duty < 30 ? 16 : duty < 50 ? 12 : duty < 60 ? 10 : duty < 70 ? 8 : duty < 80 ? 7 : 6;
}

static int stale(const struct screen_model *m) { return m->link == SCR_LINK_STALE; }

static int fan_frozen(const struct screen_model *m)
{
    return !m->have_tele || m->fan == 0 || m->estop_latched || stale(m) || m->link == SCR_LINK_LOST;
}

uint32_t screen_fan_frame(const struct screen_model *m)
{
    if (fan_frozen(m))
        return 0;
    return m->now_tick / screen_fan_ticks(m->fan) % SCR_FAN_FRAMES;
}

typedef uint8_t panel_px[PANEL_PX_H][PANEL_PX_W]; /* 700 bytes of the 16 KiB kernel stack */

static void pset(panel_px p, int x, int y, uint8_t colour)
{
    if (x >= 0 && x < PANEL_PX_W && y >= 0 && y < PANEL_PX_H)
        p[y][x] = colour;
}

/* Turns pixels of columns col..last, rows row..row+n-1 into cells. */
static void compose(cells_t *c, panel_px p, int col, int last, int row, int n)
{
    for (int r = row; r < row + n; r++)
        for (int k = col; k <= last; k++) {
            uint8_t top = p[(r - PANEL_ROW) * 2][k - 1], bot = p[(r - PANEL_ROW) * 2 + 1][k - 1];
            if (!top && !bot)
                put(c, r, k, ' ', A_TEXT);
            else if (top == bot)
                put(c, r, k, CH_BLOCK, SCR_ATTR(top, SCR_BLACK));
            else if (!bot)
                put(c, r, k, CH_UPPER, SCR_ATTR(top, SCR_BLACK));
            else if (!top)
                put(c, r, k, CH_LOWER, SCR_ATTR(bot, SCR_BLACK));
            else
                put(c, r, k, CH_UPPER, SCR_ATTR(top, bot));
        }
}

/* Centres `s` in columns col..last. */
static void centre(cells_t *c, int row, int col, int last, const char *s, uint8_t attr)
{
    int w = last - col + 1, n = (int)slen(s);
    text(c, row, col + (n < w ? (w - n) / 2 : 0), last, s, attr);
}

static void fan(const struct screen_model *m, cells_t *c, panel_px p)
{
    int frozen = fan_frozen(m);
    uint32_t f = screen_fan_frame(m);
    uint8_t blade = frozen ? C_DGREY : SCR_LIGHT_CYAN, hub = frozen ? C_GREY : SCR_WHITE;
    for (int y = 0; y < FAN_PX_H; y++)
        for (int x = 0; x < FAN_PX_W; x++)
            if (fan_blades[f][y] >> x & 1)
                pset(p, FAN_COL - 1 + x, y, blade);
    pset(p, FAN_COL - 1 + 4, 4, hub);
    pset(p, FAN_COL - 1 + 4, 5, hub);
    compose(c, p, FAN_COL, FAN_COL + FAN_PX_W - 1, PANEL_ROW, FAN_PX_H / 2);

    BUF(fv, 20);
    b_str(&fv, "FAN ");
    if (m->have_tele) {
        b_num(&fv, m->fan, 3);
        b_ch(&fv, '%');
    } else {
        b_str(&fv, "---");
    }
    centre(c, 18, 3, SEP1_COL - 1, fv.s, A_TEXT);
    const char *w = !m->have_tele                 ? "NO DATA"
                    : m->estop_latched            ? "HALTED (E-STOP)"
                    : stale(m)                    ? "STALE"
                    : m->link == SCR_LINK_LOST    ? "HALTED (LINK)"
                    : m->fan == 0                 ? "STOPPED"
                                                  : "TURNING";
    uint8_t a = !m->have_tele || (m->fan == 0 && !m->estop_latched && !stale(m) && m->link != SCR_LINK_LOST)
                    ? A_TEXT
                : frozen ? A_ALARM
                         : A_INFO;
    centre(c, 19, 3, SEP1_COL - 1, w, a);
}

/* Integer polar point on the dial: pixel (x, y) of the gauge for radius r (half 9x8-pixel units) at `deg`. */
#define GAUGE_CX2 189           /* pivot centre: pixel column 10, pixel row 11, in half units (18 x, 16 y) */
#define GAUGE_CY2 184
#define GAUGE_R2 172            /* arc radius */
#define MARK_R2 140             /* marks from here to the arc */
#define NEEDLE_R2 128           /* needle length */

static void polar(uint32_t deg, int r2, int *x, int *y)
{
    /* 91-180 degrees is the mirror image of 0-89, so the dial, its marks and the needle are exactly symmetric */
    uint32_t a = deg <= 90 ? deg : 180 - deg;
    *x = (GAUGE_CX2 - (r2 * (int)sin_q10[90 - a] + 512) / 1024) / 18;
    *y = (GAUGE_CY2 - (r2 * (int)sin_q10[a] + 512) / 1024) / 16;
    if (deg > 90)
        *x = GAUGE_PX_W - 1 - *x;
}

static void ray(panel_px p, uint32_t deg, int r0, int r1, uint8_t colour)
{
    for (int r = r0; r <= r1; r += 4) {
        int x, y;
        polar(deg, r, &x, &y);
        pset(p, GAUGE_COL - 1 + x, y, colour);
    }
}

static void gauge(const struct screen_model *m, cells_t *c, panel_px p)
{
    for (uint32_t a = 0; a <= 180; a++) {
        int x, y;
        polar(a, GAUGE_R2, &x, &y);
        pset(p, GAUGE_COL - 1 + x, y, a < SERVO_MIN_DEG || a > SERVO_MAX_DEG ? C_DRED : C_BROWN);
    }
    ray(p, SERVO_MIN_DEG, MARK_R2, GAUGE_R2, SCR_LIGHT_RED);
    ray(p, SERVO_MAX_DEG, MARK_R2, GAUGE_R2, SCR_LIGHT_RED);
    ray(p, 90, MARK_R2, GAUGE_R2, SCR_YELLOW);
    if (m->have_tele)
        ray(p, m->servo > 180 ? 180 : m->servo, 0, NEEDLE_R2, stale(m) ? C_DGREY : SCR_LIGHT_CYAN);
    pset(p, GAUGE_COL - 1 + 10, GAUGE_PX_H - 1, SCR_WHITE);
    compose(c, p, GAUGE_COL, GAUGE_COL + GAUGE_PX_W - 1, PANEL_ROW, GAUGE_PX_H / 2);

    BUF(sv, 32);
    b_str(&sv, "SERVO ");
    if (m->have_tele) {
        b_num(&sv, m->servo, 3);
        b_ch(&sv, (char)CH_DEG);
    } else {
        b_str(&sv, "---");
    }
    b_str(&sv, " (");
    b_num(&sv, SERVO_MIN_DEG, 1);
    b_ch(&sv, '-');
    b_num(&sv, SERVO_MAX_DEG, 1);
    b_ch(&sv, ')');
    centre(c, 19, SEP1_COL + 1, SEP2_COL - 1, sv.s, A_TEXT);
}

static void sensor(const struct screen_model *m, cells_t *c)
{
    int t = m->have_tele, echo = t && m->dist_mm != DIST_NO_ECHO, near = echo && m->dist_mm < PRESENCE_MM;
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
    text(c, 13, SENSOR_COL, 63, dv.s, A_TEXT);
    const char *w = !t ? "NO DATA" : stale(m) ? "STALE" : !echo ? "NO ECHO" : near ? "PRESENCE" : "CLEAR";
    uint8_t wa = !t ? A_TEXT : stale(m) ? A_ALARM : !echo || near ? A_ASK : A_INFO;
    text(c, 13, SCR_COLS - 1 - (int)slen(w), SCR_COLS - 2, w, wa);

    /* the beam: 0-400 mm over 32 cells, shades from the sensor to the reading, the object at the reading */
    uint8_t beam = stale(m) ? C_DGREY : near ? SCR_YELLOW : SCR_LIGHT_CYAN;
    uint32_t n = 0;
    if (echo)
        n = m->dist_mm >= SCR_DIST_SPAN_MM ? BEAM_CELLS : m->dist_mm * BEAM_CELLS / SCR_DIST_SPAN_MM;
    put(c, 14, SENSOR_COL, CH_BLOCK, SCR_ATTR(t ? SCR_WHITE : C_DGREY, SCR_BLACK));
    for (uint32_t i = 0; i < BEAM_CELLS; i++) {
        int col = SENSOR_COL + 1 + (int)i;
        if (i < n)
            put(c, 14, col, (uint8_t)(CH_SHADE3 - i * 3 / n), SCR_ATTR(beam, SCR_BLACK));
        else if (i == n && echo)
            put(c, 14, col, CH_BLOCK, SCR_ATTR(stale(m) ? C_DGREY : SCR_WHITE, SCR_BLACK));
        else
            put(c, 14, col, CH_DOT, A_DIM);
    }
    int mark = SENSOR_COL + 1 + (int)(PRESENCE_MM * BEAM_CELLS / SCR_DIST_SPAN_MM);
    put(c, 15, mark, CH_UP_T, A_MARK);
    BUF(th, 16);
    b_num(&th, PRESENCE_MM, 1);
    b_str(&th, " mm");
    text(c, 15, mark + 1, mark + 8, th.s, A_MARK);
    BUF(sp, 16);
    b_num(&sp, SCR_DIST_SPAN_MM, 1);
    b_str(&sp, " mm");
    text(c, 15, SCR_COLS - 1 - (int)slen(sp.s), SCR_COLS - 2, sp.s, A_DIM);
}

static void estop_link(const struct screen_model *m, cells_t *c, panel_px p)
{
    int t = m->have_tele, lit = m->estop_pressed || m->estop_latched;
    for (int y = 0; y < 4; y++)
        for (int x = 0; x < 5; x++)
            if (lamp_px[y] >> x & 1)
                pset(p, LAMP_COL - 1 + x, (LAMP_ROW - PANEL_ROW) * 2 + y, lit ? SCR_LIGHT_RED : C_DGREY);
    compose(c, p, LAMP_COL, LAMP_COL + 4, LAMP_ROW, 2);

    const char *es = m->estop_pressed && m->estop_latched ? "E-STOP PRESSED, LATCHED"
                     : m->estop_pressed                   ? "E-STOP PRESSED"
                     : m->estop_latched                   ? "E-STOP LATCHED"
                     : t                                  ? "E-STOP RELEASED"
                                                          : "E-STOP -";
    text(c, 17, LAMP_COL + 6, SCR_COLS - 2, es, lit ? A_ALARM : A_TEXT);
    static const char *const link_words[] = { "LINK no cell (COM3 absent)", "LINK waiting for the board",
                                              "LINK ok", "LINK STALE", "LINK LOST (board watchdog)" };
    uint8_t lk = m->link <= SCR_LINK_LOST ? m->link : SCR_LINK_NO_CELL;
    text(c, 18, LAMP_COL + 6, SCR_COLS - 2, link_words[lk],
         lk == SCR_LINK_OK ? A_INFO : lk >= SCR_LINK_STALE ? A_ALARM : A_TEXT);
    BUF(ag, 40);
    b_str(&ag, "TELEMETRY AGE ");
    if (t) {
        b_num(&ag, m->tele_age_ms, 1);
        b_str(&ag, " ms");
    } else {
        b_str(&ag, "-");
    }
    text(c, 19, LAMP_COL + 6, SCR_COLS - 2, ag.s, A_INFO);
}

static void cell(const struct screen_model *m, cells_t *c)
{
    static const int seps[2] = { SEP1_COL, SEP2_COL };
    for (int i = 0; i < 2; i++) {
        put(c, PANEL_ROW - 1, seps[i], CH_H_DOWN1, A_FRAME);
        for (int r = PANEL_ROW; r < PANEL_ROW + PANEL_PX_H / 2; r++)
            put(c, r, seps[i], CH_V1, A_FRAME);
    }
    panel_px p;
    for (int y = 0; y < PANEL_PX_H; y++)
        for (int x = 0; x < PANEL_PX_W; x++)
            p[y][x] = 0;
    fan(m, c, p);
    gauge(m, c, p);
    sensor(m, c);
    estop_link(m, c, p);
}

static void ticker(const struct screen_model *m, cells_t *c)
{
    uint32_t n = m->ndec > SCR_DECISIONS ? SCR_DECISIONS : m->ndec;
    for (uint32_t i = 0; i < n; i++) {
        const struct scr_decision *d = &m->dec[i];
        int row = 21 + (int)(SCR_DECISIONS - n + i);
        BUF(tr, 24);
        b_clock(&tr, d->tick, m->hz);
        b_str(&tr, "  REQ ");
        if (d->req)
            b_num(&tr, d->req, 1);
        else
            b_ch(&tr, '-');
        text(c, row, 3, 19, tr.s, A_INFO);
        const char *w = ui_verdict_word(d->code);
        text(c, row, 20, 31, w ? w : "-", verdict_attr(d->code));
        BUF(dt, 96);
        b_detail(&dt, d);
        text(c, row, 32, 78, dt.s, A_TEXT);
    }
}

void screen_render(const struct screen_model *m, uint16_t cells[SCR_ROWS][SCR_COLS])
{
    for (int r = 0; r < SCR_ROWS; r++)
        for (int k = 0; k < SCR_COLS; k++)
            cells[r][k] = (uint16_t)(' ' | (uint16_t)A_TEXT << 8);
    for (int r = 2; r < SCR_ROWS; r++) {
        put(cells, r, 1, CH_V, A_FRAME);
        put(cells, r, SCR_COLS, CH_V, A_FRAME);
    }
    rule(cells, 1, CH_TL, CH_TR, "RING3 / KERNEL EXECUTIVE");
    rule(cells, 6, CH_LT, CH_RT, "CONSOLE");
    rule(cells, 12, CH_LT, CH_RT, "LIVE CELL");
    rule(cells, 20, CH_LT, CH_RT, "LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST");
    rule(cells, 25, CH_BL, CH_BR, 0);
    header(m, cells);
    banner(m, cells);
    console(m, cells);
    cell(m, cells);
    ticker(m, cells);
}
