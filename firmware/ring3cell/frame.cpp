// RING3 work-cell frame code. See frame.h. Hand-written for RING3 (T-06).
#include "frame.h"

uint16_t crc16_update(uint16_t crc, const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)((uint16_t)data[i] << 8);
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x8000u) {
        crc = (uint16_t)((crc << 1) ^ 0x1021u);
      } else {
        crc = (uint16_t)(crc << 1);
      }
    }
  }
  return crc;
}

uint16_t crc16_ccitt_false(const uint8_t *data, size_t len) { return crc16_update(0xFFFFu, data, len); }

// Canonical COBS: a block holds up to 254 non-zero bytes behind its code byte. A code < 0xFF means "a zero
// follows" (except at the very end); a full block (0xFF) has no implied zero, and a new block is opened after it
// only if input remains.
size_t cobs_encode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap) {
  if (out_cap == 0) {
    return 0;
  }
  size_t code_pos = 0;
  size_t o = 1;
  uint8_t code = 1;
  for (size_t i = 0; i < n; i++) {
    if (in[i] == 0) {
      out[code_pos] = code;
      if (o >= out_cap) {
        return 0;
      }
      code_pos = o++;
      code = 1;
    } else {
      if (o >= out_cap) {
        return 0;
      }
      out[o++] = in[i];
      code++;
      if (code == 0xFF && i + 1 < n) {
        out[code_pos] = code;
        if (o >= out_cap) {
          return 0;
        }
        code_pos = o++;
        code = 1;
      }
    }
  }
  out[code_pos] = code;
  return o;
}

size_t cobs_decode(const uint8_t *in, size_t n, uint8_t *out, size_t out_cap) {
  size_t i = 0;
  size_t o = 0;
  while (i < n) {
    uint8_t code = in[i++];
    if (code == 0) {
      return COBS_ERROR;
    }
    for (uint8_t k = 1; k < code; k++) {
      if (i >= n) {
        return COBS_ERROR;  // code runs past the end
      }
      uint8_t b = in[i++];
      if (b == 0) {
        return COBS_ERROR;
      }
      if (out != nullptr) {
        if (o >= out_cap) {
          return COBS_ERROR;
        }
        out[o] = b;
      }
      o++;
    }
    if (code != 0xFF && i < n) {
      if (out != nullptr) {
        if (o >= out_cap) {
          return COBS_ERROR;
        }
        out[o] = 0;
      }
      o++;
    }
  }
  return o;
}

#ifndef __AVR__
// Host-only (on the Uno these strings would sit in RAM).
const char *frame_err_name(frame_err e) {
  switch (e) {
    case FE_OK:
      return "ok";
    case FE_COBS:
      return "cobs";
    case FE_SHORT:
      return "short";
    case FE_OVERSIZE:
      return "oversize";
    case FE_LEN:
      return "len";
    case FE_CRC:
      return "crc";
    case FE_TYPE:
      return "type";
    case FE_BODY_LEN:
      return "body_len";
    case FE_FIELD:
      return "field";
  }
  return "?";
}
#endif

static frame_err want_len(uint16_t len, uint16_t want) { return len == want ? FE_OK : FE_BODY_LEN; }

frame_err frame_check_body(frame_dir dir, uint8_t type, const uint8_t *body, uint16_t len) {
  if (dir == DIR_TO_KERNEL) {
    if (type != CELL_T_STATE) {
      return FE_TYPE;
    }
    if (len != CELL_STATE_LEN) {
      return FE_BODY_LEN;
    }
    if (body[CELL_ST_ESTOP] > 1 || (body[CELL_ST_FLAGS] & CELL_F_RESERVED_MASK) != 0) {
      return FE_FIELD;
    }
    return FE_OK;
  }
  switch (type) {
    case CELL_T_PING:
    case CELL_T_ESTOP_RESET:
      return want_len(len, 0);
    case CELL_T_FAN:
    case CELL_T_SERVO:
      return want_len(len, 1);  // any value; clamped by the device, not rejected
    case CELL_T_ALARM:
      if (len != CELL_ALARM_LEN) {
        return FE_BODY_LEN;
      }
      return body[0] <= CELL_ALARM_MAX ? FE_OK : FE_FIELD;
    case CELL_T_LCD:
      if (len < CELL_LCD_MIN_LEN || len > CELL_LCD_MAX_LEN) {
        return FE_BODY_LEN;
      }
      if (body[0] >= CELL_LCD_LINES) {
        return FE_FIELD;
      }
      for (uint16_t i = 1; i < len; i++) {
        if (body[i] < CELL_LCD_CHAR_MIN || body[i] > CELL_LCD_CHAR_MAX) {
          return FE_FIELD;
        }
      }
      return FE_OK;
    default:
      return FE_TYPE;
  }
}

size_t frame_build(uint8_t type, uint16_t seq, const uint8_t *body, uint16_t len, uint8_t *scratch,
                   size_t scratch_cap, uint8_t *out, size_t out_cap) {
  size_t dec_len = (size_t)len + CELL_MIN_DECODED;
  if (dec_len > scratch_cap || out_cap < 2) {
    return 0;
  }
  scratch[CELL_OFF_TYPE] = type;
  put_u16le(scratch + CELL_OFF_SEQ, seq);
  put_u16le(scratch + CELL_OFF_LEN, len);
  for (uint16_t i = 0; i < len; i++) {
    scratch[CELL_OFF_BODY + i] = body[i];
  }
  uint16_t crc = crc16_ccitt_false(scratch, CELL_HDR_LEN + (size_t)len);
  put_u16le(scratch + CELL_HDR_LEN + len, crc);
  size_t enc = cobs_encode(scratch, dec_len, out, out_cap - 1);
  if (enc == 0) {
    return 0;
  }
  out[enc] = 0x00;
  return enc + 1;
}

frame_err frame_parse(const uint8_t *enc, size_t enc_len, uint8_t *scratch, size_t scratch_cap, size_t max_body,
                      frame_dir dir, frame_view *out) {
  size_t n = cobs_decode(enc, enc_len, nullptr, 0);  // validate + measure first, write nothing
  if (n == COBS_ERROR) {
    return FE_COBS;
  }
  if (n > scratch_cap) {
    return FE_OVERSIZE;
  }
  cobs_decode(enc, enc_len, scratch, scratch_cap);
  if (n < CELL_MIN_DECODED) {
    return FE_SHORT;
  }
  uint16_t len = get_u16le(scratch + CELL_OFF_LEN);
  if (len > max_body) {
    return FE_OVERSIZE;
  }
  if ((size_t)len + CELL_MIN_DECODED != n) {
    return FE_LEN;
  }
  uint16_t want = crc16_ccitt_false(scratch, CELL_HDR_LEN + (size_t)len);
  if (get_u16le(scratch + CELL_HDR_LEN + len) != want) {
    return FE_CRC;
  }
  uint8_t type = scratch[CELL_OFF_TYPE];
  frame_err e = frame_check_body(dir, type, scratch + CELL_OFF_BODY, len);
  if (e != FE_OK) {
    return e;
  }
  out->type = type;
  out->seq = get_u16le(scratch + CELL_OFF_SEQ);
  out->len = len;
  out->body = scratch + CELL_OFF_BODY;
  return FE_OK;
}

uint8_t clamp_servo(uint8_t deg, bool *clamped) {
  uint8_t v = deg;
  if (v < CELL_SERVO_MIN) {
    v = CELL_SERVO_MIN;
  } else if (v > CELL_SERVO_MAX) {
    v = CELL_SERVO_MAX;
  }
  *clamped = (v != deg);
  return v;
}

uint8_t clamp_fan(uint8_t duty, bool *clamped) {
  uint8_t v = duty > CELL_FAN_MAX ? (uint8_t)CELL_FAN_MAX : duty;
  *clamped = (v != duty);
  return v;
}
