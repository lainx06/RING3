# T-06 evidence

In progress. The A1–A7 table is filled in when the card closes.

| File | What |
|---|---|
| `compile.txt` | A3, A6: clean `arduino-cli compile` of both sketches, flash/RAM, toolchain + library versions, A6 grep |
| `host-tests.txt` | A2: `make -C firmware test` (frame code 121 checks, E-stop / FAN rules 18 checks, vectors) |
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

## Open items
- **Glitches between the ISR entry delay and 1 ms still latch (D-49 as approved vs "a sub-millisecond glitch
  doesn't").** The ISR runs a few µs (≈ 3–15 µs incl. waiting behind the timer/serial interrupts) after the
  edge; a spike still LOW then latches. `test_rules` prints this as `info` (a 500 µs glitch latches) and doesn't
  count it as pass or fail. The 100 nF on D2 (with the ~20–50 kΩ internal pull-up `[UNVERIFIED]` value range)
  filters on the millisecond scale in hardware. Rejecting every sub-ms glitch in firmware would need the latch
  decision delayed (e.g. ISR cuts the fan, loop confirms the latch after N ms), which changes D-49; lead's call.

## Test-program fixes
Each change to a test program, with the cause, the change, and whether the expected outcome moved. None of these
changed an expectation from the card, `docs/INVARIANTS.md` or a lead decision.

| # | Commit | Test program | What was wrong with the test | Change | Expected outcome |
|---|---|---|---|---|---|
| 1 | `fb3532e` (before any run) | `tools/cellctl.py` `cobs_encode` | First draft had a tangled trailing-block rule; caught by `cellctl.py selftest` against `firmware/test/link_vectors.txt` before the tool ever touched the board | Rewrote as the canonical encoder (no empty block after a final 0xFF block), the same rule as `frame.cpp` | Unchanged: must reproduce every vector byte for byte (25/25) |
| 2 | `455002a` | `tools/cellctl.py` `open_cell` | It took the first decodable `STATE` after open as the sync point, but that was a frame sent **before** the open-reset (seq 2315, t = 115 s), and it started PINGs right away, so bytes reached the bootloader. The card (step 6) says: wait for the first valid `STATE` after the open-reset, then send | Discard `STATE` with `t_ms ≥ 3000` (pre-reset leftovers); no PING until the first fresh-boot `STATE`; error out if none within 6 s | Unchanged: no check threshold, vector or flag expectation touched; it now follows the card's open sequence exactly |
| 3 | `455002a` | `tools/cellctl.py` `open_cell` | The `undecodable` count in the `rate` result included frames cut in half by the reset before sync, so it read as a link error | Log the pre-sync count separately, then zero it | Unchanged: `rate` still passes only at 18–22 Hz; `undecodable` is informational and not part of the pass rule |
| 4 | `8c51f57` | `tools/cellctl.py` `link_loss` | Not a defect: lead request. At FAN 40 the fan sits below its 50 % minimum spinning duty (D-47), so the cut wasn't visible and 40 isn't a duty the demo would use | `link_loss` now commands `LINK_LOSS_FAN = 60` (and waits for `fan == 60`) before the silence | Unchanged: after ~1 s of silence `STATE` must show `LINK_LOST`, fan 0, servo 90, within 900–1300 ms, and PING must clear the flag without the fan restarting |
| 5 | `3c8233d` | `tools/cellctl.py` `link_loss` | Measured the silence from *after* its `wait_for(fan == 60)` loop, but PINGs kept going during that loop, so the real last frame was earlier and the measured gap came out short (886 ms on the 20:41 run) | `Cell.send` records `last_tx`; `link_loss` measures from the last frame actually written | Unchanged: `LINK_LOST` + fan 0 + servo 90 must arrive 900–1300 ms after the last frame sent |
| 6 | (this commit) | `tools/cellctl.py` all fan checks | Not a defect: same reason as #4 (D-47 minimum spinning duty 50 %); a fan at 40 % never turns, so a cut wasn't visible | One constant `FAN_DUTY = 60` for every check (was 40 in `bad_crc`, `estop`, `presence`, `echo_unplug`, `estop_flood`; `LINK_LOSS_FAN` renamed) | Unchanged: every check still expects the same `STATE` values relative to the commanded duty (fan == duty while running, 0 after the cut) |
| 7 | (this commit) | `firmware/test/test_rules.cpp` (new test, first run) | The "loop backstop alone" window assumed every poll during the 1 ms contact bounce reads HIGH; polls that land in a LOW half of the bounce rightly start the 20 ms hold, so the latch came at 30.0 ms, before my lower bound of 31 ms | Window = from 20 ms after the press starts to 20 ms after solid LOW + one poll + 1 ms (`millis()` granularity) | Unchanged rule: the loop latches after ≥ 20 ms of continuous LOW (`CELL_ESTOP_HOLD_MS`); `cell_rules.h` not modified |

Diffs: fixes 2 and 3 `git show 455002a -- tools/cellctl.py`; change 4 `git show 8c51f57 -- tools/cellctl.py`; fix 5 is in the commit that adds the `noise` command.

## Finding, resolved by D-49: running fan set the E-stop latch (20:41 ET, stopped and reported per the card)
With the button released (`STATE.estop = 0`), `ESTOP_LATCHED` gets set while the fan runs: 5 of 5 fan runs
(FAN 100 at start; FAN 60 after 250, 350, 450, 1350 ms), 0 trips while idle or during servo moves.
`ESTOP_RESET` clears it each time. `tools/cellctl.py noise /dev/ttyACM0` reproduces it (`cellctl.txt`).
Fail-closed direction (the fan stops), but it blocks every A5 check that runs the fan. Reading: brushed-motor noise
coupling into D2 (INT0, falling edge, internal pull-up only). Root cause (lead, D-49): the E-stop's ground was on
the MOTOR − rail. Fixed in hardware (see Hardware changes); firmware defence in depth added (Firmware changes).
