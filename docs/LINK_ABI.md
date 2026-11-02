# RING3 link ABI (COM2, kernel ↔ bridge), version 1

Source of truth: `abi/link_abi.h` (C, included by the kernel and `agentsh`) and its mirror
`bridge/ring3bridge/abi.py`. `tests/bridge/test_abi.py` compiles the header and fails if any `LINK_*` constant
differs in name or value, or if the object table, policy name tables or errno values differ from
`kernel/policy/`. This file explains the layouts; when it and the header disagree, the header wins and this file
is the bug. Design context: ARCHITECTURE §4–§6, D-26, D-52.

## 1. Frame
```
wire     = COBS(decoded) 0x00
decoded  = type u8 | seq u16 LE | len u16 LE | body[len] | crc16 LE
crc16    = CRC-16/CCITT-FALSE over type..body (poly 0x1021, init 0xFFFF, no reflection, xorout 0; "123456789" → 0x29B1)
```
- Same framing as the COM3 cell link (`hardware/protocol.md`); shared vectors in `firmware/test/link_vectors.txt`.
- **COBS is canonical**: exactly the encoding in the shared vectors (a full 254-byte block ends with code `0xFF`
  and no trailing empty group). A receiver rejects any other encoding of the same bytes.
- **Length consistency**: the decoded length is exactly `7 + len`; anything else is rejected.
- `seq`: each direction numbers its own frames from 0, +1 per frame, wrapping at 65536. Logged, not checked.
- Sizes: largest body 524 B (`OBSERVATION`) → largest decoded frame **531 B** → largest canonical COBS encoding
  **534 B** (COBS adds ⌈n/254⌉ to n ≥ 1 bytes) → **535 B** on the wire with the delimiter. A receiver caps its raw
  input buffer at 534 B between delimiters and drops (and resynchronises on the next `0x00`) anything longer. This
  replaces the 1024 B *(prov.)* ceiling of ARCHITECTURE §4 with an exact bound below it.

## 2. Types and exact body bounds
Types are disjoint from the COM3 cell types (`0x01`–`0x06`, `0x81`), so a frame sent on the wrong link is an
unknown type.

| Type | # | Dir | Body bytes | Body |
|---|---|---|---|---|
| `HELLO` | `0x10` | K→B | 32 | `abi_version u16, variant u8 (0 default, 1 PERMISSIVE), rsv u8, build_id[12], policy_sha256[0..16)` |
| `REQUEST` | `0x11` | K→B | 1–512 | the user's request line, printable ASCII `0x20`–`0x7E` |
| `OBSERVATION` | `0x12` | K→B | 12–524 | `nonce u32, result i32, op u8, flags u8 (bit0 truncated), rsv[2], output[0..512]` |
| `AUDIT` | `0x13` | K→B | 24 | `seq u32, tick u32, kind u8, nr u8, code u8, reason u8, target u32, result i32, tainted u8, flags u8 (bit0 permissive), rsv[2]` |
| `EVENT` | `0x14` | K→B | 28 | `struct pol_event` byte for byte: `kind u8, sub u8, flags u8, rsv u8, tick u32, nr u32, a[4] i32` |
| `HEARTBEAT` | `0x15` | K→B | 12 | `tick u32, frames_dropped u32, procs u8, rsv[3]` |
| `ACTION` | `0x90` | B→K | 16 | `op u8, flags u8, p1 i16, p2 i16, nonce u32, text_id u8, rsv[5]` |
| `TEXT` | `0x91` | B→K | 1–65 | `text_id u8 (1..255), text[0..64]` printable ASCII |
| `FINAL` | `0x92` | B→K | 1–201 | `kind u8 (1 DONE, 2 REFUSE), text[0..200]` printable ASCII |
| `STOP` | `0x93` | B→K | 1 | `reason u8` (below) |

All integers little endian. Offsets are `LINK_*_OFF_*` in the header; nothing is a C struct, so there is no
padding question on either side (read with `link_get_*`).

Field notes:
- `AUDIT`: `kind` is the policy event kind (`EV_*`), `nr` the syscall number when `kind == EV_SYSCALL` (else 0),
  `code`/`reason` are `DEC_*`/`R_*` from `kernel/policy/policy.h`. Names for display come from the mirrored
  tables in `abi.py` (`POLICY_CODE_NAMES`, …), checked against `policy.c`.
- `EVENT`: the exact input `decide()` saw, so replay can rebuild `struct pol_event` byte for byte (D-28).
- `OBSERVATION.result`: the syscall result (≥ 0) or `-errno` with the codes in `policy.h` (`EPERM` 1 … `ESAFETY`
  200). `nonce` and `op` echo the `ACTION` they answer. Output bytes are untrusted data (any byte values).
- `HELLO.build_id`: opaque 12 bytes chosen by the kernel build (T-03b); the bridge also hashes `kernel.elf` itself
  for `run_start`.

## 3. Rules
1. **Zero what you don't use.** Every `rsv` byte, every undefined flag bit, `ACTION.flags`, `ACTION.p2`, and
   `p1`/`text_id` of an op that doesn't use them are 0. A receiver rejects the frame otherwise (`agentsh` reports
   `-EINVAL` as the observation for an `ACTION`; the bridge drops and logs a K→B body).
2. **TEXT before ACTION.** An `ACTION` with `text_id != 0` refers to the `TEXT` frame with that id sent
   **immediately before** it. `agentsh` keeps only the last `TEXT`; a missing or mismatched id → `-EINVAL`.
3. **Kernel scope (INV-02).** The kernel checks framing, length, CRC, type and the per-type body bounds only, and
   forwards `ACTION`/`TEXT`/`FINAL`/`STOP` bodies to the holder of `CAP_AGENT_LINK`. It never interprets them.
4. **Schema ranges are not device limits.** `duty` 0–100, `deg` 0–180, `line` 0–1 are the JSON schema's ranges.
   The kernel's policy applies the real limits (fan 0 or 50–80 %, servo 30–150°, D-47).
5. **Deterministic nonce (D-52).** `nonce` = first 4 bytes (LE) of
   `SHA-256("ring3-nonce-v1" || seed[16] || action_index u32 LE)`, where `seed` is the per-run 16-byte seed
   recorded in `run_start` and `action_index` counts ACTIONs in the run from 0. `text_id` = `action_index % 255 + 1`
   when the op has text. Recorded replies therefore re-serialize byte for byte.

## 4. ACTION ops (complete allowlist)
| op | # | `p1` | `text_id` → TEXT | syscall |
|---|---|---|---|---|
| `cell_status` | 1 | 0 | 0 | `cell_status` |
| `fan_set` | 2 | duty 0–100 | 0 | `cell_fan` |
| `servo_set` | 3 | deg 0–180 | 0 | `cell_servo` |
| `halt` | 4 | 0 | 0 | `cell_halt` |
| `lcd_show` | 5 | line 0–1 | 1–255, text ≤ 16 B | `cell_lcd` |
| `list_objects` | 6 | 0 | 0 | `obj_list` |
| `read_object` | 7 | object id | 0 | `obj_read` |
| `write_object` | 8 | object id | 1–255, text ≤ 64 B | `obj_write` |
| `witness` | 9 | 0 | 0 | camera check (T-07 decides who answers it) |

## 5. Object table (integer IDs, D-52)
Mirrors `enum pol_object` and the `objects[]` names in `kernel/policy/policy.c` (the table `objstore.c` lists).
| id | name | trust |
|---|---|---|
| 0 | `cell_spec` | trusted |
| 1 | `maint_notes` | untrusted |
| 2–5 | `scratch_0` … `scratch_3` | untrusted |
| 6 | `audit_log` | trusted |

## 6. STOP reasons
| # | name | when |
|---|---|---|
| 1 | `invalid_replies` | two consecutive invalid model replies (INV-07/08) |
| 2 | `budget` | a reservation would pass `--budget-usd` or the project cap, or there is no price (INV-09) |
| 3 | `timeout` | the model did not answer in time |
| 4 | `model_auth` | HTTP 401/403 (INV-09) |
| 5 | `transport` | any other transport error |
| 6 | `operator` | the operator stopped the bridge |

## 7. Notes for T-03b
- `kernel/lib/cobs.c` (T-01) decodes some non-canonical encodings (e.g. a trailing `0x01` after a `0xFF` block)
  to the same bytes. The ABI requires canonical COBS; the bridge rejects non-canonical input. Whether the kernel
  must also reject it is T-03b's call (the CRC still has to match, so it is not a corruption path).
- `COBS_MAX_PAYLOAD` (1024) and `COBS_ENC_MAX` in `kernel/include/cobs.h` are looser than `LINK_MAX_DECODED` /
  `LINK_MAX_ENCODED`; `agentlink.c` should size its buffers from this header.
