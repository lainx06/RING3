/* COM3 work-cell frame codec (hardware/protocol.md "Receiver rules"). Used by the T-01B link probe and its
 * ktests; T-04's driver builds on it. */
#include "cellframe.h"
#include "cobs.h"
#include "crc16.h"

_Static_assert(CELL_MAX_DECODED <= 254, "one COBS block: CELL_MAX_ENCODED = decoded + 1");
_Static_assert(CELL_OFF_BODY == CELL_HDR_LEN && CELL_ST_ACK_SEQ + 2 == CELL_STATE_LEN, "protocol.h layout");

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

enum cell_rx cell_state_decode(const uint8_t *enc, size_t n, struct cell_state *st)
{
    uint8_t d[CELL_MAX_DECODED];
    if (n > CELL_MAX_ENCODED)
        return CELL_RX_OVERSIZE;
    size_t m = cobs_decode(enc, n, d, sizeof d);
    if (m == (size_t)-1)
        return CELL_RX_COBS;
    if (m < CELL_MIN_DECODED)
        return CELL_RX_SHORT;
    uint16_t len = rd16(d + CELL_OFF_LEN);
    if (len > CELL_MAX_BODY)
        return CELL_RX_OVERSIZE;
    if ((size_t)len != m - CELL_MIN_DECODED)
        return CELL_RX_LEN;
    if (crc16_ccitt_false(d, CELL_HDR_LEN + len) != rd16(d + CELL_HDR_LEN + len))
        return CELL_RX_CRC;
    if (d[CELL_OFF_TYPE] != CELL_T_STATE)
        return CELL_RX_TYPE;
    if (len != CELL_STATE_LEN)
        return CELL_RX_BODY_LEN;
    const uint8_t *b = d + CELL_OFF_BODY;
    if (b[CELL_ST_ESTOP] > 1 || (b[CELL_ST_FLAGS] & CELL_F_RESERVED_MASK))
        return CELL_RX_FIELD;
    st->seq = rd16(d + CELL_OFF_SEQ);
    st->t_ms = rd32(b + CELL_ST_T_MS);
    st->fan = b[CELL_ST_FAN];
    st->servo = b[CELL_ST_SERVO];
    st->dist_mm = rd16(b + CELL_ST_DIST);
    st->estop = b[CELL_ST_ESTOP];
    st->flags = b[CELL_ST_FLAGS];
    st->ack_seq = rd16(b + CELL_ST_ACK_SEQ);
    return CELL_RX_OK;
}

const char *cell_rx_name(enum cell_rx e)
{
    static const char *const names[CELL_RX_COUNT] = { "ok",  "cobs", "oversize", "short",   "len",
                                                      "crc", "type", "body_len", "field" };
    return (unsigned)e < CELL_RX_COUNT ? names[e] : "?";
}

size_t cell_cmd_encode(uint8_t type, uint16_t seq, const uint8_t *body, size_t len, uint8_t *out, size_t cap)
{
    uint8_t d[CELL_MAX_DECODED];
    if (len > CELL_MAX_BODY)
        return 0;
    d[CELL_OFF_TYPE] = type;
    d[CELL_OFF_SEQ] = (uint8_t)seq;
    d[CELL_OFF_SEQ + 1] = (uint8_t)(seq >> 8);
    d[CELL_OFF_LEN] = (uint8_t)len;
    d[CELL_OFF_LEN + 1] = 0;
    for (size_t i = 0; i < len; i++)
        d[CELL_OFF_BODY + i] = body[i];
    uint16_t crc = crc16_ccitt_false(d, CELL_HDR_LEN + len);
    d[CELL_HDR_LEN + len] = (uint8_t)crc;
    d[CELL_HDR_LEN + len + 1] = (uint8_t)(crc >> 8);
    if (cap == 0)
        return 0;
    size_t w = cobs_encode(d, CELL_MIN_DECODED + len, out, cap - 1);
    if (w == 0)
        return 0;
    out[w] = 0x00;
    return w + 1;
}
