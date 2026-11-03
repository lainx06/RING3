# Design spec from the design reviewer (D-66), verbatim except the first two lines. The lead's corrections in docs/tasks/T-08.md override it.

The design uses original industrial-console framing. All readings below are illustrative. Each kernel mockup is **80 columns × 25 rows**, including borders.

A — idle.

```text
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:02:14   EXEC IDLE                 FREE 2816 KiB    LOGGED 4            ║
║ LAST KERNEL DECISION: ALLOW                              TAINTED: NO         ║
║                                    ALLOW                                     ║
║ cell_fan(0)                                                                  ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ ring3> Set the gauge to 90 degrees and turn the fan off.                     ║
║ REQ 6  cell_servo(90) -> ALLOW                                               ║
║ REQ 6  cell_fan(0) -> ALLOW                                                  ║
║ REQ 6  OUTCOME DONE                                                          ║
║                                                                              ║
║                                                                              ║
║ ring3>                                                                       ║
╠═ LIVE CELL ══════════════════════════════════════════════════════════════════╣
║ FAN 000%         [                    ]                                      ║
║ SERVO 090°       [----------^----------]   0°                  180°          ║
║ DIST 640 mm      [████████████████    ]                                      ║
║ E-STOP released                         LINK ok                              ║
║ PRESENCE NO                             TELEMETRY AGE 12 ms                  ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:00:00  REQ -  BOARD BOOT                                                  ║
║ 00:02:10  REQ 6  ALLOW       cell_dist()                                     ║
║ 00:02:11  REQ 6  ALLOW       cell_servo(90)                                  ║
║ 00:02:12  REQ 6  ALLOW       cell_fan(0)                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

A — request running; tainted agent awaiting confirmation.

```text
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:03:09   EXEC RUNNING REQ 7        FREE 2816 KiB    LOGGED 8            ║
║ LAST KERNEL DECISION: ASK                                TAINTED: YES        ║
║                                     ASK                                      ║
║ waiting for y/N                                                              ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ ring3> Read the operator notes, then set the fan to 60%.                     ║
║ REQ 7  read operator notes -> ALLOW                                          ║
║ REQ 7  agent tainted by untrusted operator notes                             ║
║ REQ 7  cell_fan(100) -> DENIED: limit                                        ║
║ REQ 7  cell_dist() -> ALLOW                                                  ║
║ CONFIRM: agentsh wants cell_fan(60) [tainted]. Allow? [y/N]                  ║
║                                                                              ║
╠═ LIVE CELL ══════════════════════════════════════════════════════════════════╣
║ FAN 000%         [                    ]                                      ║
║ SERVO 090°       [----------^----------]   0°                  180°          ║
║ DIST 440 mm      [███████████         ]                                      ║
║ E-STOP released                         LINK ok                              ║
║ PRESENCE NO                             TELEMETRY AGE 8 ms                   ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:03:05  REQ 7  ALLOW       read operator notes                             ║
║ 00:03:06  REQ 7  DENIED      cell_fan(100): limit                            ║
║ 00:03:07  REQ 7  ALLOW       cell_dist()                                     ║
║ 00:03:09  REQ 7  ASK         cell_fan(60): tainted                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

A — E-stop pressed while the fan was running. This frame follows the first telemetry packet reporting zero duty. Zero duty does not assert that the blades have finished coasting.

```text
╔═ RING3 / KERNEL EXECUTIVE ═══════════════════════════════════════════════════╗
║ UP 00:03:12   EXEC KILLED               FREE 2816 KiB    LOGGED 10           ║
║ LAST KERNEL DECISION: AUTO HALT                          TAINTED: YES        ║
║                                  AUTO HALT                                   ║
║ E-STOP LATCHED                                                               ║
╠═ CONSOLE ════════════════════════════════════════════════════════════════════╣
║ CONFIRM: agentsh wants cell_fan(60) [tainted]. Allow? [y/N]                  ║
║ y                                                                            ║
║ REQ 7  cell_fan(60) -> CONFIRMED                                             ║
║ FAN 060% reported by cell telemetry                                          ║
║ E-STOP PRESSED                                                               ║
║ REQ 7  AUTO HALT: E-STOP latched                                             ║
║ REQ 7  OUTCOME STOPPED                                                       ║
╠═ LIVE CELL ══════════════════════════════════════════════════════════════════╣
║ FAN 000%         [                    ]                                      ║
║ SERVO 090°       [----------^----------]   0°                  180°          ║
║ DIST 120 mm      [███                 ]                                      ║
║ E-STOP LATCHED                          LINK ok                              ║
║ PRESENCE YES                            TELEMETRY AGE 4 ms                   ║
╠═ LAST 4 KERNEL DECISIONS / OLDEST TO NEWEST ═════════════════════════════════╣
║ 00:03:07  REQ 7  ALLOW       cell_dist()                                     ║
║ 00:03:09  REQ 7  ASK         cell_fan(60): tainted                           ║
║ 00:03:10  REQ 7  CONFIRMED   cell_fan(60)                                    ║
║ 00:03:12  REQ 7  AUTO HALT   E-STOP latched                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

B — host timeline, a separate example run. Ten event lines; maximum width **86 columns**. The initial fan write is assumed to be human-gated by the configured policy. The grant after `y` is recorded as `CONFIRMED`.

```text
00:03:00.000 | 7   | cell_dist()            | ALLOW      | DIST 640 mm
00:03:00.050 | 7   | cell_fan(60)           | ASK        | waiting for y/N
00:03:01.200 | 7   | cell_fan(60)           | CONFIRMED  | operator answered y
00:03:01.250 | 7   | -                      | -          | telemetry: FAN 060%
00:03:02.000 | 7   | cell_fan(100)          | DENIED     | limit
00:03:03.000 | 7   | read operator notes    | ALLOW      | untrusted read; TAINTED YES
00:03:04.000 | 7   | cell_fan(30)           | ASK        | tainted; waiting for y/N
00:03:05.100 | 7   | cell_fan(30)           | DECLINED   | operator said no (n)
00:03:05.200 | 7   | cell_dist()            | ALLOW      | DIST 440 mm
00:03:05.250 | 7   | OUTCOME                | -          | REFUSED
```

`-` means the event has no corresponding action or decision. Taint is a result/state change, never a new decision word. `REFUSED` is the reported request outcome; earlier permitted actions remain visible.

The field specification uses **one-based coordinates**. VGA colours: black **0**, dark red **4**, light red **12**, yellow **14**, light cyan **11**, white **15**. Yellow supplies the amber impression; light red supplies the warmer accent. Use the standard CP437 font and double-line borders.

| Field | Row | Columns | Colours, foreground/background | Meaning | Data source |
|---|---:|---:|---|---|---|
| Frame and section labels | 1, 6, 14, 20, 25 | 1–80 | 14/0 | Fixed region boundaries | Static labels |
| Uptime | 2 | 3–13 | 11/0 | Elapsed kernel uptime | Kernel clock |
| Executor | 2 | 17–41 | 15/0; KILLED 15/4 | IDLE, RUNNING REQ n, KILLED | Executor state and request ID |
| Free memory | 2 | 43–57 | 11/0 | Free KiB | Allocator accounting |
| Decisions logged | 2 | 60–78 | 11/0 | Count of recorded decisions | Kernel log count |
| Last decision | 3 | 3–57 | Decision treatment below | Exact latest decision word | Latest kernel decision record |
| Taint | 3 | 60–78 | NO 11/0; YES 0/14 | TAINTED: NO or YES | Executor taint flag |
| Main banner | 4 | 3–78 | Decision treatment below | Centred exact decision word | Same latest record |
| Banner detail | 5 | 3–78 | Same banner treatment | Call, reason, waiting text, or E-STOP LATCHED | Decision record and latch state |
| Console | 7–13 | 3–78 | Text 15/0; prompt 11/0; CONFIRM 0/14 | Requests, responses, confirmations, outcomes | Operator input and actual execution events |
| Fan value | 15 | 3–17 | 15/0 | Reported output duty percentage | Latest board telemetry |
| Fan bar | 15 | 20–41 | 14/0 | Twenty cells representing 0–100% duty | Same duty value |
| Servo value | 16 | 3–17 | 15/0 | Reported servo angle in degrees | Latest board telemetry |
| Servo dial | 16 | 20–42 | Track 14/0; needle 11/0 | Twenty-one positions; `^` marks angle | Same angle |
| Servo scale | 16 | 46–68 | 11/0 | Configured travel endpoints | Servo configuration |
| Distance value | 17 | 3–17 | 15/0 | Millimetres or NO ECHO | Ultrasonic telemetry |
| Distance bar | 17 | 20–41 | 11/0 | Twenty cells; shorter means closer | Same distance |
| E-stop | 18 | 3–40 | released 15/0; PRESSED/LATCHED 15/4 | Exact stop state | Hardware input and kernel latch |
| Link | 18 | 43–78 | ok 11/0; LOST 15/4 | Transport state | Kernel link monitor |
| Presence | 19 | 3–40 | NO 11/0; YES 0/14 | Presence flag | Existing presence state |
| Telemetry age | 19 | 43–78 | 11/0 | Milliseconds since last accepted packet | Kernel clock minus receive timestamp |
| Four decisions | 21–24 | 3–78 | Time/request 11/0; decision treatment below | Oldest at top, newest at bottom | Last four kernel log records |

For the banner, use **11/0** for ALLOW, CONFIRMED and BOARD BOOT; **0/14** for ASK and DECLINED; **15/4** for DENIED and AUTO HALT. Apply the same treatment to decision words in the ticker. `E-STOP LATCHED` occupies the banner detail row during the corresponding halt event and remains independently visible in the cell panel afterward.

Keep the vocabulary exact:

- DENIED reasons: capability, bad argument, limit, hand too close, stale telemetry, E-STOP latched, too soon, tainted.
- ASK detail: waiting for y/N.
- DECLINED detail: operator said no or timed out.
- Request outcomes: DONE, REFUSED, STOPPED, ERROR.

The sample distance bars assume an **800 mm display span**; use a fixed span appropriate to the sensor configuration. The sample servo dial assumes **0–180° configured travel**. Neither scale adds a measurement. A reported servo angle represents only what the board actually reports; do not imply independent position feedback.

| Timeline field | Row | Columns | ANSI colours | Meaning | Data source |
|---|---:|---:|---|---|---|
| Time | Every event | 1–12 | 159 on 0 | Kernel uptime at event, milliseconds | Event timestamp |
| Request | Every event | 16–18 | 159 on 0 | Request number; `-` when absent | Request context |
| Model action | Every event | 22–43 | 15 on 0 | Proposed action, or OUTCOME marker | Action record / outcome event |
| Kernel decision | Every event | 47–56 | 214 on 0; adverse decisions 15 on 124 | Exact kernel decision; `-` when absent | Kernel record |
| Result | Every event | 60–100 | 15 on 0; taint 214 on 0 | Returned value, reason, response or outcome | Actual event payload |

Long action/result text is clipped to its field with `...`; retain the complete event in the underlying log. Never clip the decision or outcome word. Human-response lines repeat the pending action for context; they do not represent another proposal.

Motion rules:

| Motion | Real trigger | Behaviour |
|---|---|---|
| Fan bar | New 20 Hz telemetry packet | Set twenty-cell fill from reported duty. No movement between samples. |
| Distance bar | New valid distance sample | Set fill from distance and the fixed display span. It shrinks as the measured distance decreases. NO ECHO clears the fill and prints the words. |
| Servo needle | New reported angle | Move `^` directly to the corresponding position on the character dial. No invented intermediate angles. |
| Telemetry age | Kernel clock advances | Update elapsed milliseconds since the last accepted packet. Lost packets do not reset it. |
| Decision banner | New logged kernel decision | Replace all words immediately. Apply the banner colours to rows 3, 4 and 5 at roughly 0, 70 and 140 ms; finish by 200 ms and hold. A newer event supersedes the wipe. |
| E-stop emphasis | PRESSED or latch transition | Update the stop word immediately. AUTO HALT and KILLED appear only when their actual events arrive. Never delay these words for animation. |
| Decision ticker | New decision record | Shift existing entries up one row and insert at row 24 in one redraw. Bursts retain the latest four; no scrolling backlog animation. |
| Console scroll | New console output | Scroll only rows 7–13. While ASK is pending, reserve rows 12–13 for confirmation and input; scroll output above them. |
| Timeline arrival | New event line | Show the complete line immediately with an amber inverse background for 150 ms, then settle to normal colours. |
| Final outcome pulse | Actual request-outcome event | One 200 ms inverse emphasis, then a steady bold OUTCOME line. Replace the normal arrival highlight with this pulse. |

Use a fixed 80 × 25 backing buffer and redraw only changed cells, at most 100 times per second. Telemetry remains 20 Hz; the extra redraw opportunities serve input, clocks and banner attributes. Use a steady cursor. Disable blinking attributes.

On LINK LOST or stale telemetry, retain the last received numeric values and their bars; the link word, increasing age and actual denial reason explain their status. Do not animate them toward zero. On a terminal without cursor addressing, print plain permanent lines and a bold OUTCOME; omit transient emphasis.

Video treatment:

- **Opening card:** `RING3` above `MODEL PROPOSES / KERNEL DECIDES`. Black field, amber double border, cyan secondary line. Hold for roughly two seconds.
- **Event cards:** Use `ASK`, `CONFIRMED`, `DENIED` or `AUTO HALT`, followed by the actual call or reason. Introduce each card at its recorded event.
- **Transitions:** A short vertical reveal of three horizontal bands echoes the kernel banner. Trigger cuts on request submission, confirmation, denial and E-stop events. Keep event order and timing legible.
- **Callouts:** Rectangular amber labels with simple cyan leader lines. Examples: `UNTRUSTED READ`, `TAINTED: YES`, `OPERATOR: n`, `E-STOP LATCHED`. Reveal whole labels at the corresponding event; no typed lettering.
- **Hardware shots:** Pair the fan, servo or button close-up with its corresponding screen event. Keep the physical button press and resulting halt together in one uninterrupted shot where possible.
- **CRT finish:** Apply restrained bloom around bright characters and a stationary, low-opacity scanline texture over the actual recording. Preserve sharp text beneath it. No rolling bands, flicker or animated interference.
- **Two-second readability:** Frame the whole screen for orientation, then use event-triggered crops of the banner, confirmation or timeline line. Keep the decision word and request number visible together. End on the actual `OUTCOME` and hold.