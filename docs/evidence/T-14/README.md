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
