# T-06 evidence

In progress. The A1–A7 table is filled in when the card closes.

| File | What |
|---|---|
| `bringup.txt` | A4: bring-up part 1 (four safety parts, user-reported summary), D-48 hardware changes; part 2 pending |
| `cellctl.txt` | A5: `tools/cellctl.py` runs against `ring3cell` on the Uno |

Board: genuine Arduino UNO, `/dev/ttyACM0` via usbipd (`--auto-attach`; after a drop the user runs
`sudo chmod a+rw /dev/ttyACM0`). Bootloader per the `arduino:avr` 1.8.8 core: optiboot 4.4 (not read from the
board). Measured: port open → first `STATE` of the new boot ≈ 1.62 s.

## Test-program fixes
Each change to a test program, with the cause, the change, and whether the expected outcome moved. None of these
changed an expectation from the card or `docs/INVARIANTS.md`.

| # | Commit | Test program | What was wrong with the test | Change | Expected outcome |
|---|---|---|---|---|---|
| 1 | `fb3532e` (before any run) | `tools/cellctl.py` `cobs_encode` | First draft had a tangled trailing-block rule; caught by `cellctl.py selftest` against `firmware/test/link_vectors.txt` before the tool ever touched the board | Rewrote as the canonical encoder (no empty block after a final 0xFF block), the same rule as `frame.cpp` | Unchanged: must reproduce every vector byte for byte (25/25) |
| 2 | `455002a` | `tools/cellctl.py` `open_cell` | It took the first decodable `STATE` after open as the sync point, but that was a frame sent **before** the open-reset (seq 2315, t = 115 s), and it started PINGs right away, so bytes reached the bootloader. The card (step 6) says: wait for the first valid `STATE` after the open-reset, then send | Discard `STATE` with `t_ms ≥ 3000` (pre-reset leftovers); no PING until the first fresh-boot `STATE`; error out if none within 6 s | Unchanged: no check threshold, vector or flag expectation touched; it now follows the card's open sequence exactly |
| 3 | `455002a` | `tools/cellctl.py` `open_cell` | The `undecodable` count in the `rate` result included frames cut in half by the reset before sync, so it read as a link error | Log the pre-sync count separately, then zero it | Unchanged: `rate` still passes only at 18–22 Hz; `undecodable` is informational and not part of the pass rule |
| 4 | `8c51f57` | `tools/cellctl.py` `link_loss` | Not a defect: lead request. At FAN 40 the fan sits below its 50 % minimum spinning duty (D-47), so the cut wasn't visible and 40 isn't a duty the demo would use | `link_loss` now commands `LINK_LOSS_FAN = 60` (and waits for `fan == 60`) before the silence | Unchanged: after ~1 s of silence `STATE` must show `LINK_LOST`, fan 0, servo 90, within 900–1300 ms, and PING must clear the flag without the fan restarting |
| 5 | `3c8233d` | `tools/cellctl.py` `link_loss` | Measured the silence from *after* its `wait_for(fan == 60)` loop, but PINGs kept going during that loop, so the real last frame was earlier and the measured gap came out short (886 ms on the 20:41 run) | `Cell.send` records `last_tx`; `link_loss` measures from the last frame actually written | Unchanged: `LINK_LOST` + fan 0 + servo 90 must arrive 900–1300 ms after the last frame sent |

Diffs: fixes 2 and 3 `git show 455002a -- tools/cellctl.py`; change 4 `git show 8c51f57 -- tools/cellctl.py`; fix 5 is in the commit that adds the `noise` command.

## Open finding: running fan sets the E-stop latch (20:41 ET, stopped and reported per the card)
With the button released (`STATE.estop = 0`), `ESTOP_LATCHED` gets set while the fan runs: 5 of 5 fan runs
(FAN 100 at start; FAN 60 after 250, 350, 450, 1350 ms), 0 trips while idle or during servo moves.
`ESTOP_RESET` clears it each time. `tools/cellctl.py noise /dev/ttyACM0` reproduces it (`cellctl.txt`).
Fail-closed direction (the fan stops), but it blocks every A5 check that runs the fan. Reading: brushed-motor noise
coupling into D2 (INT0, falling edge, internal pull-up only); not a firmware change until the lead decides.
