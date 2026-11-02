#ifndef RING3_CELLFRAME_H
#define RING3_CELLFRAME_H
/* COM3 work-cell frames (hardware/protocol.md): decode a device STATE frame with the receiver rules in their
 * documented order, encode a kernel command frame. Bounded buffers only; no parsing beyond the fixed layout.
 * The layout constants come straight from the firmware's header (plain C, freestanding-clean). */
#include <stddef.h>
#include <stdint.h>
#include "../../firmware/ring3cell/protocol.h"

/* Receiver errors, in the order hardware/protocol.md checks them. */
enum cell_rx {
    CELL_RX_OK = 0,
    CELL_RX_COBS,     /* malformed COBS */
    CELL_RX_OVERSIZE, /* encoded frame too long for the buffer, or len field > CELL_MAX_BODY */
    CELL_RX_SHORT,    /* decoded < CELL_MIN_DECODED */
    CELL_RX_LEN,      /* len field != actual body length */
    CELL_RX_CRC,
    CELL_RX_TYPE,     /* anything but STATE travelling device -> kernel */
    CELL_RX_BODY_LEN, /* STATE body != CELL_STATE_LEN */
    CELL_RX_FIELD,    /* estop > 1 or reserved flag bits set */
    CELL_RX_COUNT
};

struct cell_state {
    uint16_t seq;     /* device STATE counter */
    uint32_t t_ms;
    uint8_t fan;
    uint8_t servo;
    uint16_t dist_mm;
    uint8_t estop;
    uint8_t flags;
    uint16_t ack_seq;
};

/* `enc` is one COBS frame without the 0x00 delimiter. On CELL_RX_OK fills *st. */
enum cell_rx cell_state_decode(const uint8_t *enc, size_t n, struct cell_state *st);
const char *cell_rx_name(enum cell_rx e);

/* Encodes a kernel -> device frame with a `len`-byte body (len <= CELL_MAX_BODY) and appends the 0x00
 * delimiter. Returns the wire length, or 0 if it doesn't fit `cap` (CELL_MAX_WIRE always fits). */
size_t cell_cmd_encode(uint8_t type, uint16_t seq, const uint8_t *body, size_t len, uint8_t *out, size_t cap);
#endif
