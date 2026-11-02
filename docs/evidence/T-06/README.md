# T-06 evidence

Status 2026-11-01 21:46 ET: **A1, A2, A3, A6, A7 pass; A5 passes except the alarm outputs; A4 part 1 passes,
part 2 pending.** Pending items wait for the LCD, buzzer and RGB LED (parts arrive 2026-11-02).

| # | Acceptance (card) | Result | Evidence |
|---|---|---|---|
| A1 | Pin table, no conflicts (D2 INT0 E-stop, D3 INT1 echo, servo D9, fan PWM D5/D6, L293D IN1/IN2 + LCD RW hard-wired, D0/D1 free), D-43 two-rail power, sensor backstop, every part with wiring + check, D-41f order | **PASS** (document). 15/18 pins, fan on D5 (Timer0); layout corrected to the real build (D-47, D-48, D-49). LCD/buzzer/RGB sections are written but not yet checked against hardware | `hardware/wiring.md` |
| A2 | `make -C firmware test` passes (CRC, COBS, rejection, clamps) | **PASS**: 121 frame checks + 19 rule checks (E-stop latching, FAN drop), vectors match, `frame.cpp` clean under avr-g++ `-Werror` | `host-tests.txt` |
| A3 | Both sketches compile for `arduino:avr:uno`, no warnings from our code, RAM < 75 % | **PASS**: `ring3cell` 26 % flash / 22 % RAM, `bringup` 32 % / 15 %; 0 warnings in our files (1 core warning in `new.cpp`) | `compile.txt` |
| A4 | Bring-up transcript: every wired part responds (after the 4 safety parts, then after the rest); unwired parts listed; min spinning duty recorded | **PARTIAL.** Part 1 PASS (E-stop, sensor 264–290 mm, servo, fan; min duty **50 %**; LCD/buzzer/RGB listed as not wired) — as reported by the user, not a verbatim transcript. **Part 2 PENDING** (LCD, buzzer, RGB) | `bringup.txt` |
| A5 | `ring3cell` + `cellctl.py` hardware checks | **PASS for every item below except the alarm outputs (PENDING)** | `cellctl.txt` |
| A5.1 | `STATE` at 18–22 Hz | PASS: 19.1 Hz host / 20.0 Hz device, 0 seq gaps (21:45, final firmware) | |
| A5.2 | `FAN 100` → 80 + clamp flag; `SERVO 170` → 165 | PASS (21:23, 21:30, 21:39) | |
| A5.3 | Bad-CRC frame rejected and flagged | PASS: BAD_FRAME, fan 0, `ack_seq` unchanged with PINGs paused (21:30) | |
| A5.4 | E-stop press → fan 0 in the next `STATE`; latch holds until `ESTOP_RESET` with the button released | PASS (21:24; re-run on the fix #10 tool 21:43) | |
| A5.5 | Hand < 100 mm while the fan runs → fan 0, presence fault latched | PASS: cut in the first `STATE` reading < 100 mm (96 mm) (21:28) | |
| A5.6 | ECHO unplugged while the fan runs → fan 0 within ~150 ms, fault latched | PASS: fan 0 100 ms (device clock) after the first no-echo `STATE`, i.e. ~100–150 ms after the wire left D3 (21:41) | |
| A5.7 | E-stop during a tight `FAN` loop → fan stays 0 | PASS: 275 `FAN 60` frames, 0 `STATE` with fan > 0 after the press (21:43) | |
| A5.8 | No frames for 1 s → safe state + alarm | **Safe state PASS**: `LINK_LOST`, fan 0, servo 90 after 1093 ms; PING clears it; no auto-restart (21:23). **Alarm PENDING**: the 3 beeps + red blink + LCD `LINK LOST - STOP` need the buzzer, RGB and LCD | |
| A5+ | D-49 noise / D-50 F2 (lead additions) | PASS: `noise` → `none` (21:23); `discard_link` (21:39), `discard_estop` (21:43), `discard_presence` (21:40), all on the proven-acceptance check | |
| A6 | No `delay(`, `pulseIn(`, `tone(`, `lcd.clear(`/`lcd.home(` outside `setup()` in `firmware/ring3cell/`; `wdt_enable` + `.init3` hook present | **PASS**: only `lcd.clear()` at line 592, inside `setup()`; both present | `compile.txt` |
| A7 | Every commit has the `Builder:` trailer and touches only this card's paths | **PASS**: 28 commits, all with `Builder: Opus 5.5/high`; STATUS edits only the T-06 row. One commit outside the card's paths by explicit instruction: `f342248 docs: lead update D-43` (ARCHITECTURE, DECISIONS, INVARIANTS, T-06 card) | `git log` |

**Pending for 2026-11-02 (LCD, buzzer, RGB):** wire §6–§8 of `hardware/wiring.md` (power order: Uno 5V jumper out
first); flash `bringup`, run `a` with every part `y` → `bringup.txt` part 2 (re-checks the four safety parts on the
final D-48/D-49 wiring too); re-flash `ring3cell`, re-run `link_loss` and watch for 3 beeps, red blinking and
`LINK LOST - STOP` → closes A5.8; check the boot LCD shows `HALTED` / `no kernel link` (D-46). Also confirm on the
parts: RGB common cathode, active buzzer, LCD header soldered (`[UNVERIFIED]` in the guide).

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

## D-49 glitch item: raised, then resolved by the lead (2026-11-01)
Raised: the ISR runs a few µs (≈ 3–15 µs incl. waiting behind the timer/serial interrupts) after the edge, so a
spike still LOW then latches; "a sub-millisecond glitch doesn't latch" held only below that entry delay.
Lead's restated requirement (quoted):
> Spikes shorter than the ISR start-up delay never latch; anything still LOW when the ISR runs latches (the safe
> direction); the 100 nF filter and the noise test (must show `none`) cover realistic noise. Don't add delay to
> the latch.

The design is kept unchanged. `test_rules` checks both halves: glitches shorter than the ISR entry delay
(3/6/15 µs models) never latch, and a 500 µs glitch still LOW when the ISR runs latches (formerly printed as an
`info` line; now a check, per the lead, `dbc6471`). The realistic-noise half is `tools/cellctl.py noise` → `none`
(`cellctl.txt`, 21:23 ET).

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
| 6 | `222d26d` | `tools/cellctl.py` all fan checks | Not a defect: same reason as #4 (D-47 minimum spinning duty 50 %); a fan at 40 % never turns, so a cut wasn't visible | One constant `FAN_DUTY = 60` for every check (was 40 in `bad_crc`, `estop`, `presence`, `echo_unplug`, `estop_flood`; `LINK_LOSS_FAN` renamed) | Unchanged: every check still expects the same `STATE` values relative to the commanded duty (fan == duty while running, 0 after the cut) |
| 7 | `222d26d` | `firmware/test/test_rules.cpp` (new test, first run) | The "loop backstop alone" window assumed every poll during the 1 ms contact bounce reads HIGH; polls that land in a LOW half of the bounce rightly start the 20 ms hold, so the latch came at 30.0 ms, before my lower bound of 31 ms | Window = from 20 ms after the press starts to 20 ms after solid LOW + one poll + 1 ms (`millis()` granularity) | Unchanged rule: the loop latches after ≥ 20 ms of continuous LOW (`CELL_ESTOP_HOLD_MS`); `cell_rules.h` not modified |
| 8 | `c181aa2` | `tools/cellctl.py` `clamp`, `discard_link`, `discard_presence` | Waited for a `STATE` with `ack_seq == seq` of the frame under test, but PINGs keep flowing every 250 ms: when a PING went out right after the FAN, the next `STATE` acknowledged the PING and the exact match never fired (`discard_presence` 21:29: "clearing FAN cleared the fault=False", while the next step "a new FAN starts it=True" shows the fault had been cleared, since a still-latched fault would have dropped that FAN too) | `acked(st, seq)`: the frame or a later one was accepted (16-bit wrap) | Unchanged: same flag/duty conditions on the same `STATE`; `clamp` and `discard_link` re-run OK 21:30 |
| 9 | `c181aa2` | `tools/cellctl.py` `bad_crc` | Weak, the other way round: "ack_seq != seq" was also true if the bad frame had been accepted and a PING followed | PINGs paused for < 0.5 s around the bad frame; pass needs `ack_seq` unchanged across it | Stricter, not looser: still BAD_FRAME + fan 0, plus ack_seq must not move; re-run OK 21:30 (ack stayed 21, bad frame seq 22) |
| 10 | `638e0a5` | `tools/cellctl.py` `discard_link`, `discard_presence`, `discard_estop`, `estop` | Lead review of #8: "acknowledged this frame or a later one" is also true if the board **rejected** the FAN and then acked a PING. `discard_link`'s "link restored by the FAN" rested on that alone, and every "dropped FAN → fan stays 0" step would pass vacuously if the FAN had been rejected | `Cell.send_proven`: PINGs paused, send, wait for the `STATE` whose `ack_seq` is **exactly** that frame's seq (nothing else was sent, so it proves acceptance); the pass then requires that `STATE`'s own fields: fan 0 + `ESTOP_LATCHED` / `PRESENCE_FAULT` held, or `LINK_LOST`/`PRESENCE_FAULT` cleared with fan 0. `acked()` is left only in `clamp`, where it just times the wait (pass = fan 80 + CLAMP, servo 165 + CLAMP) | Stricter: same state conditions plus proven acceptance; `clamp` + `discard_link` re-run OK 21:39. `estop` and `discard_estop` passed (21:24, 21:26) with the older check |

Diffs: fixes 2 and 3 `git show 455002a -- tools/cellctl.py`; change 4 `git show 8c51f57 -- tools/cellctl.py`; fix 5 `git show 3c8233d -- tools/cellctl.py`; 6 and 7 `git show 222d26d -- tools/cellctl.py firmware/test/test_rules.cpp`; 8 and 9 `git show c181aa2 -- tools/cellctl.py`.

## Finding, resolved by D-49: running fan set the E-stop latch (20:41 ET, stopped and reported per the card)
With the button released (`STATE.estop = 0`), `ESTOP_LATCHED` gets set while the fan runs: 5 of 5 fan runs
(FAN 100 at start; FAN 60 after 250, 350, 450, 1350 ms), 0 trips while idle or during servo moves.
`ESTOP_RESET` clears it each time. `tools/cellctl.py noise /dev/ttyACM0` reproduces it (`cellctl.txt`).
Fail-closed direction (the fan stops), but it blocks every A5 check that runs the fan. Reading: brushed-motor noise
coupling into D2 (INT0, falling edge, internal pull-up only). Root cause (lead, D-49): the E-stop's ground was on
the MOTOR − rail. Fixed in hardware (see Hardware changes); firmware defence in depth added (Firmware changes).

## Evidence-file repair
`dbc6471` accidentally deleted this README's test-program fixes table (#1–#7) and the resolved-finding section
while replacing the open-item section (a script cut everything after the heading). Restored from `d786714` with
the intended changes kept; nothing in the table was altered.
