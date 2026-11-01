/* RING3 work-cell link protocol (COM3, kernel <-> Uno). Single source of truth for the firmware (T-06) and the
 * kernel driver (T-04). Plain C: no Arduino headers, no C++; usable from C11 and C++.
 * Spec with byte layouts and rationale: hardware/protocol.md. Wire format: docs/ARCHITECTURE.md §4. */
#ifndef RING3_CELL_PROTOCOL_H
#define RING3_CELL_PROTOCOL_H

/* ---- Line settings -------------------------------------------------------------------------------------- */
#define CELL_BAUD 115200UL /* 8N1, no flow control */

/* ---- Frame layout (decoded payload) --------------------------------------------------------------------- *
 * COBS( type u8 | seq u16 LE | len u16 LE | body[len] | crc16 LE ) 0x00
 * CRC-16/CCITT-FALSE over type..body (poly 0x1021, init 0xFFFF, no reflection, xorout 0, check 0x29B1). */
#define CELL_OFF_TYPE 0
#define CELL_OFF_SEQ 1
#define CELL_OFF_LEN 3
#define CELL_OFF_BODY 5
#define CELL_HDR_LEN 5
#define CELL_CRC_LEN 2
#define CELL_MIN_DECODED (CELL_HDR_LEN + CELL_CRC_LEN) /* 7: empty body */
#define CELL_CRC_CHECK 0x29B1u                         /* CRC of ASCII "123456789" */

/* Device-side size limits. The largest frame either side sends is LCD (1 + 16 = 17-byte body).
 * Decoded max = 5 + 17 + 2 = 24. COBS adds at most 1 byte per 254 plus 1, so encoded max = 25, and 26 on the
 * wire with the 0x00 delimiter. STATE (12-byte body): 19 decoded, 20 encoded, 21 on the wire. */
#define CELL_MAX_BODY 17
#define CELL_MAX_DECODED (CELL_MIN_DECODED + CELL_MAX_BODY) /* 24 */
#define CELL_MAX_ENCODED (CELL_MAX_DECODED + 1)             /* 25, valid while CELL_MAX_DECODED <= 254 */
#define CELL_MAX_WIRE (CELL_MAX_ENCODED + 1)                /* 26, incl. 0x00 delimiter */

/* ---- Frame types ---------------------------------------------------------------------------------------- */
/* Kernel -> device */
#define CELL_T_PING 0x01        /* body: none                                                      len 0     */
#define CELL_T_FAN 0x02         /* body: duty u8 (%), device clamps to CELL_FAN_MAX                len 1     */
#define CELL_T_SERVO 0x03       /* body: deg u8, device clamps to CELL_SERVO_MIN..CELL_SERVO_MAX     len 1     */
#define CELL_T_LCD 0x04         /* body: line u8 (0|1), text 0..16 B printable ASCII 0x20..0x7E    len 1..17 */
#define CELL_T_ALARM 0x05       /* body: pattern u8 (CELL_ALARM_*)                                 len 1     */
#define CELL_T_ESTOP_RESET 0x06 /* body: none; honoured only with the button released for 50 ms    len 0     */
/* Device -> kernel */
#define CELL_T_STATE 0x81 /* body: CELL_STATE_LEN bytes, layout below; seq = device's STATE counter */

#define CELL_FAN_LEN 1
#define CELL_SERVO_LEN 1
#define CELL_LCD_MIN_LEN 1
#define CELL_LCD_MAX_LEN 17
#define CELL_LCD_COLS 16
#define CELL_LCD_LINES 2
#define CELL_LCD_CHAR_MIN 0x20
#define CELL_LCD_CHAR_MAX 0x7E
#define CELL_ALARM_LEN 1

#define CELL_ALARM_OFF 0
#define CELL_ALARM_BEEP 1   /* one short beep (denial) */
#define CELL_ALARM_REPEAT 2 /* repeating pattern (fault) until ALARM 0 or a reset */
#define CELL_ALARM_MAX 2

/* ---- STATE body (12 bytes, little endian) --------------------------------------------------------------- */
#define CELL_STATE_LEN 12
#define CELL_ST_T_MS 0      /* u32 millis() since device boot                              */
#define CELL_ST_FAN 4       /* u8  fan duty actually applied, % (0 while cut)              */
#define CELL_ST_SERVO 5     /* u8  servo angle actually commanded after clamping, degrees  */
#define CELL_ST_DIST 6      /* u16 distance mm, CELL_DIST_NO_ECHO = no echo (not clear)    */
#define CELL_ST_ESTOP 8     /* u8  1 = E-stop button pressed now (debounced), 0 = released */
#define CELL_ST_FLAGS 9     /* u8  CELL_F_* bits                                           */
#define CELL_ST_ACK_SEQ 10  /* u16 seq of the last accepted kernel frame (0 before any)    */

#define CELL_DIST_NO_ECHO 0xFFFFu

/* STATE.flags */
#define CELL_F_WDT_RESET (1u << 0)      /* hardware watchdog reset seen at this boot (sticky until reboot) */
#define CELL_F_CLAMP (1u << 1)          /* the last accepted FAN or SERVO value was clamped               */
#define CELL_F_BAD_FRAME (1u << 2)      /* >= 1 frame rejected since the previous STATE (then cleared)    */
#define CELL_F_ESTOP_LATCHED (1u << 3)  /* E-stop latch set; cleared only by ESTOP_RESET + released 50 ms */
#define CELL_F_PRESENCE_FAULT (1u << 4) /* presence cutoff latched (< 100 mm, or 3 no-echo while running)   */
#define CELL_F_LINK_LOST (1u << 5)      /* link watchdog fired; cleared by the next accepted kernel frame  */
#define CELL_F_RESERVED_MASK 0xC0u      /* bits 6-7: always 0                                             */

/* ---- Device limits and timing (firmware layer, ARCHITECTURE §3a; the kernel's own limits are tighter) ---- */
#define CELL_SERVO_MIN 15
#define CELL_SERVO_MAX 165
#define CELL_SERVO_PARK 90      /* safe-state angle */
#define CELL_FAN_MAX 80         /* % */
#define CELL_PRESENCE_MM 100    /* distance < this = presence */
#define CELL_NO_ECHO_LIMIT 3    /* consecutive no-echo readings while the fan runs = presence fault */
#define CELL_ECHO_TIMEOUT_MS 30
#define CELL_STATE_PERIOD_MS 50 /* 20 Hz */
#define CELL_LINK_TIMEOUT_MS 1000
#define CELL_ESTOP_RELEASE_MS 50

#endif
