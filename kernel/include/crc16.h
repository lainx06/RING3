#ifndef RING3_CRC16_H
#define RING3_CRC16_H
#include <stddef.h>
#include <stdint.h>
/* CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout. Check value for "123456789" = 0x29B1. */
uint16_t crc16_ccitt_false(const uint8_t *data, size_t len);
uint16_t crc16_update(uint16_t crc, const uint8_t *data, size_t len);
#endif
