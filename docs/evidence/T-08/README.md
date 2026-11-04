# T-08 evidence: kernel console screen (VGA text) + styled bridge timeline

Builder: Opus 5.5 / high, D-57 auto mode, 2026-11-03. Offline: no paid call, no hardware, no flashing.
Commits: `0ede67c` (card + design spec as the lead wrote them), `891e6ff` (kernel screen, steps 1-4, 6, 7),
`9d49b42` (step 5, `--style console`).

## What was built
- **Pure renderer** `kernel/ui/screen.c`: `screen_render(const struct screen_model *, uint16_t cells[25][80])`,
  no I/O, no globals, the time is a snapshot field. Built into the kernel and, unchanged, into
  `build/host/screen_test` (`make host-screen`), like `decide()`.
- **One vocabulary** `kernel/ui/words.c`: verdict words (ALLOW, DENIED, ASK, CONFIRMED, DECLINED, AUTO HALT,
  BOARD BOOT) and reason phrases. Moved out of the LCD driver; the panel (`device_show`) and the screen both
  use it (correction 4).
- **VGA driver** `kernel/drivers/vga.c`: off unless the kernel is booted with the `screen` option. Back
  buffer, writes only cells that changed, at most once per tick. It runs from the kernel's wait loops (through
  `audit_flush`) or from a tick that interrupted ring 3 (the kernel's own context is parked then). Blink off,
  cursor off. The snapshot is filled under `irq_save` from read-only copies: `audit_state()` (taint, pending,
  latch, audit seq), the audited decisions (noted in `audit_feed` after `decide()`), `device_last_frame()`
  (new accessor: copy of the last accepted STATE), `pmm_free_frames()`, `exec_last_pid()` (new accessor), and
  COM1 output (mirrored in `serial_putc`; the bytes on COM1 are unchanged).
- **Make targets:** `make run-cell-screen` (same three serial ports as `run-cell`, GTK window via WSLg,
  `-append screen`, monitor socket `build/screen-mon.sock`); `SCREEN_DISPLAY=curses` puts the screen in the
  terminal and COM1 on `tcp:127.0.0.1:4556` (`nc 127.0.0.1 4556`). `make screendump NAME=<x>` writes
  `docs/evidence/T-08/<x>.png` + `<x>.txt` (`tools/screendump.py`, monitor `screendump` + `pmemsave`).
- **Console command** `screen`: render-time figures (TSC calibrated against the PIT over the first 0.5 s, so
  boot isn't delayed).
- **Bridge** `bridge/ring3bridge/style.py` + a 10-line hook in `cli.py`: `ring3 run --style console`. The
  run gets the plain printer itself unless stdout is a TTY.

Not touched (`git diff --stat 0ede67c..9d49b42`): `kernel/policy/`, `syscall.c`, `uaccess.c`, `cellframe.c`,
`agentlink.c`, `confirm.c`, the event log, ledger, strict JSON, `run.py`. No new dependency.

## Acceptance
| # | Result | Evidence |
|---|---|---|
| A1 | **pass**: build has zero warnings (`-Werror`; production, TEST, KTEST, permissive and host images); full default suite in a clean worktree at `9d49b42`: **770 passed, 6 skipped** (hw + 2 by design), 160 s; ruff check + format clean; host goldens for idle / ask / estop pass | `build.txt`, `pytest.txt`, `tests/test_t08_screen.py::test_render_golden_text` |
| A2 | **pass**: `test_t08_vga_buffer` boots the test image with `screen` and the simulated cell, runs `cellcmd status; servo 120; fan 90`, reads 0xB8000 through the QEMU monitor (`pmemsave`). It finds the status bar (`UP .. EXEC IDLE .. FREE .. KiB .. LOGGED`), the console window (`cellcmd:` lines, prompt), banner `DENIED` in white on dark red, and ticker rows `ALLOW cell_servo(120)` / `DENIED cell_fan(90): fan max 80%` | `tests/test_t08_screen.py` |
| A3 | **pass**: INV-11's scanner covers `kernel/**/*.c` + `bridge/**/*.py` (new files included); `test_scanner_sees_strings` now points at `kernel/ui/words.c` + `screen.c`; new `test_screen_sources_wording` / `test_rendered_text_wording` check the new sources and every rendered state for banned words and franchise or military wording (D-66a) | `tests/test_inv_11_banned_words.py`, `tests/test_t08_screen.py` |
| A4 | **pass**: whole service pass (snapshot + render + diff + VGA writes), measured in the guest under QEMU TCG: **avg 26-34 µs, max 400-508 µs per tick, 0 passes ≥ 1 ms** over a command burst. The max is the console scroll (≈630 changed cells; each VGA write is an emulated MMIO store). No `dropped` and no `PANIC` in any run (every QEMU test checks both). | `render-time.txt` |
| A5 | **pass**: non-TTY output of `--style console` is the plain printer itself, byte for byte on T-04 run 4's timeline (`test_console_style_to_a_non_tty_is_the_plain_printer`, pipe + file, dry run unchanged). Styled TTY mode is recorded with `script` (real run-4 lines replayed through `StyledTimeline` 0.4 s apart): `scriptreplay -t styled-timeline.timing styled-timeline.typescript` in a terminal ≥ 200 columns | `tests/test_t08_style.py`, `styled-timeline.*` |
| A6 | **pass** (D-81): three states as PNG + exact text from a **simulated-cell** run (`sim-idle`, `sim-ask` tainted ASK, `sim-estop` AUTO HALT with the executor still RUNNING) and from the user's **live run `20261103-214504`** on the real cell (build `747fa2d`, $0.50 budget): `real-ask` the tainted `cell_fan(60)` confirmation, `real-estop` AUTO HALT with the E-stop latched and the fan at 0, `real-idle` the two-phase `estop reset` with CONFIRM_YES; request DONE after 3 actions. Seen on the real shots: the console `estop_reset` CONFIRM line is 83 columns and wraps; DIST reads 116 mm (something in the beam, D-81). Real-run files committed in T-11. Commits contain only this card's paths, `Builder:` trailer on each | `sim-*.png`, `sim-*.txt`, `real-*.png`, `real-*.txt` |

## Corrections (card) as built
1. Calls are shown by the kernel's own names (`policy_syscall_name`): `cell_status`, `cell_fan(n)`,
   `cell_servo(n)`, `cell_halt`, `cell_lcd(line)`, `obj_read(name)`, `obj_write(name)`, `estop reset`. No
   `cell_dist`. Console writes, link calls and `uptime` are left out of the ticker unless refused.
2. Golden fan values are 0, 60 and 100 (the denied over-limit try); no 30.
3. `EXEC KILLED` only after a real `LIMIT` / `PROC_FAULT` decision (held until the next request starts). The
   E-stop golden and the sim E-stop shot show `EXEC RUNNING`.
4. One table (`words.c`) for panel and screen. CONFIRMED / DECLINED did not exist in the LCD's table (it
   showed ALLOW / DENIED for confirm answers), so **the panel now says `CONFIRMED reset`, `CONFIRMED fan 60`,
   `DECLINED ...`**. Every other LCD text is unchanged. If verdict + call don't fit 16 columns
   (`CONFIRMED servo 120`), row 0 shows the verdict alone and row 1 the call, so a number is never cut.
5. Servo dial 0-180° with `|` at the kernel's 30 and 150 limits (`SERVO_MIN_DEG`/`SERVO_MAX_DEG`); distance
   bar 0-400 mm (longer = full), `NO ECHO` in words with an empty bar.
6. Only existing state: uptime (ticks), executor (`in_request`, last pid, kill decisions), free KiB
   (allocator), LOGGED (audit seq), taint, pending, latch (kernel or the board's flag), last frame's fan,
   servo, distance, button, flags, frame age (10 ms steps at 100 Hz). **LINK** = `ok` / `STALE` (no frame for
   more than 500 ms, `STALE_TICKS`) / `LOST (board watchdog)` (STATE flag) / `waiting` / `no cell`.
   **PRESENCE** is the reported distance against the same 100 mm constant (`PRESENCE_MM`), display only.
   Without telemetry every reading shows `-`.
7. The timeline keeps the bridge's host time column.

## Test changes (memory rule: cause, diff, expectation)
| Test | Cause | Change | Expected outcome |
|---|---|---|---|
| `tests/test_inv_13_estop_latch.py:64` | LCD verdict for a confirmed answer now comes from the shared table (correction 4) | `{0: "ALLOW reset", ...}` -> `{0: "CONFIRMED reset", 1: "operator: yes"}` | same decision (`CONFIRM_YES estop_reset`), ESTOP_RESET still sent only after `y`; only the panel word changed |
| `tests/test_inv_11_banned_words.py::test_scanner_sees_strings` | the phrases moved from `device.c` to `kernel/ui/words.c` | points at `words.c` (+ checks `CONFIRMED` and two `screen.c` literals are in reach) | scanner and banned list unchanged |
| `tests/qemu.py` | T-08 needs a monitor socket | optional `monitor=` (default `"none"`, so every other test's command is identical) | n/a (addition) |

Fixes to my own new tests while writing them (no kernel expectation involved): the `status` result filter
(the label carries the decoded fields), `servo 120; servo 90` needed `wait 25` (the kernel correctly refused
the second move as `servo: too soon`), the prompt row on test images can be row 12 or 13 (see below), and the
render figures start after the 0.5 s calibration.

## Findings
- **Test images**: COM1 starts a new line when tape/AUDIT records print after `ring3> ` (existing line
  discipline), so the window can show the prompt one row up with an empty last row. The window hides
  `TAPE`/`AUDIT` lines on test images only (`#ifdef RING3_TEST`); production images never print them.
- WSLg GTK window opens (`-display gtk`, no errors; screendump OK); I didn't see the window myself.
  `SCREEN_DISPLAY=curses` **[UNVERIFIED]**: built, not run.
- The `sim-*` shots show test-image lines (`RUN`, `SYSCALL`); a production `make run-cell-screen` shows agentsh's
  lines instead.

## For the user (real-run screenshots, A6)
Three terminals, bridge first (D-70). Check the reservation with `ring3 run ... --dry-run` first (free).
```
uv run ring3 run --budget-usd 0.50 --style console          # terminal 1 (paid: yours)
make run-cell-screen                                         # terminal 2: QEMU window + COM1 console
make screendump NAME=real-idle                               # terminal 3, after a finished request
make screendump NAME=real-ask                                # while a tainted CONFIRM waits (before y/N)
make screendump NAME=real-estop                              # after pressing the E-stop with the fan running
```
A request that reaches all three: `read the operator notes, then set the fan to 60 %` (tainted ASK ->
screendump -> `y` -> fan at 60 -> press the E-stop -> screendump), then release, `estop reset`, and a short
request for the idle shot. The files land in `docs/evidence/T-08/real-*.png` + `.txt`; I'll file them.
