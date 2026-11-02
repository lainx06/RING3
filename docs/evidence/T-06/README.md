# T-06 evidence

In progress. The A1–A7 table is filled in when the card closes.

| File | What |
|---|---|
| `compile.txt` | A3, A6: clean `arduino-cli compile` of both sketches, flash/RAM, toolchain + library versions, A6 grep |
| `host-tests.txt` | A2: `make -C firmware test` (frame code 121 checks, E-stop / FAN rules 19 checks, vectors) |
| `bringup.txt` | A4: bring-up part 1 (four safety parts, user-reported summary), D-48 hardware changes; part 2 pending |
| `cellctl.txt` | A5: `tools/cellctl.py` runs against `ring3cell` on the Uno |

Board: genuine Arduino UNO, `/dev/ttyACM0` via usbipd (`--auto-attach`; after a drop the user runs
`sudo chmod a+rw /dev/ttyACM0`). Bootloader per the `arduino:avr` 1.8.8 core: optiboot 4.4 (not read from the
board). Measured: port open → first `STATE` of the new boot ≈ 1.62 s.

**`WDT_RESET` is not claimed (D-50 F6).** The flag exists in `STATE.flags`, but on this board it can't be observed:
optiboot 4.4 clears `MCUSR` before the sketch runs and doesn't pass it in `r2`, so the flag reads 0 even after our
own watchdog reset (and a bootloader that did pass it would report one after every port open, because optiboot
leaves through its own watchdog). Observed `WDT_RESET=False` on every boot. What the evidence does support: the
hardware watchdog is enabled (`wdt_enable(WDTO_250MS)`, `compile.txt`) and every boot comes up in the safe state.
Details: `hardware/protocol.md`.

## Hardware changes
| Decision | Change | Why |
|---|---|---|
| D-47 | 9 V DC adapter into the module's barrel jack; MOTOR = row-a side, LOGIC = row-j side | lead's build |
| D-48a | 10 kΩ pull-down, L293D pin 1 (EN) → GND | fan blipped while the Uno reset (EN floating) |
| D-48b | Power order: module on whenever the Uno feeds LOGIC | L293D back-feeds an unpowered MOTOR rail |
| D-49 | E-stop ground moved MOTOR − → LOGIC −; 100 nF D2 → LOGIC −; 100 nF across the motor leads; motor leads twisted, kept away from the E-stop wires. L293D 4, 5, 7 on MOTOR − (guide corrected to the real layout) | running fan set the E-stop latch, 5/5 runs; after the fix `cellctl noise`: `none` (user, FAN 60 x2 + servo) |

## Firmware changes after the first flash (all in `ring3cell`, host-tested in `firmware/test/test_rules.cpp`)
| Decision | Change | Test |
|---|---|---|
| D-49 | INT0 ISR latches (and cuts the fan) only if D2 still reads LOW when it runs; loop latches on D2 LOW ≥ 20 ms (`cell_rules.h`) | `test_rules`: real press latches via ISR; via loop alone with the ISR disabled; 30 ms tap latches; glitches shorter than the ISR entry delay (3/6/15 µs models) and a 40-spike burst don't; loop ignores 0.5/5/19 ms LOW |
| D-50 F2 | `FAN` during any latch (E-stop latched or pressed, presence fault, `LINK_LOST`) is dropped; clearing a latch leaves the fan at 0 until a new `FAN` (`fan_rx_decide`). Before this, a `FAN` sent while E-stop-latched was kept and the fan restarted on `ESTOP_RESET` | `test_rules` 12-row `fan_rx` table; hardware: `cellctl` `discard_link`, `discard_estop`, `discard_presence` |
| D-50 F10 | `frame_parse` checks the second `cobs_decode` return (defensive; same input as the measuring pass, so not reachable by a test) | `test_frame` boundary cases: scratch_cap and scratch_cap + 1, max_body and max_body + 1, host 1024 / 1025 |
| D-46 | Boot LCD `HALTED` / `no kernel link`; no "safe" in display or serial strings | `compile.txt` grep |

## D-49 requirement as restated by the lead (2026-11-01, after the open item was raised)
> Spikes shorter than the ISR start-up delay never latch; anything still LOW when the ISR runs latches (the safe
> direction); the 100 nF filter and the noise test (must show `none`) cover realistic noise. Don't add delay to
> the latch.

The design is kept unchanged. `test_rules` checks both halves: glitches shorter than the ISR entry delay
(3/6/15 µs models) never latch, and a 500 µs glitch still LOW when the ISR runs latches (formerly printed as an
`info` line; now a check, per the lead). The realistic-noise half is `tools/cellctl.py noise` → `none`
(`cellctl.txt`).
