/* agentsh: the RING3 executor (ring 3, ARCHITECTURE §5, docs/LINK_ABI.md). One request per lifetime.
 *
 *   REQUEST (the console line the kernel placed at USER_ARG_ADDR) -> agent_send
 *   loop: agent_recv -> TEXT: keep it for the next ACTION
 *                    -> ACTION: parse the fixed 16-byte struct HERE (never in the kernel, INV-02); any rule
 *                       break -> observation -EINVAL and no syscall; else perform it with the one matching
 *                       syscall and report the kernel's result as the observation
 *                    -> FINAL: DONE / REFUSED      -> STOP: STOPPED (bridge reason)
 *   until FINAL, STOP, ACTIONS_PER_REQUEST actions (own cap; the kernel enforces the same cap in ring 0) or
 *   no frame for RECV_TIMEOUT ticks. Then the outcome line and the list of performed operations.
 *
 * Builds: production (no defines); AGENTSH_TEST adds a deliberate ring-3 fault on `lcd_show` with the text
 * "crash!" (fault tests); AGENTSH_NO_CAP drops the own cap so the kernel's LIMIT can be observed (A7).
 * Never prints "success", "secure", "safe" or "real-time" (INV-11). */
#include "libu.h"
#include "link_abi.h"

#define ACTION_CAP 8u                    /* ACTIONS_PER_REQUEST in kernel/policy/policy.h */
#define RECV_TIMEOUT_TICKS 3000u         /* 30 s without a bridge frame -> STOPPED */
#define OUT_MAX LINK_OBS_OUT_MAX

struct action {
    uint8_t op, flags, text_id;
    int16_t p1, p2;
    uint32_t nonce;
};

struct done_op {
    uint8_t op;
    int16_t p1;
    int32_t result;
    uint8_t parsed;                      /* 0: rejected in ring 3 (-EINVAL), no syscall */
};

static uint8_t frame[LINK_HDR_LEN + LINK_FINAL_MAX + 8];
static uint8_t obs[LINK_OBS_MAX];
static uint8_t big[2048 + 16];           /* object reads: OBJ_DATA_MAX */
static char last_text[LINK_TEXT_DATA_MAX + 1];
static uint32_t last_text_len;
static int last_text_id;                 /* id of the TEXT received immediately before, else 0 */
static struct done_op done[ACTION_CAP + 2];
static uint32_t ndone;

/* Output is line-buffered: every cons_write is an audited syscall, so one call per line keeps the tape lean. */
static char line_buf[320];
static uint32_t line_len;

static void out_flush(void)
{
    if (line_len)
        u_write(line_buf, line_len);
    line_len = 0;
}

static void out_bytes(const char *s, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
        if (line_len < sizeof line_buf)
            line_buf[line_len++] = s[i];
        if (s[i] == '\n' || line_len == sizeof line_buf)
            out_flush();
    }
}

static void out(const char *s) { out_bytes(s, u_strlen(s)); }

static void out_dec(int v)
{
    char b[12];
    int n = 0, neg = v < 0;
    uint32_t m = neg ? (uint32_t)(-v) : (uint32_t)v;
    do {
        b[n++] = (char)('0' + m % 10);
        m /= 10;
    } while (m);
    if (neg)
        b[n++] = '-';
    for (int i = 0; i < n / 2; i++) {
        char t = b[i];
        b[i] = b[n - 1 - i];
        b[n - 1 - i] = t;
    }
    out_bytes(b, (uint32_t)n);
}

static const char *const op_names[LINK_OP_MAX + 1] = {
    "?", "cell_status", "fan_set", "servo_set", "halt", "lcd_show", "list_objects", "read_object",
    "write_object", "witness",
};

static const char *errname(int r)
{
    switch (-r) {
    case EPERM: return "EPERM";
    case ENOENT: return "ENOENT";
    case EACCES: return "EACCES";
    case EFAULT: return "EFAULT";
    case EINVAL: return "EINVAL";
    case ENOSYS: return "ENOSYS";
    case ETIMEDOUT: return "ETIMEDOUT";
    case ECANCELED: return "ECANCELED";
    case ESAFETY: return "ESAFETY";
    default: return "E?";
    }
}

static void put_result(int r)
{
    out_dec(r);
    if (r < 0) {
        out(" (");
        out(errname(r));
        out(")");
    }
}

static int printable(const uint8_t *s, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++)
        if (s[i] < LINK_TEXT_CHAR_MIN || s[i] > LINK_TEXT_CHAR_MAX)
            return 0;
    return 1;
}

/* Field rules (docs/LINK_ABI.md §3, §4). 0 = good, -EINVAL otherwise. */
static int parse_action(const uint8_t *b, struct action *a)
{
    a->op = b[LINK_ACT_OFF_OP];
    a->flags = b[LINK_ACT_OFF_FLAGS];
    a->p1 = link_get_i16(b + LINK_ACT_OFF_P1);
    a->p2 = link_get_i16(b + LINK_ACT_OFF_P2);
    a->nonce = link_get_u32(b + LINK_ACT_OFF_NONCE);
    a->text_id = b[LINK_ACT_OFF_TEXT_ID];
    for (int i = 0; i < LINK_ACT_RSV_LEN; i++)
        if (b[LINK_ACT_OFF_RSV + i])
            return -EINVAL;
    if (a->flags || a->p2)
        return -EINVAL;
    if (a->op < LINK_OP_MIN || a->op > LINK_OP_MAX)
        return -EINVAL;
    int has_text = a->op == LINK_OP_LCD_SHOW || a->op == LINK_OP_WRITE_OBJECT;
    if (has_text ? a->text_id == 0 : a->text_id != 0)
        return -EINVAL;
    switch (a->op) {
    case LINK_OP_FAN_SET:
        if (a->p1 < 0 || a->p1 > LINK_DUTY_MAX)
            return -EINVAL;
        break;
    case LINK_OP_SERVO_SET:
        if (a->p1 < 0 || a->p1 > LINK_DEG_MAX)
            return -EINVAL;
        break;
    case LINK_OP_LCD_SHOW:
        if (a->p1 < 0 || a->p1 > LINK_LCD_LINE_MAX)
            return -EINVAL;
        break;
    case LINK_OP_READ_OBJECT:
    case LINK_OP_WRITE_OBJECT:
        if (a->p1 < 0 || a->p1 >= LINK_OBJ_COUNT)
            return -EINVAL;
        break;
    default:
        if (a->p1 != 0)
            return -EINVAL;
        break;
    }
    if (has_text) {
        if (last_text_id == 0 || a->text_id != last_text_id)
            return -EINVAL;              /* the TEXT must have come immediately before, with this id */
        if (a->op == LINK_OP_LCD_SHOW && last_text_len > LINK_LCD_TEXT_MAX)
            return -EINVAL;
    }
    return 0;
}

/* Performs a parsed action with its one syscall; fills obs output, returns the result. */
static int perform(const struct action *a, uint32_t *out_len, int *truncated)
{
    int r;
    *out_len = 0;
    *truncated = 0;
    switch (a->op) {
    case LINK_OP_CELL_STATUS:
        r = u_cell_status(big);
        if (r > 0 && r <= 16) {
            for (int i = 0; i < r; i++)
                obs[LINK_OBS_OFF_OUT + i] = big[i];
            *out_len = (uint32_t)r;
        }
        return r;
    case LINK_OP_FAN_SET:
        return u_cell_fan(a->p1);
    case LINK_OP_SERVO_SET:
        return u_cell_servo(a->p1);
    case LINK_OP_HALT:
        return u_cell_halt();
    case LINK_OP_LCD_SHOW:
#ifdef AGENTSH_TEST
        if (last_text_len == 6 && last_text[0] == 'c' && last_text[1] == 'r' && last_text[2] == 'a' &&
            last_text[3] == 's' && last_text[4] == 'h' && last_text[5] == '!')
            *(volatile int *)0 = 1;      /* test image: a deliberate ring-3 fault mid-request */
#endif
        return u_cell_lcd(a->p1, last_text);
    case LINK_OP_LIST_OBJECTS:
        r = u_obj_list((char *)big, OUT_MAX);
        if (r > 0) {
            for (int i = 0; i < r; i++)
                obs[LINK_OBS_OFF_OUT + i] = big[i];
            *out_len = (uint32_t)r;
        }
        return r;
    case LINK_OP_READ_OBJECT:
        r = u_obj_read(a->p1, big, 2048);
        if (r > 0) {
            uint32_t n = (uint32_t)r;
            if (n > OUT_MAX) {
                n = OUT_MAX;
                *truncated = 1;
            }
            for (uint32_t i = 0; i < n; i++)
                obs[LINK_OBS_OFF_OUT + i] = big[i];
            *out_len = n;
        }
        return r;
    case LINK_OP_WRITE_OBJECT:
        return u_obj_write(a->p1, last_text, last_text_len);
    case LINK_OP_WITNESS:
        return -ENOSYS;                  /* no handler yet (T-07 decides who answers it) */
    default:
        return -EINVAL;
    }
}

static int send_observation(const struct action *a, int result, uint32_t out_len, int truncated)
{
    link_put_u32(obs + LINK_OBS_OFF_NONCE, a->nonce);
    link_put_u32(obs + LINK_OBS_OFF_RESULT, (uint32_t)result);
    obs[LINK_OBS_OFF_OP] = a->op;
    obs[LINK_OBS_OFF_FLAGS] = truncated ? LINK_OBSF_TRUNCATED : 0;
    obs[LINK_OBS_OFF_RSV] = 0;
    obs[LINK_OBS_OFF_RSV + 1] = 0;
    return u_agent_send(LINK_T_OBSERVATION, obs, LINK_OBS_HDR_LEN + out_len);
}

static void record(const struct action *a, int result, int parsed)
{
    if (ndone < sizeof done / sizeof done[0]) {
        done[ndone].op = a->op;
        done[ndone].p1 = a->p1;
        done[ndone].result = result;
        done[ndone].parsed = (uint8_t)parsed;
        ndone++;
    }
}

static void report(const char *outcome, const char *detail, const uint8_t *text, uint32_t text_len)
{
    out("OUTCOME ");
    out(outcome);
    if (detail) {
        out(" ");
        out(detail);
    }
    if (text && text_len) {
        out(": ");
        out_bytes((const char *)text, text_len);
    }
    out("\nEXECUTED ");
    out_dec((int)ndone);
    out(" operation(s):\n");
    for (uint32_t i = 0; i < ndone; i++) {
        out("  ");
        out_dec((int)i + 1);
        out(" ");
        out(done[i].op <= LINK_OP_MAX ? op_names[done[i].op] : "?");
        out("(");
        out_dec(done[i].p1);
        out(") -> ");
        put_result(done[i].result);
        if (!done[i].parsed)
            out(" rejected in ring 3, no syscall");
        out("\n");
    }
    out_flush();
}

static const char *stop_name(uint8_t reason)
{
    switch (reason) {
    case LINK_STOP_INVALID_REPLIES: return "invalid_replies";
    case LINK_STOP_BUDGET: return "budget";
    case LINK_STOP_TIMEOUT: return "timeout";
    case LINK_STOP_MODEL_AUTH: return "model_auth";
    case LINK_STOP_TRANSPORT: return "transport";
    case LINK_STOP_OPERATOR: return "operator";
    default: return "unknown";
    }
}

int main(void)
{
    const char *req = (const char *)USER_ARG_ADDR;
    uint32_t req_len = u_strlen(req);
    uint32_t actions = 0;

    out("agentsh: request \"");
    out_bytes(req, req_len);
    out("\"\n");
    int r = u_agent_send(LINK_T_REQUEST, req, req_len);
    if (r < 0) {
        out("agentsh: agent_send(REQUEST) -> ");
        put_result(r);
        out("\n");
        report("ERROR", "link: REQUEST not sent", 0, 0);
        return 3;
    }
    for (;;) {
        r = u_agent_recv(frame, sizeof frame, RECV_TIMEOUT_TICKS);
        if (r == -ETIMEDOUT) {
            report("STOPPED", "no frame from the bridge within 30 s", 0, 0);
            return 2;
        }
        if (r < LINK_HDR_LEN) {
            out("agentsh: agent_recv -> ");
            put_result(r);
            out("\n");
            report("ERROR", "link: agent_recv failed", 0, 0);
            return 3;
        }
        uint8_t type = frame[LINK_OFF_TYPE];
        uint16_t len = link_get_u16(frame + LINK_OFF_LEN);
        const uint8_t *body = frame + LINK_OFF_BODY;
        if ((uint32_t)r != (uint32_t)LINK_HDR_LEN + len) {
            report("ERROR", "link: frame length mismatch", 0, 0);
            return 3;
        }
        if (type == LINK_T_TEXT) {
            uint32_t n = len - 1;
            if (body[LINK_TEXT_OFF_ID] == 0 || n > LINK_TEXT_DATA_MAX || !printable(body + 1, n)) {
                last_text_id = 0;        /* a bad TEXT binds to nothing: the next ACTION is -EINVAL */
                continue;
            }
            last_text_id = body[LINK_TEXT_OFF_ID];
            last_text_len = n;
            for (uint32_t i = 0; i < n; i++)
                last_text[i] = (char)body[1 + i];
            last_text[n] = 0;
            continue;
        }
        if (type == LINK_T_ACTION) {
            struct action a;
            int ok = parse_action(body, &a);
            last_text_id = 0;            /* a TEXT binds only to the ACTION right after it */
            actions++;
            out("agentsh: action ");
            out_dec((int)actions);
            out(" ");
            out(a.op >= LINK_OP_MIN && a.op <= LINK_OP_MAX ? op_names[a.op] : "?");
            out("(");
            out_dec(a.p1);
            out(")");
            int result;
            uint32_t out_len = 0;
            int truncated = 0;
            if (ok < 0) {
                result = -EINVAL;
                out(" rejected in ring 3 (field rule) -> ");
            } else {
                result = perform(&a, &out_len, &truncated);
                out(" -> ");
            }
            put_result(result);
            out("\n");
            record(&a, result, ok == 0);
            r = send_observation(&a, result, out_len, truncated);
            if (r < 0) {
                report("ERROR", "link: OBSERVATION not sent", 0, 0);
                return 3;
            }
#ifndef AGENTSH_NO_CAP
            if (actions >= ACTION_CAP) {
                report("STOPPED", "action limit reached (8 per request)", 0, 0);
                return 2;
            }
#endif
            continue;
        }
        last_text_id = 0;
        if (type == LINK_T_FINAL) {
            uint8_t kind = body[LINK_FINAL_OFF_KIND];
            const uint8_t *text = body + LINK_FINAL_OFF_TEXT;
            uint32_t n = len - 1;
            if (!printable(text, n))
                n = 0;
            if (kind == LINK_FINAL_DONE) {
                report("DONE", 0, text, n);
                return 0;
            }
            if (kind == LINK_FINAL_REFUSE) {
                report("REFUSED", 0, text, n);
                return 0;
            }
            report("ERROR", "link: FINAL with an unknown kind", 0, 0);
            return 3;
        }
        if (type == LINK_T_STOP) {
            report("STOPPED", "by the bridge:", (const uint8_t *)stop_name(body[LINK_STOP_OFF_REASON]),
                   u_strlen(stop_name(body[LINK_STOP_OFF_REASON])));
            return 2;
        }
        report("ERROR", "link: unexpected frame type", 0, 0);
        return 3;
    }
}
