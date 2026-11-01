#include "cobs.h"

size_t cobs_encode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap)
{
    size_t code_pos = 0, w = 1;
    uint8_t code = 1;
    if (out_cap == 0)
        return 0;
    for (size_t i = 0; i < n; i++) {
        if (in[i] == 0) {
            out[code_pos] = code;
            code_pos = w;
            if (w >= out_cap)
                return 0;
            w++;
            code = 1;
        } else {
            if (w >= out_cap)
                return 0;
            out[w++] = in[i];
            code++;
            if (code == 0xFF && i + 1 < n) {
                out[code_pos] = code;
                code_pos = w;
                if (w >= out_cap)
                    return 0;
                w++;
                code = 1;
            }
        }
    }
    out[code_pos] = code;
    return w;
}

size_t cobs_decode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap)
{
    size_t r = 0, w = 0;
    while (r < n) {
        uint8_t code = in[r++];
        if (code == 0)
            return (size_t)-1;
        if (r + (size_t)code - 1 > n)
            return (size_t)-1;                   /* code claims more bytes than the frame holds */
        for (uint8_t i = 1; i < code; i++) {
            if (in[r] == 0 || w >= out_cap)
                return (size_t)-1;
            out[w++] = in[r++];
        }
        if (code != 0xFF && r < n) {
            if (w >= out_cap)
                return (size_t)-1;
            out[w++] = 0;
        }
    }
    return w;
}
