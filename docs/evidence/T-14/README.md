# T-14 evidence: VGA cell panel (pictures of the work cell, text mode)

Builder: Opus 5.5 / high, 2026-11-04, worktree `~/dev/RING3-t14`, branch `t14`. Offline: no paid call, no hardware,
no flashing.

## Mockups (written before coding)
80x25 text mode as in T-08 (D-66a), CP437 only. Rows 1-5 (status bar, banner) and 20-25 (decision ticker) are
T-08's, unchanged. The panel replaces the LIVE CELL rows and takes 2 rows from the console window, which keeps
5 rows (rows 7-11). Panel: rule at row 12, pictures at rows 13-19, in three columns split by `│` (0xB3) at
columns 20 and 44.

Full screen, idle (fan 0, gauge 90, 320 mm, clear):
```
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:02:14   EXEC IDLE                 FREE 14920 KiB   LOGGED 41           ║
║ LAST KERNEL DECISION: ALLOW                              TAINTED: NO         ║
║                                    ALLOW                                     ║
║ cell_fan(0)                                                                  ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ agentsh: action 3 fan_set(0) -> 0                                            ║
║ OUTCOME DONE: gauge at 90, fan off                                           ║
║ EXECUTED 3 operation(s):                                                     ║
║ END pid=6 EXIT ticks=388 uptime=13300                                        ║
║ ring3>                                                                       ║
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 320 mm                 CLEAR ║
║        ▀█▀       │    ▄█▀▀   █   ▀▀█▄    │ █▓▓▓▓▓▓▓▓▓▒▒▒▒▒▒▒▒░░░░░░░░█······ ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 000%     │ █▀        █        ▀█ │ ▀███▀ LINK ok                     ║
║      STOPPED     │  SERVO 090° (30-150)  │       TELEMETRY AGE 10 ms         ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:00:00  REQ -  BOARD BOOT  fresh boot                                      ║
║ 00:02:10  REQ 6  ALLOW       cell_status                                     ║
║ 00:02:11  REQ 6  ALLOW       cell_servo(90)                                  ║
║ 00:02:12  REQ 6  ALLOW       cell_fan(0)                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

Panel rows 12-19 per state (the rest of the screen as above):

**idle**: fan 0 % (rotor frozen, grey), needle at 90, beam to 320 mm, CLEAR, lamp unlit
```
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 320 mm                 CLEAR ║
║        ▀█▀       │    ▄█▀▀   █   ▀▀█▄    │ █▓▓▓▓▓▓▓▓▓▒▒▒▒▒▒▒▒░░░░░░░░█······ ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 000%     │ █▀        █        ▀█ │ ▀███▀ LINK ok                     ║
║      STOPPED     │  SERVO 090° (30-150)  │       TELEMETRY AGE 10 ms         ║
```
**fan 60 %** (one fixed rotor frame; the frame steps on ticks at the rate for 60 %), needle at 120
```
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║         ███▄     │       ▄▄█▀█▀█▄▄       │ DIST 250 mm                 CLEAR ║
║     ▄▄  ██▀▀     │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▓▓▓▓▒▒▒▒▒▒▒░░░░░░█··········· ║
║     █████        │   █▀         █   ▀█   │          ┴100 mm           400 mm ║
║     ▀▀   ██▄     │  █▀█        █▀   █▀█  │                                   ║
║          ██▀     │  █         █▀      █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 060%     │ █▀        █▀       ▀█ │ ▀███▀ LINK ok                     ║
║      TURNING     │  SERVO 120° (30-150)  │       TELEMETRY AGE 20 ms         ║
```
**tainted ASK**: the panel is the idle panel (fan 0 until the human answers); the banner and console show the ASK
(T-08 rows 3-5, the CONFIRM line in the console window).

**E-stop latched**: rotor frozen in the halted colour, `HALTED (E-STOP)` in alarm colours, lamp lit red
```
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 120 mm                 CLEAR ║
║        ▀█▀       │    ▄█▀▀   █   ▀▀█▄    │ █▓▓▓▒▒▒░░░█······················ ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP PRESSED, LATCHED     ║
║     FAN 000%     │ █▀        █        ▀█ │ ▀███▀ LINK ok                     ║
║  HALTED (E-STOP) │  SERVO 090° (30-150)  │       TELEMETRY AGE 30 ms         ║
```
**presence** (70 mm, inside the 100 mm threshold): short yellow beam, `PRESENCE` in ASK colours
```
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST 70 mm               PRESENCE ║
║        ▀█▀       │    ▄█▀▀   █   ▀▀█▄    │ █▓▓▒▒░█·························· ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 000%     │ █▀        █        ▀█ │ ▀███▀ LINK ok                     ║
║      STOPPED     │  SERVO 090° (30-150)  │       TELEMETRY AGE 10 ms         ║
```
**no echo**: no beam, only the dotted 0-400 mm scale, `NO ECHO` in ASK colours (no echo counts as not clear, D-43)
```
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║       ▀███       │       ▄▄█▀█▀█▄▄       │ DIST NO ECHO              NO ECHO ║
║        ▀█▀       │    ▄█▀▀   █   ▀▀█▄    │ █································ ║
║     ▄▄▄▄█▄▄▄▄    │   █▀      █      ▀█   │          ┴100 mm           400 mm ║
║     ▀██▀ ▀██▀    │  █▀█      █      █▀█  │                                   ║
║      ▀▀    ▀     │  █        █        █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 000%     │ █▀        █        ▀█ │ ▀███▀ LINK ok                     ║
║      STOPPED     │  SERVO 090° (30-150)  │       TELEMETRY AGE 10 ms         ║
```
**stale** (no frame for > 500 ms): every picture at the last reported values in grey, rotor frozen, `STALE`, `LINK STALE`
```
╠═ LIVE CELL ══════╤═══════════════════════╤═══════════════════════════════════╣
║      ▄    ▄▄     │       ▄▄█▀█▀█▄▄       │ DIST 250 mm                 STALE ║
║     ▄██▄ ▄██▄    │    ▄█▀▀   ▀   ▀▀█▄    │ █▓▓▓▓▓▓▓▒▒▒▒▒▒▒░░░░░░█··········· ║
║     ▀▀▀▀█▀▀▀▀    │   █▀         █   ▀█   │          ┴100 mm           400 mm ║
║        ▄█▄       │  █▀█        █▀   █▀█  │                                   ║
║        ███▄      │  █         █▀      █  │ ▄███▄ E-STOP RELEASED             ║
║     FAN 060%     │ █▀        █▀       ▀█ │ ▀███▀ LINK STALE                  ║
║   HALTED (LINK)  │  SERVO 120° (30-150)  │       TELEMETRY AGE 900 ms        ║
```

## Design as planned
- **Pixels.** A cell is two pixels: top and bottom. Both empty: space; one colour on both: `█` 0xDB; only the top
  or only the bottom: `▀` 0xDF / `▄` 0xDC in that colour on black; two colours: `▀` with the top colour as
  foreground and the bottom as background (blink is off, T-08, so all 16 backgrounds work). One cell pixel is
  9x8 screen pixels, close to square, so circles stay round.
- **Fan** (columns 3-19): 3-blade rotor, 9x5 cells (9x10 pixels) at columns 7-15, rows 13-17; 4 frames 30 degrees
  apart (a 3-blade rotor repeats every 120 degrees), white hub. Frame = `(now_tick / ticks_per_frame) mod 4` with
  a fixed table from the **board-reported** duty: 0 % still; 1-29: 16 ticks; 30-49: 12; 50-59: 10; 60-69: 8;
  70-79: 7; 80-100: 6 (100 Hz ticks; 80 % = 16.7 frames/s, 40 % = 8.3). Frozen at frame 0 when the duty is 0, the
  E-stop is latched, the link is STALE or LOST, or there is no telemetry. Running: light cyan blades; frozen:
  dark grey (the halted colour). Row 18 `FAN 060%`; row 19 the word: `TURNING` / `STOPPED` (duty 0) /
  `HALTED (E-STOP)` / `HALTED (LINK)` / `NO DATA`.
- **Gauge** (columns 21-43): semicircle of 21x6 cells (21x12 pixels) at columns 22-42, rows 13-18, pivot at the
  bottom of row 18, column 32. 0 degrees points left, 90 up, 180 right (as T-08's bar). Arc: brown inside the
  kernel's range 30-150 (`SERVO_MIN_DEG`/`SERVO_MAX_DEG`), dark red outside; radial marks at 30 and 150 in
  light red and at 90 in yellow. Needle: light cyan from the pivot to about 3/4 of the radius at the **reported**
  angle, integer sine table, no floating point; grey when stale; none without telemetry. Row 19:
  `SERVO 090° (30-150)`.
- **Sensor** (columns 45-78): row 13 `DIST 320 mm` and the word right-aligned (`CLEAR`, `PRESENCE`, `NO ECHO`,
  `STALE`, `NO DATA`); row 14 the sensor head `█` at column 46, then 32 cells for 0-400 mm (12.5 mm per cell): beam
  `▓▒░` from the sensor to the reading, `█` at the reading, `·` (0xFA) beyond it; 400 mm or more: the beam fills the
  row. Row 15: the 100 mm threshold `┴` (0xC1) under cell 8 (column 55) with `100 mm`, `400 mm` at the scale end.
  Colours: clear light cyan, presence yellow, stale grey.
- **E-stop lamp**: a 5x2-cell round block lamp at columns 46-50, rows 17-18; lit light red when pressed or
  latched, dark grey otherwise. Row 17 `E-STOP RELEASED` / `E-STOP LATCHED` / `E-STOP PRESSED` /
  `E-STOP PRESSED, LATCHED` / `E-STOP -`; row 18 LINK, row 19 TELEMETRY AGE, both with T-08's words.
- **Never colour alone**: every state above has its word. No franchise symbols, no military wording.
- **Motion**: only the fan frame moves, and only from the reported duty and the tick count; the needle and the
  beam are drawn from the last accepted frame each render (they jump, no easing); nothing else animates. The
  renderer stays pure (no globals, no clock; the tick is a snapshot field) and runs where T-08 put it.

## As built (differences from the plan above)
- **Fan word when stale:** `STALE`, not `HALTED (LINK)`: with no frame for > 500 ms the panel cannot know the
  fan stopped, so it doesn't say so. `HALTED (LINK)` is kept for the board's own watchdog flag (`LINK LOST`),
  where the board halts the fan itself. The rotor is frozen in both.
- **Frozen = rest frame.** The renderer is pure (no state between renders, as T-08 built it), so the rotor's
  frame is `(now_tick / ticks_per_frame) mod 4` and a frozen rotor is drawn at frame 0. When the reported duty
  changes, the rotor can step to a different frame at once (one step, then the new rate). No change outside
  `kernel/ui/` was needed: the snapshot already carries the duty, angle, distance, button, latch, link and age.
- **The dial is exactly symmetric:** 91-180 degrees is drawn as the mirror image of 0-89 (integer sine table
  `sin_q10[0..90]`, x 1024, rounded), so 30 and 150 mirror about column 32.
- **Needle cells (one-based row, column), light cyan:** 30 degrees `(16,26) (17,27) (17,28) (17,29) (17,30)
  (18,30) (18,31)`; 90 degrees `(14,32) .. (18,32)` straight up column 32; 150 degrees the mirror of 30
  (`(16,38) (17,34) (17,35) (17,36) (17,37) (18,33) (18,34)`). Pivot: white bottom pixel of (18,32).
- **Pixel canvas** 14x50 bytes (700 bytes on the 16 KiB kernel stack), only for the rotor, dial and lamp; the
  beam is shade characters. The console window is `SCR_CON_ROWS` = 5; `vga.c` sizes its buffers from that
  constant, so the driver source is unchanged.

## Rotor speed (fixed table, ticks per frame at 100 Hz)
| reported duty | 0 | 1-29 | 30-49 | 50-59 | 60-69 | 70-79 | 80-100 |
|---|---|---|---|---|---|---|---|
| ticks / frame | still | 16 | 12 | 10 | 8 | 7 | 6 |

Frame changes over 960 ticks (`screen_test fan-count 960`, `fan-count.txt`): duty 0: 0; 40 %: 80; 60 %: 120;
80 %: 160; 80 % latched: 0; 80 % stale: 0; 80 % link lost: 0.

## T-08 golden rows changed (only the rows the panel and the smaller console replaced)
| State | Rows changed | Why |
|---|---|---|
| idle | 7-19 | console now rows 7-11 (shows the last 5 lines; the first fixture line `action 2 servo_set(90)` dropped), panel rule row 12, pictures 13-19 |
| ask | 7-19 | same; the 5 console lines fit exactly (row 7 is no longer blank) |
| estop | 8-19 | same; row 7 is blank in both |

Rows 1-6 and 20-25 are byte-identical to T-08's goldens in all three states.

## Test changes (memory rule: cause, diff, expectation)
| Test | Cause | Change | Expected outcome |
|---|---|---|---|
| `test_t08_screen.py` GOLDEN idle/ask/estop | the panel and 5-row console | rows 7-19 (see above) | rows 1-6, 20-25 unchanged |
| `test_render_colours_idle/ask/estop` | rows moved | prompt and CONFIRM line row 13 -> 11; RELEASED/LINK ok (18, 3-17 / 43-49) -> (17, 52-66 / 18, 52-58); PRESSED, LATCHED (18, 3-25) -> (17, 52-74) | same colours (TEXT, INFO, ASK, ALARM) |
| `test_render_no_cell_shows_no_values` | the dial and beam replace the bars | `FAN ---` row 15 -> 18; `^` / `█` absence -> `SERVO ---`, `DIST ---` and no beam shades; the missing needle is checked by colour in `test_no_needle_without_data_grey_when_stale` | without telemetry still no reading drawn |
| `test_t08_vga_buffer` console | the 5-row window has scrolled past `cellcmd: servo 120 -> 0` / `fan 90 -> -200` when it is read | asserts the window shows exactly the newest COM1 lines in order (TAPE/AUDIT left out) and `cellcmd: done`, and that both result lines are on COM1; the ticker still checks both decisions | stronger than before: every window row is compared with COM1 |
| `test_t08_vga_buffer`, `test_t08_screenshots_sim` rows | rows moved | SERVO row 16 -> 19, E-STOP row 18 -> 17 (`released` -> `RELEASED`), FAN row 15 -> 18, prompt rows 12-13 -> 10-11 | same values |
| `tests/host/screen_test.c` | console 5 rows; new states | idle fixture drops its first console line; states `fan60`, `presence`, `noecho`, `stale`, `servo=<deg>`, `fan-count <ticks>` added | n/a |

Fixes to my own new tests while writing them (no kernel expectation involved): a column range off by one
(`TURNING` is columns 8-14); the wording check matched its own docstring (it now checks the card's banned words
over the new files and franchise/military wording over the rendered states and C literals); the sim run must
answer `y` to the first fan command (policy `first_fan: ask`); the presence / no echo / stale shots moved
before the E-stop (the kernel latch stays until `estop reset`, so they showed `E-STOP LATCHED`), and the stale
shot waits for a 250 mm frame first.

## Render time
`test_t14_render_time` (`render-time.txt`): test image with `screen` and the simulated cell, the rotor turning
at 60 % for 2 s while the distance jumps 70 / 250 / no echo / 300 mm. Whole service pass (snapshot + render +
diff + VGA writes): **avg 34-35 µs, max 498-720 µs, 0 passes ≥ 1 ms** over 343 renders, in four runs. The max
follows the number of changed cells (~500 emulated VGA stores), as in T-08.

Found while measuring: QEMU monitor commands (`pmemsave`, `screendump`) hold the vCPU while the TSC runs on, so
a service pass that spans one reads 3-10 ms. Phase by phase (a throwaway diagnostic, not committed): idle,
presence, silent link and resume: max ≤ 102 µs, 0 over 1 ms; rotor turning 2 s: max 502-603 µs, 0 over 1 ms;
then 10 `pmemsave`: one pass 3.2 ms in 1 of 3 runs; then 5 `screendump`: one or two passes 9.4-9.5 ms in 2 of 3
runs. So the timing is measured in its own test with no monitor command, and the screenshot test asserts no
timing. T-08's `test_t08_vga_buffer` measures after one `pmemsave` and has passed every run so far; it could hit
the same artifact (not changed: outside this card's need).

## Acceptance
| # | Result | Evidence |
|---|---|---|
| A1 | **pass**: clean build, zero warnings (`-Werror`; kernel, test, ktest, permissive, host images); full default suite in the worktree: **902 passed, 6 skipped** (hw + 2 by design); ruff clean; new goldens (fan60, presence, noecho, stale) and updated T-08 goldens (idle, ask, estop) pass; `make test-kernel` 20/20 | `build.txt`, `pytest.txt`, `golden-tests.txt`, `host-render.txt` |
| A2 | **pass**: `test_fan_frame_motion` (0 changes at duty 0 / latched / stale / lost; 80 % > 60 % > 40 %), `test_needle_cells[30,90,150]`, `test_needle_mirror_and_marks`; render avg 34-35 µs, max 498-720 µs over four runs, 0 ≥ 1 ms | `fan-count.txt`, `render-time.txt` |
| A3 | **pass**: `git diff --stat main...t14` = `kernel/ui/screen.c`, `kernel/ui/screen.h`, `tests/host/screen_test.c`, `tests/test_t08_screen.py`, `tests/test_t14_panel.py`, `docs/evidence/T-14/` (+ `docs/STATUS.md` at the end, per CLAUDE.md); policy hash `4101a8cc30507872...` unchanged; `kernel/policy`, `kernel/drivers`, `abi`, `firmware`, `bridge` untouched | this README |
| A4 | **pass**: sim-cell PNG + exact text of every state: `sim-idle`, `sim-fan60` (rotor turning, checked across 6 reads), `sim-ask` (tainted ASK), `sim-estop` (AUTO HALT, latched, lamp red, rotor halted), `sim-presence` (70 mm), `sim-noecho`, `sim-stale`; mockups above | `sim-*.png`, `sim-*.txt` |
| A5 | **pass**: commits `T-14: ...` with `Builder: Opus 5.5/high`; "ready to merge" reported, not merged | `git log main..t14` |

## For the lead / filming
- `docs/video/shots.md` (not in this card's paths) quotes T-08's panel words: `E-STOP released` is now
  `E-STOP RELEASED`; `PRESENCE YES` is now the beam word `PRESENCE` (and `PRESENCE NO` is `CLEAR`).
- The console window shows 5 lines instead of 7; long request lines still wrap at 76 columns as before.
- The real-cell shots come from the filming session (`make run-cell-screen`, `make screendump NAME=<x>`, which
  writes into `docs/evidence/T-08/`).
