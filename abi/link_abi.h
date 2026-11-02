/* RING3 kernel <-> bridge link ABI (COM2). Single source for both sides: the kernel and agentsh include this
 * header; the bridge mirrors every constant in bridge/ring3bridge/abi.py, and tests/bridge/test_abi.py compiles
 * this header and fails if any value differs. Prose, layouts and rules: docs/LINK_ABI.md.
 *
 * Freestanding C11, <stdint.h> only. Multi-byte fields are little endian at the byte offsets below; read them
 * with the LINK_GET_* helpers (no structs, so no padding or alignment questions on either side).
 *
 * Rules every sender and receiver follows (docs/LINK_ABI.md §3):
 *   - a frame's decoded length is exactly LINK_MIN_DECODED + len, and len is inside its type's bounds;
 *   - every reserved byte and every field an op does not use is zero; a receiver rejects the frame otherwise;
 *   - a TEXT frame is always sent before the ACTION that references it (text_id != 0);
 *   - the kernel checks framing, length, CRC and type only; ACTION bodies are parsed in ring 3 (INV-02). */
#ifndef RING3_LINK_ABI_H
#define RING3_LINK_ABI_H
#include <stdint.h>

#define LINK_ABI_VERSION 1

/* ---- frame layout (decoded payload) ----------------------------------------------------------------------- *
 * COBS( type u8 | seq u16 LE | len u16 LE | body[len] | crc16 LE ) 0x00
 * CRC-16/CCITT-FALSE over type..body: poly 0x1021, init 0xFFFF, no reflection, xorout 0, check 0x29B1.
 * seq: each direction numbers its own frames from 0, +1 per frame, wrapping at 65536 (logged, not checked). */
#define LINK_OFF_TYPE 0
#define LINK_OFF_SEQ 1
#define LINK_OFF_LEN 3
#define LINK_OFF_BODY 5
#define LINK_HDR_LEN 5
#define LINK_CRC_LEN 2
#define LINK_MIN_DECODED 7 /* header + CRC, empty body */
#define LINK_CRC_POLY 0x1021
#define LINK_CRC_INIT 0xFFFF
#define LINK_CRC_CHECK 0x29B1 /* CRC of ASCII "123456789" */
#define LINK_DELIM 0x00

/* ---- frame types ------------------------------------------------------------------------------------------ *
 * Disjoint from the COM3 cell types (0x01-0x06, 0x81), so a frame crossed between links is an unknown type. */
/* kernel -> bridge */
#define LINK_T_HELLO 0x10
#define LINK_T_REQUEST 0x11
#define LINK_T_OBSERVATION 0x12
#define LINK_T_AUDIT 0x13
#define LINK_T_EVENT 0x14
#define LINK_T_HEARTBEAT 0x15
/* bridge -> kernel */
#define LINK_T_ACTION 0x90
#define LINK_T_TEXT 0x91
#define LINK_T_FINAL 0x92
#define LINK_T_STOP 0x93

/* ---- exact body-length bounds per type (min..max inclusive; MIN == MAX means fixed size) ---------------- */
#define LINK_HELLO_LEN 32
#define LINK_REQUEST_MIN 1
#define LINK_REQUEST_MAX 512
#define LINK_OBS_HDR_LEN 12
#define LINK_OBS_OUT_MAX 512
#define LINK_OBS_MIN LINK_OBS_HDR_LEN
#define LINK_OBS_MAX (LINK_OBS_HDR_LEN + LINK_OBS_OUT_MAX) /* 524 */
#define LINK_AUDIT_LEN 24
#define LINK_EVENT_LEN 28
#define LINK_HEARTBEAT_LEN 12
#define LINK_ACTION_LEN 16
#define LINK_TEXT_DATA_MAX 64
#define LINK_TEXT_MIN 1                           /* text_id only: an empty text */
#define LINK_TEXT_MAX (1 + LINK_TEXT_DATA_MAX)    /* 65 */
#define LINK_FINAL_TEXT_MAX 200
#define LINK_FINAL_MIN 1
#define LINK_FINAL_MAX (1 + LINK_FINAL_TEXT_MAX)  /* 201 */
#define LINK_STOP_LEN 1

/* Largest body of any type (OBSERVATION), largest decoded frame, and largest COBS encoding of it.
 * COBS (canonical, as in firmware/test/link_vectors.txt) adds ceil(n / 254) bytes to n >= 1 input bytes:
 * 531 + 3 = 534. On the wire add the 0x00 delimiter. A receiver caps raw input at LINK_MAX_ENCODED. */
#define LINK_MAX_BODY LINK_OBS_MAX                            /* 524 */
#define LINK_MAX_DECODED (LINK_MIN_DECODED + LINK_MAX_BODY)   /* 531 */
#define LINK_MAX_ENCODED 534
#define LINK_MAX_WIRE (LINK_MAX_ENCODED + 1)                  /* 535 */

/* ---- HELLO (K->B, 32 B) ---------------------------------------------------------------------------------- */
#define LINK_HELLO_OFF_ABI 0        /* u16 LINK_ABI_VERSION                                    */
#define LINK_HELLO_OFF_VARIANT 2    /* u8  LINK_VARIANT_*                                      */
#define LINK_HELLO_OFF_RSV 3        /* u8  0                                                   */
#define LINK_HELLO_OFF_BUILD 4      /* u8[12] kernel build id, opaque bytes (set by the build) */
#define LINK_HELLO_BUILD_LEN 12
#define LINK_HELLO_OFF_POLICY 16    /* u8[16] first 16 bytes of the policy SHA-256             */
#define LINK_HELLO_POLICY_LEN 16
#define LINK_VARIANT_DEFAULT 0
#define LINK_VARIANT_PERMISSIVE 1   /* "PERMISSIVE (EVAL ONLY)" build */

/* ---- REQUEST (K->B, 1..512 B): the user's request line, printable ASCII 0x20..0x7E, no NUL -------------- */

/* ---- OBSERVATION (K->B, 12..524 B): the result of the last ACTION ---------------------------------------- */
#define LINK_OBS_OFF_NONCE 0        /* u32 the ACTION's nonce, echoed                          */
#define LINK_OBS_OFF_RESULT 4       /* i32 syscall result (>= 0) or -errno (policy.h codes)    */
#define LINK_OBS_OFF_OP 8           /* u8  the ACTION's op (LINK_OP_*), echoed                 */
#define LINK_OBS_OFF_FLAGS 9        /* u8  LINK_OBSF_*                                         */
#define LINK_OBS_OFF_RSV 10         /* u8[2] 0                                                 */
#define LINK_OBS_OFF_OUT 12         /* u8[0..512] output bytes (untrusted data)                */
#define LINK_OBSF_TRUNCATED 0x01    /* the output was longer than LINK_OBS_OUT_MAX              */
#define LINK_OBSF_MASK 0x01

/* ---- AUDIT (K->B, 24 B): one audit record {seq, tick, syscall, target, decision, reason, result, tainted} - */
#define LINK_AUD_OFF_SEQ 0          /* u32 audit sequence                                      */
#define LINK_AUD_OFF_TICK 4         /* u32 tick of the event                                   */
#define LINK_AUD_OFF_KIND 8         /* u8  policy event kind (EV_*)                            */
#define LINK_AUD_OFF_NR 9           /* u8  syscall number (SYS_*) when kind is EV_SYSCALL, else 0 */
#define LINK_AUD_OFF_CODE 10        /* u8  decision (DEC_*)                                    */
#define LINK_AUD_OFF_REASON 11      /* u8  reason (R_*)                                        */
#define LINK_AUD_OFF_TARGET 12      /* u32 target (object id, duty, degrees, prog, vector)     */
#define LINK_AUD_OFF_RESULT 16      /* i32 result                                              */
#define LINK_AUD_OFF_TAINTED 20     /* u8  0 or 1                                              */
#define LINK_AUD_OFF_FLAGS 21       /* u8  LINK_AUDF_*                                         */
#define LINK_AUD_OFF_RSV 22         /* u8[2] 0                                                 */
#define LINK_AUDF_PERMISSIVE 0x01
#define LINK_AUDF_MASK 0x01

/* ---- EVENT (K->B, 28 B): one inbound policy event, byte for byte struct pol_event (policy.h) ------------ */
#define LINK_EV_OFF_KIND 0          /* u8  EV_*                                                */
#define LINK_EV_OFF_SUB 1           /* u8                                                      */
#define LINK_EV_OFF_FLAGS 2         /* u8  EVF_*                                               */
#define LINK_EV_OFF_RSV 3           /* u8  0 (pol_event.pad)                                   */
#define LINK_EV_OFF_TICK 4          /* u32                                                     */
#define LINK_EV_OFF_NR 8            /* u32                                                     */
#define LINK_EV_OFF_A0 12           /* i32 a[0..3] at 12, 16, 20, 24                           */
#define LINK_EV_NARGS 4

/* ---- HEARTBEAT (K->B, 12 B) ------------------------------------------------------------------------------- */
#define LINK_HB_OFF_TICK 0          /* u32 kernel tick                                         */
#define LINK_HB_OFF_DROPPED 4       /* u32 COM2 frames dropped since boot                      */
#define LINK_HB_OFF_PROCS 8         /* u8  executors alive (0 or 1, D-26)                      */
#define LINK_HB_OFF_RSV 9           /* u8[3] 0                                                 */

/* ---- ACTION (B->K, exactly 16 B; D-26) -------------------------------------------------------------------- *
 * op u8 | flags u8 | p1 i16 | p2 i16 | nonce u32 | text_id u8 | rsv[5] */
#define LINK_ACT_OFF_OP 0
#define LINK_ACT_OFF_FLAGS 1        /* no flags defined: always 0                              */
#define LINK_ACT_OFF_P1 2
#define LINK_ACT_OFF_P2 4           /* no op uses p2: always 0                                 */
#define LINK_ACT_OFF_NONCE 6
#define LINK_ACT_OFF_TEXT_ID 10     /* 0 = no text; 1..255 = the id of the TEXT sent just before */
#define LINK_ACT_OFF_RSV 11
#define LINK_ACT_RSV_LEN 5

/* Op numbers: the complete allowlist (ARCHITECTURE §3a). Fields each op uses; everything else must be 0.
 *   op                p1                        text_id                          syscall       */
#define LINK_OP_CELL_STATUS 1   /* -                         -                                cell_status   */
#define LINK_OP_FAN_SET 2       /* duty 0..100               -                                cell_fan      */
#define LINK_OP_SERVO_SET 3     /* deg 0..180                -                                cell_servo    */
#define LINK_OP_HALT 4          /* -                         -                                cell_halt     */
#define LINK_OP_LCD_SHOW 5      /* line 0..1                 1..255, text <= 16 B             cell_lcd      */
#define LINK_OP_LIST_OBJECTS 6  /* -                         -                                obj_list      */
#define LINK_OP_READ_OBJECT 7   /* object id 0..OBJ_COUNT-1  -                                obj_read      */
#define LINK_OP_WRITE_OBJECT 8  /* object id 0..OBJ_COUNT-1  1..255, text <= 64 B             obj_write     */
#define LINK_OP_WITNESS 9       /* -                         -                    (camera check, T-07)      */
#define LINK_OP_MIN 1
#define LINK_OP_MAX 9

/* Schema ranges (the bridge's JSON schema, ARCHITECTURE §6). Not device limits: the kernel applies those. */
#define LINK_DUTY_MAX 100
#define LINK_DEG_MAX 180
#define LINK_LCD_LINE_MAX 1
#define LINK_LCD_TEXT_MAX 16
#define LINK_OBJ_TEXT_MAX 64
#define LINK_TEXT_CHAR_MIN 0x20     /* TEXT, FINAL and REQUEST text: printable ASCII only */
#define LINK_TEXT_CHAR_MAX 0x7E

/* ---- object table (integer IDs; mirrors enum pol_object and the names in kernel/policy/policy.c) -------- *
 *   0 cell_spec  1 maint_notes  2 scratch_0  3 scratch_1  4 scratch_2  5 scratch_3  6 audit_log */
#define LINK_OBJ_CELL_SPEC 0
#define LINK_OBJ_MAINT_NOTES 1
#define LINK_OBJ_SCRATCH_0 2
#define LINK_OBJ_SCRATCH_1 3
#define LINK_OBJ_SCRATCH_2 4
#define LINK_OBJ_SCRATCH_3 5
#define LINK_OBJ_AUDIT_LOG 6
#define LINK_OBJ_COUNT 7

/* ---- TEXT (B->K, 1..65 B): text_id u8 (1..255) | text[0..64] printable ASCII ----------------------------- */
#define LINK_TEXT_OFF_ID 0
#define LINK_TEXT_OFF_DATA 1

/* ---- FINAL (B->K, 1..201 B): kind u8 | text[0..200] printable ASCII ------------------------------------- */
#define LINK_FINAL_OFF_KIND 0
#define LINK_FINAL_OFF_TEXT 1
#define LINK_FINAL_DONE 1
#define LINK_FINAL_REFUSE 2

/* ---- STOP (B->K, 1 B): reason u8 ------------------------------------------------------------------------- */
#define LINK_STOP_OFF_REASON 0
#define LINK_STOP_INVALID_REPLIES 1 /* two consecutive invalid model replies (INV-07, INV-08)   */
#define LINK_STOP_BUDGET 2          /* the reservation would exceed --budget-usd or the cap, or no price (INV-09) */
#define LINK_STOP_TIMEOUT 3         /* the model did not answer in time                        */
#define LINK_STOP_MODEL_AUTH 4      /* HTTP 401 / 403 from the model API (INV-09)              */
#define LINK_STOP_TRANSPORT 5       /* any other transport error                               */
#define LINK_STOP_OPERATOR 6        /* the operator stopped the bridge                         */
#define LINK_STOP_MIN 1
#define LINK_STOP_MAX 6

/* ---- little-endian field helpers (byte pointers; no alignment assumptions) ------------------------------- */
static inline uint16_t link_get_u16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t link_get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline int16_t link_get_i16(const uint8_t *p) { return (int16_t)link_get_u16(p); }
static inline int32_t link_get_i32(const uint8_t *p) { return (int32_t)link_get_u32(p); }
static inline void link_put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}
static inline void link_put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

#endif
