# RING3 work-cell link protocol (COM3)

Kernel (`kernel/drivers/device.c`, T-04) ↔ Uno firmware (`firmware/ring3cell/`, T-06). The constants live in
`firmware/ring3cell/protocol.h` (plain C, includable from the kernel). That header and this page are the single
source of truth; if they disagree, the header wins and this page is a bug. Test vectors:
`firmware/test/link_vectors.txt`. Frame code: `firmware/ring3cell/frame.{h,cpp}`, host-tested by
`make -C firmware test`.

## Line
- UART at **115200 baud, 8N1, no flow control.** On WSL2 the Uno is `/dev/ttyACM0` (attached with usbipd). QEMU
  gives it to the guest as COM3.
- **Opening the port resets the Uno** (DTR). The firmware then boots into the **safe state** (fan off, servo
  parked at 90°, LCD `HALTED` / `no kernel link`, D-46) and starts sending `STATE`. The bootloader (optiboot 4.4 in
  `arduino:avr` 1.8.8) first waits about 1 s for an upload (its watchdog timeout) `[UNVERIFIED: measure]`, so a
  host must wait for the first valid `STATE` frame before it sends anything.

## Frame format (same as COM2, ARCHITECTURE §4)
```
wire    = COBS(decoded) 0x00
decoded = type u8 | seq u16 LE | len u16 LE | body[len] | crc16 LE
```
- **CRC-16/CCITT-FALSE** (poly `0x1021`, init `0xFFFF`, no reflection, xorout `0x0000`) over `type..body`.
  Check value: CRC(`"123456789"`) = `0x29B1`. Stored little endian.
- **COBS** in the canonical form: a block holds up to 254 non-zero bytes after its code byte. A full block
  (code `0xFF`) doesn't imply a zero, and no empty block follows it at the end of the input. Both ends must emit
  exactly this encoding (the vectors pin it down). The kernel's `kernel/lib/cobs.c` produces the same bytes for
  every vector.
- `seq` is the sender's own counter (u16, wraps). Kernel frames: any value. The device echoes the `seq` of the
  last accepted kernel frame in `STATE.ack_seq`. The device's `STATE` frames carry the device's `STATE` counter in
  `seq` (+1 per frame), so a gap shows a lost frame.

### Sizes (device side)
| | Bytes |
|---|---|
| Header + CRC (empty body) | 7 |
| Largest body (`LCD`: line + 16 chars) = `CELL_MAX_BODY` | 17 |
| Largest decoded frame = `CELL_MAX_DECODED` | 24 |
| Largest COBS-encoded frame = `CELL_MAX_ENCODED` (+1 for ≤ 254 bytes) | 25 |
| Largest frame on the wire incl. `0x00` = `CELL_MAX_WIRE` | 26 |
| `STATE` frame: decoded / encoded / on the wire | 19 / 20 / 21 |

The firmware's receive buffer holds `CELL_MAX_ENCODED` bytes. Any frame longer than that before its delimiter is
dropped whole (the bytes up to the next `0x00` are discarded) and counted as bad. Host tests use a 1024-byte body
limit (the COM2 limit) to exercise COBS runs of 253/254/255 bytes; `frame_parse` takes the limit as a parameter.

## Frame types
### Kernel → device
| Type | Name | Body (`len`) | Device action |
|---|---|---|---|
| `0x01` | `PING` | none (0) | Keeps the link watchdog fed; nothing else. |
| `0x02` | `FAN` | `duty u8` % (1) | Sets the fan PWM. Any value accepted; **clamped to 0–80 %** (`CELL_FAN_MAX`), `CLAMP` flag set if changed. Refused (fan stays 0) while the E-stop is latched or the button is pressed. With a valid clear reading (≥ 100 mm, not `0xFFFF`), clears a latched presence fault (the fan starts only if `duty > 0`). |
| `0x03` | `SERVO` | `deg u8` (1) | Moves the servo. Any value accepted; **clamped to 15–165°**, `CLAMP` flag set if changed. **Held (not moved)** while the E-stop is latched or pressed, or without a valid clear reading (second layer of the interlock); `servo_cmd` then keeps the old angle. |
| `0x04` | `LCD` | `line u8` (0 or 1), then 0–16 bytes of text, printable ASCII `0x20`–`0x7E` (1–17) | Writes that LCD line, space-padded to 16. Other line numbers or bytes: rejected (`field`). |
| `0x05` | `ALARM` | `pattern u8` (1): 0 = off, 1 = one short beep (denial), 2 = repeating (fault) | Buzzer + RGB. Other values: rejected (`field`). |
| `0x06` | `ESTOP_RESET` | none (0) | Clears the E-stop latch **only if the button has read released for ≥ 50 ms** (`CELL_ESTOP_RELEASE_MS`); otherwise the latch stays and the frame is still accepted (it feeds the link watchdog). |

### Device → kernel
`STATE` = `0x81`, every **50 ms (20 Hz)** (`CELL_STATE_PERIOD_MS`), body 12 bytes, little endian:
| Offset | Field | Type | Meaning |
|---|---|---|---|
| 0 | `t_ms` | u32 | `millis()` since device boot |
| 4 | `fan_duty` | u8 | duty actually applied, % (0 while cut by E-stop, presence or the link watchdog) |
| 5 | `servo_cmd` | u8 | angle actually commanded after clamping, degrees |
| 6 | `distance_mm` | u16 | latest presence reading, mm; `0xFFFF` = no echo (**not clear**) |
| 8 | `estop` | u8 | 1 = button pressed now (debounced), 0 = released |
| 9 | `flags` | u8 | bits below |
| 10 | `ack_seq` | u16 | `seq` of the last accepted kernel frame (0 before any) |

`STATE.flags`:
| Bit | Name | Set when | Cleared when |
|---|---|---|---|
| 0 | `WDT_RESET` | this boot followed a hardware-watchdog reset | next boot without one (sticky) |
| 1 | `CLAMP` | the last accepted `FAN` or `SERVO` value was clamped | the next accepted `FAN`/`SERVO` isn't clamped |
| 2 | `BAD_FRAME` | ≥ 1 frame rejected since the previous `STATE` | after that `STATE` is sent |
| 3 | `ESTOP_LATCHED` | E-stop pressed (INT0) | `ESTOP_RESET` with the button released ≥ 50 ms |
| 4 | `PRESENCE_FAULT` | distance < 100 mm, or no echo on 3 readings in a row, while the fan runs | the next accepted `FAN` with a valid clear reading (≥ 100 mm, not `0xFFFF`) |
| 5 | `LINK_LOST` | no accepted kernel frame for 1 s (link watchdog) | the next accepted kernel frame |
| 6–7 | reserved | never | always 0 (a receiver rejects `STATE` with these set) |

## Receiver rules (both ends)
A frame is **rejected** (the device counts it and sets `BAD_FRAME`; the kernel drops it and audits `FRAME_DROP`)
when any of these holds, checked in this order. The name is the error used in the vectors:
1. `cobs`: malformed COBS (a `0x00` inside, a code byte running past the end).
2. `oversize`: the decoded payload doesn't fit the receive buffer.
3. `short`: decoded < 7 bytes.
4. `oversize`: `len` field > the body limit (17 on the device).
5. `len`: `len` field ≠ actual body length.
6. `crc`: CRC mismatch.
7. `type`: unknown type, or a type that doesn't travel in this direction (`STATE` sent to the device, a command
   sent to the kernel).
8. `body_len`: body length wrong for the type.
9. `field`: invalid value (`LCD` line ≥ 2 or a non-printable byte, `ALARM` pattern > 2, `STATE.estop` > 1,
   reserved flag bits).

`FAN` and `SERVO` values out of range are **not** rejected: the device clamps them and reports `CLAMP`. Only
accepted frames feed the link watchdog; a rejected frame never changes device state.

## Device behaviour visible on the link (firmware layer; the kernel's own limits are tighter)
- **Safe state** at boot and after the link watchdog: fan 0 (and `fan_cmd` forgotten, so nothing restarts by
  itself), servo parked at 90°, LCD shows why.
- **Link watchdog:** 1 s (`CELL_LINK_TIMEOUT_MS`) without an accepted kernel frame → safe state + `LINK_LOST`; if
  a link had been up, 3 beeps and red blinking. Before the first kernel frame after boot the light is blue and the
  buzzer stays quiet (the flag is still set after 1 s). The kernel sends `PING` every 250 ms while healthy.
- **E-stop** (D2, INT0, normally-open button, pressed = LOW): the interrupt cuts the fan (`digitalWrite`, which
  also stops the PWM) and sets the latch; every loop pass forces the fan off again while latched or pressed. While
  latched or pressed, `fan_duty` reads 0, `FAN` commands don't start the fan and `SERVO` is held. A button held at
  power-up latches too. On a new latch: one long beep, LCD `E-STOP LATCHED` / `reset at console`.
- **Presence cutoff:** distance < 100 mm (`CELL_PRESENCE_MM`), or no echo on 3 readings in a row
  (`CELL_NO_ECHO_LIMIT`) while the fan runs or is commanded → fan 0 in the same loop pass, latched as
  `PRESENCE_FAULT`, 2 beeps. A `FAN` with duty > 0 that arrives without a valid clear reading is refused and also
  latches the fault. Readings every 50 ms, echo timeout 30 ms; ECHO has the internal pull-up, so an unplugged
  wire reads high = no echo. The kernel treats `0xFFFF` as not clear too (INV-13).
- **Stack light / buzzer** (always with LCD text): red steady = E-stop latched; red blinking = presence fault, lost
  link or `ALARM 2`; yellow (red + green) = fan running; green = linked, idle; blue = waiting for the kernel.
  `ALARM 1` = one short beep; `ALARM 2` = a beep every second until `ALARM 0` or an accepted `ESTOP_RESET`.
- **LCD:** line 1 shows a local condition (E-stop, presence, lost link, waiting) if there is one, else the
  kernel's `LCD` line 0 text, else `LINK F<duty> S<deg>`. Line 2 shows the last command (`> FAN 80 clamp`) or the
  kernel's `LCD` line 1 text, whichever came last. Lines are rewritten only when they change.
- **Hardware watchdog:** a firmware hang resets the Uno; the next boot reports `WDT_RESET`.

## Examples (from `link_vectors.txt`)
```
FAN 40, seq 2      decoded 02 0200 0100 28 8953        wire 03 02 02 02 01 04 28 89 53 00
STATE              decoded 81 6400 0c00 40e20100 28 5a 3801 00 00 0200 039c
                   = t_ms 123456, fan 40 %, servo 90°, 312 mm, released, flags 0, ack_seq 2
```
