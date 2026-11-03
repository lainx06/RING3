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
#define CH_DEG 0xF8

#define A_FRAME SCR_ATTR(SCR_YELLOW, SCR_BLACK)
#define A_TEXT SCR_ATTR(SCR_WHITE, SCR_BLACK)
#define A_INFO SCR_ATTR(SCR_LIGHT_CYAN, SCR_BLACK)
#define A_ASK SCR_ATTR(SCR_BLACK, SCR_YELLOW)
#define A_ALARM SCR_ATTR(SCR_WHITE, SCR_DARK_RED)
#define A_MARK SCR_ATTR(SCR_LIGHT_RED, SCR_BLACK)

#define BAR_CELLS 20
#define DIAL_POS 21

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

static void bar(cells_t *c, int row, int col, uint32_t fill, uint8_t attr)
{
    put(c, row, col, '[', attr);
    for (uint32_t i = 0; i < BAR_CELLS; i++)
        put(c, row, col + 1 + (int)i, i < fill ? CH_BLOCK : ' ', attr);
    put(c, row, col + 1 + BAR_CELLS, ']', attr);
}

static uint32_t dial_pos(uint32_t deg)
{
    uint32_t p = (deg * (DIAL_POS - 1) + 90) / 180;
    return p > DIAL_POS - 1 ? DIAL_POS - 1 : p;
}

static void cell(const struct screen_model *m, cells_t *c)
{
    int t = m->have_tele;
    /* fan */
    BUF(fv, 20);
    b_str(&fv, "FAN ");
    if (t) {
        b_num(&fv, m->fan, 3);
        b_ch(&fv, '%');
    } else {
        b_str(&fv, "---");
    }
    text(c, 15, 3, 17, fv.s, A_TEXT);
    uint32_t ff = t ? (m->fan * BAR_CELLS + 50) / 100 : 0;
    bar(c, 15, 20, ff > BAR_CELLS ? BAR_CELLS : ff, SCR_ATTR(SCR_YELLOW, SCR_BLACK));

    /* servo: 21 positions over 0-180 deg, the kernel's 30 and 150 limits marked */
    BUF(sv, 20);
    b_str(&sv, "SERVO ");
    if (t) {
        b_num(&sv, m->servo, 3);
        b_ch(&sv, (char)CH_DEG);
    } else {
        b_str(&sv, "---");
    }
    text(c, 16, 3, 17, sv.s, A_TEXT);
    put(c, 16, 20, '[', A_FRAME);
    for (uint32_t i = 0; i < DIAL_POS; i++)
        put(c, 16, 21 + (int)i, '-', A_FRAME);
    put(c, 16, 21 + DIAL_POS, ']', A_FRAME);
    put(c, 16, 21 + (int)dial_pos(SERVO_MIN_DEG), '|', A_MARK);
    put(c, 16, 21 + (int)dial_pos(SERVO_MAX_DEG), '|', A_MARK);
    if (t)
        put(c, 16, 21 + (int)dial_pos(m->servo), '^', A_INFO);
    BUF(sc, 32);
    b_str(&sc, "0");
    b_ch(&sc, (char)CH_DEG);
    b_str(&sc, " |");
    b_num(&sc, SERVO_MIN_DEG, 1);
    b_ch(&sc, '-');
    b_num(&sc, SERVO_MAX_DEG, 1);
    b_str(&sc, " limits| 180");
    b_ch(&sc, (char)CH_DEG);
    text(c, 16, 46, 68, sc.s, A_INFO);

    /* distance: 0-400 mm, longer = full bar, no echo = words and an empty bar */
    BUF(dv, 20);
    int echo = t && m->dist_mm != DIST_NO_ECHO;
    b_str(&dv, "DIST ");
    if (!t) {
        b_str(&dv, "---");
    } else if (!echo) {
        b_str(&dv, "NO ECHO");
    } else {
        b_num(&dv, m->dist_mm, 1);
        b_str(&dv, " mm");
    }
    text(c, 17, 3, 17, dv.s, A_TEXT);
    uint32_t df = echo ? m->dist_mm * BAR_CELLS / SCR_DIST_SPAN_MM : 0;
    bar(c, 17, 20, df > BAR_CELLS ? BAR_CELLS : df, A_INFO);

    /* E-stop and link */
    const char *es = m->estop_pressed && m->estop_latched ? "E-STOP PRESSED, LATCHED"
                     : m->estop_pressed                   ? "E-STOP PRESSED"
                     : m->estop_latched                   ? "E-STOP LATCHED"
                     : t                                  ? "E-STOP released"
                                                          : "E-STOP -";
    text(c, 18, 3, 40, es, m->estop_pressed || m->estop_latched ? A_ALARM : A_TEXT);
    static const char *const link_words[] = { "LINK no cell (COM3 absent)", "LINK waiting for the board",
                                              "LINK ok", "LINK STALE", "LINK LOST (board watchdog)" };
    uint8_t lk = m->link <= SCR_LINK_LOST ? m->link : SCR_LINK_NO_CELL;
    text(c, 18, 43, 78, link_words[lk],
         lk == SCR_LINK_OK ? A_INFO : lk >= SCR_LINK_STALE ? A_ALARM : A_TEXT);

    /* presence and telemetry age */
    const char *pr = !t ? "PRESENCE -" : !echo ? "PRESENCE no echo (not clear)"
                     : m->dist_mm < PRESENCE_MM ? "PRESENCE YES" : "PRESENCE NO";
    text(c, 19, 3, 40, pr, t && (!echo || m->dist_mm < PRESENCE_MM) ? A_ASK : A_INFO);
    BUF(ag, 40);
    b_str(&ag, "TELEMETRY AGE ");
    if (t) {
        b_num(&ag, m->tele_age_ms, 1);
        b_str(&ag, " ms");
    } else {
        b_str(&ag, "-");
    }
    text(c, 19, 43, 78, ag.s, A_INFO);
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
    rule(cells, 14, CH_LT, CH_RT, "LIVE CELL");
    rule(cells, 20, CH_LT, CH_RT, "LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST");
    rule(cells, 25, CH_BL, CH_BR, 0);
    header(m, cells);
    banner(m, cells);
    console(m, cells);
    cell(m, cells);
    ticker(m, cells);
}
