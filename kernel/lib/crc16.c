#include "crc16.h"

uint16_t crc16_update(uint16_t crc, const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

uint16_t crc16_ccitt_false(const uint8_t *data, size_t len)
{
    return crc16_update(0xFFFF, data, len);
}
