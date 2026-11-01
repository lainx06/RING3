#ifndef RING3_COBS_H
#define RING3_COBS_H
#include <stddef.h>
#include <stdint.h>
/* Consistent Overhead Byte Stuffing. Encoded output contains no 0x00; the frame delimiter is added by the link
 * layer. Max overhead: 1 byte per 254 input bytes, plus 1. */
#define COBS_MAX_PAYLOAD 1024u                                   /* (prov.) ARCHITECTURE §4 */
#define COBS_ENC_MAX(n) ((n) + (n) / 254 + 1)

/* Returns the encoded length, or 0 if `out` (capacity out_cap) is too small. */
size_t cobs_encode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap);
/* Returns the decoded length, or (size_t)-1 on a malformed frame (zero byte inside, code overruns the input,
 * or output too small). */
size_t cobs_decode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap);
#endif
