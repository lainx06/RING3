// RING3 work-cell frame code: CRC-16/CCITT-FALSE, COBS, frame build/parse, body checks, clamps.
// Plain C++ with no Arduino headers, no heap, no STL, so it is host-tested by `make -C firmware test` and
// compiled unchanged into the Uno sketch. Layout and rules: protocol.h, hardware/protocol.md.
#ifndef RING3_CELL_FRAME_H
#define RING3_CELL_FRAME_H

#include <stddef.h>
#include <stdint.h>

#include "protocol.h"

#define COBS_ERROR ((size_t)-1)

// CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, xorout 0). Bitwise, no table.
uint16_t crc16_ccitt_false(const uint8_t *data, size_t len);
uint16_t crc16_update(uint16_t crc, const uint8_t *data, size_t len);

// COBS. Encoded output contains no 0x00; the caller appends the delimiter.
// cobs_encode returns the encoded length, or 0 if `out` (capacity out_cap) is too small.
size_t cobs_encode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap);
// cobs_decode returns the decoded length, or COBS_ERROR on a malformed input (0x00 inside, a code that runs past
// the end of the input) or when `out` is too small. With out == nullptr it only validates and returns the
// decoded length (out_cap ignored).
size_t cobs_decode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap);

enum frame_err : uint8_t {
  FE_OK = 0,
  FE_COBS,      // COBS malformed
  FE_SHORT,     // decoded < 7 bytes (header + CRC)
  FE_OVERSIZE,  // body longer than max_body, or decoded payload longer than the scratch buffer
  FE_LEN,       // len field != actual body length
  FE_CRC,       // CRC mismatch
  FE_TYPE,      // unknown type, or a type that doesn't travel in this direction
  FE_BODY_LEN,  // body length wrong for the type
  FE_FIELD,     // a body field has an invalid value
};

const char *frame_err_name(frame_err e);

// Which side is receiving: the device accepts kernel->device types only, the kernel accepts STATE only.
enum frame_dir : uint8_t { DIR_TO_DEVICE = 0, DIR_TO_KERNEL = 1 };

struct frame_view {
  uint8_t type;
  uint16_t seq;
  uint16_t len;
  const uint8_t *body;  // points into the caller's scratch buffer
};

// Type-specific body checks (length, then fields). FE_TYPE for a type not valid in `dir`.
frame_err frame_check_body(frame_dir dir, uint8_t type, const uint8_t *body, uint16_t len);

// Builds COBS(type|seq|len|body|crc) followed by the 0x00 delimiter into `out`. `scratch` holds the decoded
// payload (needs len + 7 bytes). Returns the number of bytes written to `out` including the delimiter, or 0 if
// a buffer is too small.
size_t frame_build(uint8_t type, uint16_t seq, const uint8_t *body, uint16_t len, uint8_t *scratch,
                   size_t scratch_cap, uint8_t *out, size_t out_cap);

// Parses one encoded frame (bytes between delimiters, delimiter not included). Decodes into `scratch`, checks
// size, length field, CRC, type and body for `dir`. On FE_OK, `*out` describes the frame.
frame_err frame_parse(const uint8_t *enc, size_t enc_len, uint8_t *scratch, size_t scratch_cap, size_t max_body,
                      frame_dir dir, frame_view *out);

// Firmware clamps (second layer). Set *clamped to true when the value was changed, false otherwise.
uint8_t clamp_servo(uint8_t deg, bool *clamped);
uint8_t clamp_fan(uint8_t duty, bool *clamped);

// Little-endian helpers.
static inline uint16_t get_u16le(const uint8_t *p) { return (uint16_t)(p[0] | ((uint16_t)p[1] << 8)); }
static inline uint32_t get_u32le(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline void put_u16le(uint8_t *p, uint16_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
}
static inline void put_u32le(uint8_t *p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
  p[2] = (uint8_t)(v >> 16);
  p[3] = (uint8_t)(v >> 24);
}

#endif
