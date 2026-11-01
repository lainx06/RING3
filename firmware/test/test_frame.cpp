// Host tests for firmware/ring3cell/frame.cpp (T-06 A2). Run by `make -C firmware test`.
//   test_frame             run all checks, one line per case, exit 1 on any failure
//   test_frame --vectors   print the link vectors (firmware/test/link_vectors.txt) to stdout
#include <stdio.h>
#include <string.h>

#include "../ring3cell/frame.h"

static int g_pass = 0;
static int g_fail = 0;

static void check(bool ok, const char *name) {
  if (ok) {
    g_pass++;
    printf("ok    %s\n", name);
  } else {
    g_fail++;
    printf("FAIL  %s\n", name);
  }
}

// Host limit for the COBS run / max-frame cases (ARCHITECTURE §4 max decoded payload is 1024 on COM2).
static const size_t HOST_MAX_BODY = 1024;
static const size_t HOST_MAX_DECODED = HOST_MAX_BODY + CELL_MIN_DECODED;
static const size_t HOST_MAX_ENCODED = HOST_MAX_DECODED + HOST_MAX_DECODED / 254 + 1;

// ---- helpers ------------------------------------------------------------------------------------------------

static bool cobs_roundtrip(const uint8_t *in, size_t n, size_t expect_enc_len) {
  static uint8_t enc[HOST_MAX_ENCODED + 16];
  static uint8_t dec[HOST_MAX_DECODED + 16];
  size_t e = cobs_encode(in, n, enc, sizeof enc);
  if (e == 0 || (expect_enc_len != 0 && e != expect_enc_len)) {
    return false;
  }
  for (size_t i = 0; i < e; i++) {
    if (enc[i] == 0) {
      return false;
    }
  }
  if (cobs_decode(enc, e, nullptr, 0) != n) {
    return false;
  }
  size_t d = cobs_decode(enc, e, dec, sizeof dec);
  return d == n && (n == 0 || memcmp(in, dec, n) == 0);
}

// Builds a frame and returns the encoded bytes WITHOUT the delimiter (what the receiver hands to frame_parse).
static size_t build(uint8_t type, uint16_t seq, const uint8_t *body, uint16_t len, uint8_t *enc, size_t cap) {
  static uint8_t scratch[HOST_MAX_DECODED];
  size_t w = frame_build(type, seq, body, len, scratch, sizeof scratch, enc, cap);
  return w == 0 ? 0 : w - 1;
}

// Builds a decoded payload by hand (so the len field and CRC can be wrong on purpose), then COBS-encodes it.
static size_t raw_frame(const uint8_t *dec, size_t n, uint8_t *enc, size_t cap) { return cobs_encode(dec, n, enc, cap); }

static size_t make_dec(uint8_t type, uint16_t seq, uint16_t len_field, const uint8_t *body, size_t body_n,
                       uint8_t *dec) {
  dec[0] = type;
  put_u16le(dec + 1, seq);
  put_u16le(dec + 3, len_field);
  if (body_n) {
    memcpy(dec + 5, body, body_n);
  }
  put_u16le(dec + 5 + body_n, crc16_ccitt_false(dec, 5 + body_n));
  return 7 + body_n;
}

static frame_err parse_dev(const uint8_t *enc, size_t n, frame_view *v) {
  uint8_t scratch[CELL_MAX_DECODED];
  static uint8_t keep[CELL_MAX_DECODED];
  frame_err e = frame_parse(enc, n, scratch, sizeof scratch, CELL_MAX_BODY, DIR_TO_DEVICE, v);
  if (e == FE_OK) {  // keep the body valid after return
    memcpy(keep, scratch, sizeof scratch);
    v->body = keep + CELL_OFF_BODY;
  }
  return e;
}

static frame_err parse_host(const uint8_t *enc, size_t n, frame_dir dir) {
  static uint8_t scratch[HOST_MAX_DECODED];
  frame_view v;
  return frame_parse(enc, n, scratch, sizeof scratch, HOST_MAX_BODY, dir, &v);
}

static frame_err parse_dec_dev(const uint8_t *dec, size_t n) {
  uint8_t enc[HOST_MAX_ENCODED + 16];
  size_t e = raw_frame(dec, n, enc, sizeof enc);
  frame_view v;
  return parse_dev(enc, e, &v);
}

static void check_err(frame_err got, frame_err want, const char *name) {
  if (got != want) {
    printf("      (got %s, want %s)\n", frame_err_name(got), frame_err_name(want));
  }
  check(got == want, name);
}

// ---- tests --------------------------------------------------------------------------------------------------

static void test_crc() {
  const uint8_t s[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  check(crc16_ccitt_false(s, sizeof s) == CELL_CRC_CHECK, "crc: check value 0x29B1 for \"123456789\"");
  check(crc16_ccitt_false(s, 0) == 0xFFFF, "crc: empty input = init 0xFFFF");
  uint16_t c = crc16_update(0xFFFF, s, 4);
  c = crc16_update(c, s + 4, 5);
  check(c == CELL_CRC_CHECK, "crc: incremental update matches one-shot");
}

static void test_cobs() {
  static uint8_t buf[2048];
  check(cobs_roundtrip(buf, 0, 1), "cobs: empty -> 01");
  uint8_t z1[] = {0x00};
  check(cobs_roundtrip(z1, 1, 2), "cobs: single 00 -> 01 01");
  uint8_t z[16] = {0};
  check(cobs_roundtrip(z, sizeof z, sizeof z + 1), "cobs: 16 zeros");
  uint8_t mix[] = {0x11, 0x22, 0x00, 0x33, 0x00, 0x00, 0x44};
  check(cobs_roundtrip(mix, sizeof mix, sizeof mix + 1), "cobs: data with zeros");
  for (size_t i = 0; i < sizeof buf; i++) {
    buf[i] = (uint8_t)(1 + i % 255);
  }
  check(cobs_roundtrip(buf, 253, 254), "cobs: run of 253 non-zero -> 254 B");
  check(cobs_roundtrip(buf, 254, 255), "cobs: run of 254 non-zero -> 255 B (no trailing block)");
  check(cobs_roundtrip(buf, 255, 257), "cobs: run of 255 non-zero -> 257 B");
  uint8_t run0[255];
  memcpy(run0, buf, 254);
  run0[254] = 0;
  check(cobs_roundtrip(run0, 255, 257), "cobs: 254 non-zero then 00 -> FF..01 01");
  // Max frames: device (24 B) and host (1031 B) with zeros sprinkled in, and host-size with no zeros.
  for (size_t i = 0; i < HOST_MAX_DECODED; i++) {
    buf[i] = (i % 7 == 3) ? 0 : (uint8_t)(i * 37 + 1);
    if (buf[i] == 0 && i % 7 != 3) {
      buf[i] = 1;
    }
  }
  check(cobs_roundtrip(buf, CELL_MAX_DECODED, 0), "cobs: device max frame (24 B) round-trip");
  check(cobs_roundtrip(buf, HOST_MAX_DECODED, 0), "cobs: host max frame (1031 B) round-trip");
  for (size_t i = 0; i < HOST_MAX_DECODED; i++) {
    buf[i] = (uint8_t)(1 + i % 255);
  }
  check(cobs_roundtrip(buf, HOST_MAX_DECODED, HOST_MAX_DECODED + HOST_MAX_DECODED / 254 + 1),
        "cobs: host max frame without zeros hits the worst-case overhead");
  // Worst-case device encoded size.
  uint8_t enc[64];
  check(cobs_encode(buf, CELL_MAX_DECODED, enc, sizeof enc) == CELL_MAX_ENCODED, "cobs: device max encodes to 25 B");
  check(cobs_encode(buf, CELL_MAX_DECODED, enc, CELL_MAX_ENCODED - 1) == 0, "cobs: encode refuses a short buffer");
  check(cobs_encode(buf, 0, enc, 0) == 0, "cobs: encode refuses a zero-size buffer");

  uint8_t out[16];
  const uint8_t embedded0[] = {0x03, 0x11, 0x00};
  check(cobs_decode(embedded0, sizeof embedded0, out, sizeof out) == COBS_ERROR, "cobs: decode rejects embedded 00");
  const uint8_t code0[] = {0x00};
  check(cobs_decode(code0, 1, out, sizeof out) == COBS_ERROR, "cobs: decode rejects code byte 00");
  const uint8_t trunc[] = {0x05, 0x11, 0x22};
  check(cobs_decode(trunc, sizeof trunc, out, sizeof out) == COBS_ERROR, "cobs: decode rejects truncated block");
  check(cobs_decode(trunc, sizeof trunc, nullptr, 0) == COBS_ERROR, "cobs: count-only decode rejects truncated block");
  const uint8_t ok4[] = {0x05, 0x11, 0x22, 0x33, 0x44};
  check(cobs_decode(ok4, sizeof ok4, out, 3) == COBS_ERROR, "cobs: decode rejects output overflow");
  check(cobs_decode(ok4, sizeof ok4, out, 4) == 4, "cobs: decode fits exactly");
  check(cobs_decode(ok4, sizeof ok4, nullptr, 0) == 4, "cobs: count-only decode returns the length");
}

static void test_accept_all_types() {
  uint8_t enc[64];
  frame_view v;
  size_t n;

  n = build(CELL_T_PING, 1, nullptr, 0, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.type == CELL_T_PING && v.seq == 1 && v.len == 0, "accept: PING");

  uint8_t fan[] = {40};
  n = build(CELL_T_FAN, 2, fan, 1, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.type == CELL_T_FAN && v.body[0] == 40, "accept: FAN 40");
  uint8_t fan255[] = {255};
  n = build(CELL_T_FAN, 3, fan255, 1, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.body[0] == 255, "accept: FAN 255 (clamped later, not rejected)");

  uint8_t servo[] = {170};
  n = build(CELL_T_SERVO, 0xBEEF, servo, 1, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.type == CELL_T_SERVO && v.seq == 0xBEEF && v.body[0] == 170,
        "accept: SERVO 170, seq 0xBEEF");

  uint8_t lcd[17] = {1, 'R', 'I', 'N', 'G', '3', ' ', 'R', 'E', 'A', 'D', 'Y', ' ', '-', '_', '~', '!'};
  n = build(CELL_T_LCD, 4, lcd, 17, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.len == 17 && memcmp(v.body, lcd, 17) == 0,
        "accept: LCD line 1, 16 chars (max body)");
  uint8_t lcd0[] = {0};
  n = build(CELL_T_LCD, 5, lcd0, 1, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.len == 1, "accept: LCD line 0, empty text");

  for (uint8_t p = 0; p <= CELL_ALARM_MAX; p++) {
    uint8_t al[] = {p};
    n = build(CELL_T_ALARM, 6, al, 1, enc, sizeof enc);
    check(n && parse_dev(enc, n, &v) == FE_OK && v.body[0] == p, "accept: ALARM 0/1/2");
  }

  n = build(CELL_T_ESTOP_RESET, 7, nullptr, 0, enc, sizeof enc);
  check(n && parse_dev(enc, n, &v) == FE_OK && v.type == CELL_T_ESTOP_RESET, "accept: ESTOP_RESET");

  uint8_t st[CELL_STATE_LEN] = {0};
  put_u32le(st + CELL_ST_T_MS, 123456);
  st[CELL_ST_FAN] = 40;
  st[CELL_ST_SERVO] = 90;
  put_u16le(st + CELL_ST_DIST, 312);
  st[CELL_ST_FLAGS] = CELL_F_CLAMP | CELL_F_ESTOP_LATCHED;
  put_u16le(st + CELL_ST_ACK_SEQ, 2);
  n = build(CELL_T_STATE, 9, st, CELL_STATE_LEN, enc, sizeof enc);
  {
    uint8_t scratch[CELL_MAX_DECODED];
    frame_view sv;
    frame_err e = frame_parse(enc, n, scratch, sizeof scratch, CELL_MAX_BODY, DIR_TO_KERNEL, &sv);
    check(e == FE_OK && sv.len == CELL_STATE_LEN && get_u32le(sv.body) == 123456 &&
              get_u16le(sv.body + CELL_ST_DIST) == 312 && get_u16le(sv.body + CELL_ST_ACK_SEQ) == 2,
          "accept: STATE toward the kernel, fields round-trip");
    check(n + 1 == 21, "size: STATE is 21 B on the wire");
  }
  n = build(CELL_T_LCD, 8, lcd, 17, enc, sizeof enc);
  check(n + 1 == CELL_MAX_WIRE, "size: max LCD frame is 26 B on the wire");
}

static void test_reject() {
  uint8_t enc[HOST_MAX_ENCODED + 16];
  uint8_t dec[HOST_MAX_DECODED + 8];
  frame_view v;
  size_t n;

  uint8_t fan[] = {40};
  n = build(CELL_T_FAN, 2, fan, 1, enc, sizeof enc);
  // Flip one CRC bit in the decoded payload and re-encode.
  size_t dn = make_dec(CELL_T_FAN, 2, 1, fan, 1, dec);
  dec[dn - 1] ^= 0x01;
  check_err(parse_dec_dev(dec, dn), FE_CRC, "reject: bad CRC");
  dn = make_dec(CELL_T_FAN, 2, 1, fan, 1, dec);
  dec[5] ^= 0x10;  // body changed after the CRC was computed
  check_err(parse_dec_dev(dec, dn), FE_CRC, "reject: body corrupted (CRC catches it)");

  check_err(parse_dev(enc, n - 1, &v), FE_COBS, "reject: truncated encoding (last byte lost)");
  check_err(parse_dec_dev(dec, 6), FE_SHORT, "reject: decoded 6 B (< header + CRC)");
  check_err(parse_dev(enc, 0, &v), FE_SHORT, "reject: empty frame between delimiters");
  uint8_t junk[] = {0x02, 0x00};
  check_err(parse_dev(junk, sizeof junk, &v), FE_COBS, "reject: 00 inside the encoding");

  // len field mismatch both ways
  dn = make_dec(CELL_T_FAN, 2, 2, fan, 1, dec);
  check_err(parse_dec_dev(dec, dn), FE_LEN, "reject: len field 2, body 1");
  uint8_t two[] = {40, 41};
  dn = make_dec(CELL_T_FAN, 2, 1, two, 2, dec);
  check_err(parse_dec_dev(dec, dn), FE_LEN, "reject: len field 1, body 2");

  // Oversize vs the device limit (17) and the host limit (1024).
  static uint8_t big[HOST_MAX_BODY + 2];
  memset(big, 'A', sizeof big);
  big[0] = 0;
  dn = make_dec(CELL_T_LCD, 2, 18, big, 18, dec);
  check_err(parse_dec_dev(dec, dn), FE_OVERSIZE, "reject: LCD body 18 B on the device (> 17)");
  dn = make_dec(CELL_T_LCD, 2, 18, big, 18, dec);
  {
    size_t e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_DEVICE), FE_BODY_LEN, "reject: LCD body 18 B with host limit -> body_len");
  }
  dn = make_dec(CELL_T_LCD, 2, (uint16_t)HOST_MAX_BODY, big, HOST_MAX_BODY, dec);
  {
    size_t e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_DEVICE), FE_BODY_LEN, "reject: body 1024 B passes the host size check");
  }
  dn = make_dec(CELL_T_LCD, 2, (uint16_t)(HOST_MAX_BODY + 1), big, HOST_MAX_BODY + 1, dec);
  {
    size_t e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_DEVICE), FE_OVERSIZE, "reject: body 1025 B (> host limit 1024)");
    frame_view hv;
    uint8_t small[CELL_MAX_DECODED];
    check_err(frame_parse(enc, e, small, sizeof small, CELL_MAX_BODY, DIR_TO_DEVICE, &hv), FE_OVERSIZE,
              "reject: decoded payload larger than the device scratch buffer");
  }
  // A len field that claims a huge body on a short frame.
  dn = make_dec(CELL_T_FAN, 2, 0xFFFF, fan, 1, dec);
  check_err(parse_dec_dev(dec, dn), FE_OVERSIZE, "reject: len field 0xFFFF");

  // Unknown types and wrong direction.
  const uint8_t unknown[] = {0x00, 0x07, 0x42, 0x80, 0xFF};
  for (uint8_t t : unknown) {
    dn = make_dec(t, 2, 0, nullptr, 0, dec);
    check_err(parse_dec_dev(dec, dn), FE_TYPE, "reject: unknown type (00/07/42/80/FF)");
  }
  uint8_t st[CELL_STATE_LEN] = {0};
  dn = make_dec(CELL_T_STATE, 2, CELL_STATE_LEN, st, CELL_STATE_LEN, dec);
  check_err(parse_dec_dev(dec, dn), FE_TYPE, "reject: STATE sent to the device");
  dn = make_dec(CELL_T_FAN, 2, 1, fan, 1, dec);
  {
    size_t e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_KERNEL), FE_TYPE, "reject: FAN sent to the kernel");
  }

  // Wrong body length per type.
  struct {
    uint8_t type;
    uint16_t len;
    const char *name;
  } bad_len[] = {
      {CELL_T_PING, 1, "reject: PING with 1-byte body"},
      {CELL_T_FAN, 0, "reject: FAN with empty body"},
      {CELL_T_FAN, 2, "reject: FAN with 2-byte body"},
      {CELL_T_SERVO, 0, "reject: SERVO with empty body"},
      {CELL_T_SERVO, 2, "reject: SERVO with 2-byte body"},
      {CELL_T_LCD, 0, "reject: LCD with empty body"},
      {CELL_T_ALARM, 0, "reject: ALARM with empty body"},
      {CELL_T_ALARM, 2, "reject: ALARM with 2-byte body"},
      {CELL_T_ESTOP_RESET, 1, "reject: ESTOP_RESET with 1-byte body"},
  };
  uint8_t zeros[4] = {0, 0, 0, 0};
  for (auto &c : bad_len) {
    dn = make_dec(c.type, 2, c.len, zeros, c.len, dec);
    check_err(parse_dec_dev(dec, dn), FE_BODY_LEN, c.name);
  }
  {
    uint8_t st11[CELL_STATE_LEN] = {0};
    dn = make_dec(CELL_T_STATE, 2, CELL_STATE_LEN - 1, st11, CELL_STATE_LEN - 1, dec);
    size_t e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_KERNEL), FE_BODY_LEN, "reject: STATE with 11-byte body");
  }

  // Invalid fields.
  uint8_t lcd_line2[] = {2, 'X'};
  dn = make_dec(CELL_T_LCD, 2, 2, lcd_line2, 2, dec);
  check_err(parse_dec_dev(dec, dn), FE_FIELD, "reject: LCD line 2");
  uint8_t lcd_ctrl[] = {0, 'O', 'K', 0x0A};
  dn = make_dec(CELL_T_LCD, 2, 4, lcd_ctrl, 4, dec);
  check_err(parse_dec_dev(dec, dn), FE_FIELD, "reject: LCD text with 0x0A");
  uint8_t lcd_hi[] = {0, 0x7F};
  dn = make_dec(CELL_T_LCD, 2, 2, lcd_hi, 2, dec);
  check_err(parse_dec_dev(dec, dn), FE_FIELD, "reject: LCD text with 0x7F");
  uint8_t lcd_zero[] = {0, 'A', 0x00, 'B'};
  dn = make_dec(CELL_T_LCD, 2, 4, lcd_zero, 4, dec);
  check_err(parse_dec_dev(dec, dn), FE_FIELD, "reject: LCD text with 0x00");
  uint8_t al3[] = {3};
  dn = make_dec(CELL_T_ALARM, 2, 1, al3, 1, dec);
  check_err(parse_dec_dev(dec, dn), FE_FIELD, "reject: ALARM pattern 3");
  {
    uint8_t stf[CELL_STATE_LEN] = {0};
    stf[CELL_ST_FLAGS] = 0x40;
    dn = make_dec(CELL_T_STATE, 2, CELL_STATE_LEN, stf, CELL_STATE_LEN, dec);
    size_t e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_KERNEL), FE_FIELD, "reject: STATE with a reserved flag bit");
    stf[CELL_ST_FLAGS] = 0;
    stf[CELL_ST_ESTOP] = 2;
    dn = make_dec(CELL_T_STATE, 2, CELL_STATE_LEN, stf, CELL_STATE_LEN, dec);
    e = raw_frame(dec, dn, enc, sizeof enc);
    check_err(parse_host(enc, e, DIR_TO_KERNEL), FE_FIELD, "reject: STATE with estop = 2");
  }

  // Builder refuses buffers that are too small.
  uint8_t small_out[8];
  uint8_t scratch[CELL_MAX_DECODED];
  uint8_t lcd[17] = {0};
  memset(lcd + 1, 'A', 16);
  check(frame_build(CELL_T_LCD, 1, lcd, 17, scratch, sizeof scratch, small_out, sizeof small_out) == 0,
        "build: refuses a short output buffer");
  check(frame_build(CELL_T_LCD, 1, lcd, 17, scratch, 23, enc, sizeof enc) == 0, "build: refuses a short scratch buffer");
}

static void test_clamps() {
  struct {
    uint8_t in, out;
    bool clamped;
  } servo[] = {{0, 15, true},     {14, 15, true},   {15, 15, false},  {90, 90, false},
               {165, 165, false}, {166, 165, true}, {170, 165, true}, {255, 165, true}};
  for (auto &c : servo) {
    bool cl = !c.clamped;
    uint8_t v = clamp_servo(c.in, &cl);
    char name[64];
    snprintf(name, sizeof name, "clamp: servo %u -> %u%s", c.in, c.out, c.clamped ? " (flag)" : "");
    check(v == c.out && cl == c.clamped, name);
  }
  struct {
    uint8_t in, out;
    bool clamped;
  } fan[] = {{0, 0, false}, {80, 80, false}, {81, 80, true}, {100, 80, true}, {255, 80, true}};
  for (auto &c : fan) {
    bool cl = !c.clamped;
    uint8_t v = clamp_fan(c.in, &cl);
    char name[64];
    snprintf(name, sizeof name, "clamp: fan %u -> %u%s", c.in, c.out, c.clamped ? " (flag)" : "");
    check(v == c.out && cl == c.clamped, name);
  }
}

// ---- vectors ------------------------------------------------------------------------------------------------

static bool g_print = false;  // --vectors prints; otherwise the same table is checked

static void hex(const uint8_t *p, size_t n) {
  if (!g_print) {
    return;
  }
  if (n == 0) {
    printf("-");
  }
  for (size_t i = 0; i < n; i++) {
    printf("%02x", p[i]);
  }
}

static void vec_frame(const char *name, const char *expect, const uint8_t *dec, size_t n) {
  uint8_t enc[HOST_MAX_ENCODED + 1];
  size_t e = cobs_encode(dec, n, enc, sizeof enc - 1);
  enc[e++] = 0;
  if (!g_print) {
    // Parse as the receiver would (STATE goes to the kernel, all else to the device) and compare.
    frame_dir dir = dec[0] == CELL_T_STATE ? DIR_TO_KERNEL : DIR_TO_DEVICE;
    uint8_t scratch[CELL_MAX_DECODED];
    frame_view v;
    frame_err got = frame_parse(enc, e - 1, scratch, sizeof scratch, CELL_MAX_BODY, dir, &v);
    char label[96];
    snprintf(label, sizeof label, "vector: frame %s -> %s", name, expect);
    if (strcmp(frame_err_name(got), expect) != 0) {
      printf("      (got %s)\n", frame_err_name(got));
    }
    check(strcmp(frame_err_name(got), expect) == 0, label);
    return;
  }
  printf("frame %s %s ", name, expect);
  hex(dec, n);
  printf(" ");
  hex(enc, e);
  printf("\n");
}

static void vec_cobs(const char *name, const uint8_t *raw, size_t n) {
  static uint8_t enc[HOST_MAX_ENCODED + 16];
  size_t e = cobs_encode(raw, n, enc, sizeof enc);
  if (!g_print) {
    static uint8_t back[HOST_MAX_DECODED];
    char label[64];
    snprintf(label, sizeof label, "vector: cobs %s round-trips", name);
    check(cobs_decode(enc, e, back, sizeof back) == n && (n == 0 || memcmp(back, raw, n) == 0), label);
    return;
  }
  printf("cobs %s ", name);
  hex(raw, n);
  printf(" ");
  hex(enc, e);
  printf("\n");
}

static void vectors() {
  if (!g_print) {
    const uint8_t c[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    check(crc16_ccitt_false(c, sizeof c) == 0x29B1, "vector: crc 123456789");
  }
  if (g_print) {
    printf("# RING3 work-cell link vectors (COM3). Generated by `make -C firmware vectors` from\n");
    printf("# firmware/test/test_frame.cpp; checked by `make -C firmware test`. Spec: hardware/protocol.md.\n");
    printf("# Lines (fields separated by one space, hex lowercase, '-' = empty):\n");
    printf("#   crc <input-hex> <crc16-hex>            CRC-16/CCITT-FALSE value as 4 hex digits\n");
    printf("#   cobs <name> <raw-hex> <encoded-hex>     canonical COBS, no delimiter\n");
    printf("#   frame <name> <expect> <decoded-hex> <wire-hex>\n");
    printf("#       decoded = type u8 | seq u16 LE | len u16 LE | body | crc16 LE\n");
    printf("#       wire    = COBS(decoded) followed by the 00 delimiter\n");
    printf("#       expect  = ok, or the receiver's error (crc, len, type, body_len, field, oversize)\n");
    printf("#       direction: STATE (type 81) is device->kernel; all others kernel->device\n");
    const uint8_t s[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    printf("crc ");
    hex(s, sizeof s);
    printf(" %04x\n", crc16_ccitt_false(s, sizeof s));
  }

  static uint8_t run[300];
  for (size_t i = 0; i < sizeof run; i++) {
    run[i] = (uint8_t)(1 + i % 255);
  }
  const uint8_t z1[] = {0x00};
  const uint8_t z2[] = {0x00, 0x00};
  const uint8_t mix[] = {0x11, 0x22, 0x00, 0x33};
  vec_cobs("empty", z1, 0);
  vec_cobs("zero", z1, 1);
  vec_cobs("two_zeros", z2, 2);
  vec_cobs("mixed", mix, sizeof mix);
  vec_cobs("run253", run, 253);
  vec_cobs("run254", run, 254);
  vec_cobs("run255", run, 255);

  uint8_t dec[64];
  size_t n;
  n = make_dec(CELL_T_PING, 1, 0, nullptr, 0, dec);
  vec_frame("ping", "ok", dec, n);
  const uint8_t fan40[] = {40};
  n = make_dec(CELL_T_FAN, 2, 1, fan40, 1, dec);
  vec_frame("fan_40", "ok", dec, n);
  const uint8_t fan100[] = {100};
  n = make_dec(CELL_T_FAN, 3, 1, fan100, 1, dec);
  vec_frame("fan_100_clamps_to_80", "ok", dec, n);
  const uint8_t servo120[] = {120};
  n = make_dec(CELL_T_SERVO, 4, 1, servo120, 1, dec);
  vec_frame("servo_120", "ok", dec, n);
  const uint8_t servo170[] = {170};
  n = make_dec(CELL_T_SERVO, 5, 1, servo170, 1, dec);
  vec_frame("servo_170_clamps_to_165", "ok", dec, n);
  const uint8_t lcd[] = {0, 'R', 'I', 'N', 'G', '3', ' ', 'R', 'E', 'A', 'D', 'Y'};
  n = make_dec(CELL_T_LCD, 6, sizeof lcd, lcd, sizeof lcd, dec);
  vec_frame("lcd_line0", "ok", dec, n);
  const uint8_t lcd_max[] = {1, '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'A', 'B', 'C', 'D', 'E', 'F'};
  n = make_dec(CELL_T_LCD, 7, sizeof lcd_max, lcd_max, sizeof lcd_max, dec);
  vec_frame("lcd_line1_max", "ok", dec, n);
  const uint8_t alarm1[] = {CELL_ALARM_BEEP};
  n = make_dec(CELL_T_ALARM, 8, 1, alarm1, 1, dec);
  vec_frame("alarm_beep", "ok", dec, n);
  n = make_dec(CELL_T_ESTOP_RESET, 9, 0, nullptr, 0, dec);
  vec_frame("estop_reset", "ok", dec, n);
  uint8_t st[CELL_STATE_LEN] = {0};
  put_u32le(st + CELL_ST_T_MS, 123456);
  st[CELL_ST_FAN] = 40;
  st[CELL_ST_SERVO] = 90;
  put_u16le(st + CELL_ST_DIST, 312);
  st[CELL_ST_ESTOP] = 0;
  st[CELL_ST_FLAGS] = 0;
  put_u16le(st + CELL_ST_ACK_SEQ, 2);
  n = make_dec(CELL_T_STATE, 100, CELL_STATE_LEN, st, CELL_STATE_LEN, dec);
  vec_frame("state_running", "ok", dec, n);
  put_u32le(st + CELL_ST_T_MS, 200000);
  st[CELL_ST_FAN] = 0;
  st[CELL_ST_SERVO] = 165;
  put_u16le(st + CELL_ST_DIST, CELL_DIST_NO_ECHO);
  st[CELL_ST_ESTOP] = 1;
  st[CELL_ST_FLAGS] = CELL_F_CLAMP | CELL_F_BAD_FRAME | CELL_F_ESTOP_LATCHED | CELL_F_PRESENCE_FAULT;
  put_u16le(st + CELL_ST_ACK_SEQ, 0x0102);
  n = make_dec(CELL_T_STATE, 0x0100, CELL_STATE_LEN, st, CELL_STATE_LEN, dec);
  vec_frame("state_estop_noecho", "ok", dec, n);

  n = make_dec(CELL_T_FAN, 2, 1, fan40, 1, dec);
  dec[n - 1] ^= 0x01;
  vec_frame("fan_40_bad_crc", "crc", dec, n);
  n = make_dec(CELL_T_FAN, 2, 2, fan40, 1, dec);
  vec_frame("fan_len_mismatch", "len", dec, n);
  n = make_dec(0x42, 2, 0, nullptr, 0, dec);
  vec_frame("unknown_type", "type", dec, n);
  const uint8_t two[] = {40, 41};
  n = make_dec(CELL_T_FAN, 2, 2, two, 2, dec);
  vec_frame("fan_body_2", "body_len", dec, n);
  const uint8_t al3[] = {3};
  n = make_dec(CELL_T_ALARM, 2, 1, al3, 1, dec);
  vec_frame("alarm_pattern_3", "field", dec, n);
  uint8_t lcd18[18];
  lcd18[0] = 0;
  memset(lcd18 + 1, 'A', 17);
  n = make_dec(CELL_T_LCD, 2, 18, lcd18, 18, dec);
  vec_frame("lcd_body_18", "oversize", dec, n);
}

int main(int argc, char **argv) {
  if (argc > 1 && strcmp(argv[1], "--vectors") == 0) {
    g_print = true;
    vectors();
    return 0;
  }
  test_crc();
  test_cobs();
  test_accept_all_types();
  test_reject();
  test_clamps();
  vectors();
  printf("%d/%d checks passed, %d failed\n", g_pass, g_pass + g_fail, g_fail);
  return g_fail == 0 ? 0 : 1;
}
