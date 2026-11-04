# T-18 Graphics screen (experimental branch `gfx`, D-101)

Builder: Fable 5.1 / high. Worktree `~/dev/RING3-gfx`, branch `gfx`. **Not merged**: main stays at the release
candidate; a merge needs the user's word, a release check and Gate C.

## What it is

An optional pixel screen for the QEMU window: the same screen model as the text screen (T-08 / T-14: status
bar, decision banner, console window, the cell panel, the decision ticker), drawn at 1024x768x32 with shapes.
Text mode stays the default; nothing changes without the boot option `gfx=1` (`make run-cell-gfx`).

- `kernel/ui/gfx.c` / `gfx.h`: the pure renderer (integer maths, no I/O, no globals; built for the kernel and
  the host). Regions are redrawn only when their inputs changed; a per-tick pixel budget with pending bits
  spreads a big change (a decision touches the banner, the console, the ticker and the status line) over a
  few ticks in priority order, so one tick never does more than the budget while every change still lands.
- `kernel/drivers/gfxdev.c`: PCI scan for QEMU's standard VGA, the Bochs DISPI mode set, the font read from
  VGA plane 2 before the switch, the kernel-only mappings of the framebuffer (BAR0) and of the back buffer
  (physical 16 MiB, above the allocator window, checked against the multiboot memory map), the rectangle copy.
  Every check that fails prints why on COM1 and leaves the text screen on; no panic, no blank screen.
- `kernel/arch/i386/paging.c`: `paging_map_kernel()`: supervisor-only windows outside the identity map
  (never inside the user range).
- `kernel/drivers/vga.c`: with `gfx=1` the snapshot goes to the pixel renderer and the touched rectangles are
  copied to the framebuffer; otherwise the text path, unchanged. `screen` prints the figures for either.
- `user/test/fbpoke.c`, `bbpoke.c`: ring-3 writes into the two windows (INV-06 tests).
- `tests/host/gfx_test.c`, `tests/host/screen_states.h` (the host fixtures, now shared with the text screen's
  `screen_test.c`), `tests/fixtures/vga-font-8x16.hex`, `tests/test_t18_gfx.py`.

No change to `decide()`, policy state, the syscall gate, `uaccess`, the frame parsers, COM1/COM2/COM3 output,
the event log, the bridge or the firmware. Policy hash `4101a8cc30507872...` unchanged.

## Hardware facts and their sources (D-09: facts, no code)

| Fact | Source |
|---|---|
| PCI configuration mechanism 1: address port 0xCF8 (bit 31 enable, bus 23:16, device 15:11, function 10:8, register 7:2), data port 0xCFC | PCI Local Bus Specification 3.0, §3.2.2.3.2 |
| QEMU standard VGA: PCI ID `1234:1111`; BAR0 = framebuffer memory, 16 MB by default; Bochs VBE interface at I/O ports 0x01CE (index) and 0x01CF (data); the framebuffer endianness register defaults to little endian | QEMU `docs/specs/standard-vga.rst` (https://www.qemu.org/docs/master/specs/standard-vga.html) |
| DISPI register indexes 0 ID, 1 XRES, 2 YRES, 3 BPP, 4 ENABLE, 5 BANK, 6 VIRT_WIDTH, 7 VIRT_HEIGHT, 8/9 X/Y_OFFSET; IDs 0xB0C0..0xB0C5; ENABLE bits 0x01 enabled, 0x40 LFB, 0x80 keep memory | QEMU `include/hw/display/bochs-vbe.h` |
| VGA sequencer 0x3C4/0x3C5 (0 reset, 2 map mask, 4 memory mode), graphics controller 0x3CE/0x3CF (4 read map select, 5 mode, 6 miscellaneous); the text font in plane 2, 32 bytes per character, at 0xA0000 with plane 2 selected and odd/even off | IBM VGA technical reference (Video Subsystem), sequencer and graphics controller register descriptions |
| Multiboot v1 memory map entries (`size, addr, len, type`; type 1 = available) | Multiboot Specification 0.6.96, §3.3 |

The measured values on this QEMU (8.2.2): BAR0 = `0xFD000000`, DISPI id `0xB0C5`, mode read-back
1024x768x32. The kernel prints them at boot:

```
gfx: 1024x768x32 linear framebuffer phys=0xfd000000 (PCI 1234:1111 BAR0, DISPI id 0xb0c5), back buffer phys=0x01000000, font from VGA plane 2, both windows kernel-only
```

## The font

`tests/fixtures/vga-font-8x16.hex` is the 8x16 glyph set the VGA BIOS had loaded into plane 2, read by the
kernel at boot (sequencer / graphics controller switched to plane 2, 0xA0000, registers restored) and dumped
once over COM1 with the test image's `gfxfont` command (256 lines `F xx <32 hex digits>`, sha256
`259dcc985d9b643b6edd4ca9f8aabbdca59caa8adc25042c905fff5d21b64fce`). It is hardware data from the running
machine, not a pasted font; `test_t18_font_fixture_is_what_the_kernel_reads` fails if the kernel reads
anything else. The kernel checks the glyphs before trusting them (space blank, full block solid, 'A' has ink)
and falls back to text mode otherwise.

## Design (original, D-66a)

Black ground, amber frame and labels, light-cyan information, inverse boxes for ASK (black on yellow) and the
alarm colours (white on dark red); every state has a word; the words are the text screen's (`words.c`). Status
bar and banner in 16x32 glyphs (x2), console and ticker in 8x16 (x1). The cell panel: a three-blade rotor
(filled swept polygons, 136 px) whose angle is `tick x rate(duty)` modulo 120 (2..7 degrees per tick for
duties 1..100 %, 0 while frozen: no telemetry, duty 0, E-stop latched, link STALE or LOST); a half dial with
the servo range marked (dark red outside 30-150, brown inside, marks and numerals at 30 / 90 / 150) and a
needle to the reported angle (grey when stale); the sensor beam (0-400 mm over 280 px, fading, the object at
the reading, the 100 mm threshold marked, dots beyond the reading); the E-stop lamp (lit red with a
highlight when pressed or latched). Nothing moves without a real value.

## Host golden images (`host-*.png`)

`gfx_test` renders the shared fixtures; `test_golden_image` hashes the raw frame per state (idle, ask,
ask-wipe, estop, nocell, fan60, presence, noecho, stale, simboot). `test_incremental_draw_equals_full_draw`
and `test_budgeted_draw_converges` prove the damage path: drawing a state over `idle` through the region
mechanism (unlimited budget, and the kernel's 66,000-unit budget over several calls) gives the same pixels as
a full draw, and every changed pixel lies inside a reported rectangle. `test_fan_motion_rules`: over 200
ticks the angle never changes at duty 0 / latched / stale / lost, changes every tick at 40 / 60 / 80 %, and the
degrees turned at 80 % > 60 % > 40 %. `test_fan_turns_only_inside_its_box`: two consecutive ticks differ only
inside the rotor's rectangle.

## QEMU (`sim-*.png`, from the simulated cell, `-display none`, screendump through the monitor)

- `test_t18_gfx_screendump_and_ring3_fault`: boot with `gfx=1`, sim cell idle. The screendump's title, rotor,
  dial, beam, lamp and their words equal the host render of `simboot` pixel for pixel; 99.5 % of all pixels
  equal (`pixel-match-boot.txt`; the rest: clock, free KiB, logged count, console lines, age). Then `fbpoke`
  (write to `0xFD000000`) and `bbpoke` (write to `0x01000000`) each end in `PROC_FAULT vector=14` with `cr2` at
  that address and a user-mode error code, the next program runs, the screen is still drawn
  (`sim-after-fault.png`), 0 PANIC, 0 dropped records.
- `test_t18_states_sim`: `sim-idle`, `sim-presence` (70 mm), `sim-noecho`, `sim-stale` (250 mm, silence),
  `sim-ask` (tainted, rotor at rest), `sim-fan60` + three more shots 50 ms apart (each rotor picture is one of
  the 24 host pictures for 60 %, and they differ: it turns), `sim-estop` (lit lamp, rotor at rest, HALTED).
- `test_t18_fallback_without_vga_device`: `-vga none` + `gfx=1`: `gfx: no QEMU standard VGA (PCI 1234:1111)
  on bus 0: text mode`, the prompt comes up, `screen` reports `gfx=0`, exit 33.

## D-105: the tick lease under rendering (replaces the "max <= 2 ms" assertion)

### (a) Where rendering runs and what can preempt it

`vga_service()` (`kernel/drivers/vga.c`) is the whole pass: snapshot, render, rectangle copies. It is called
from exactly two places in `kernel/core/audit.c`:

1. **`audit_flush()`, `audit.c:160`**, reached from the kernel's own wait loops. All IDT gates are interrupt
   gates (`kernel/arch/i386/idt.c:1-2, 39`), so the interrupt state is whatever the loop set:
   - the console loop (`console.c:66-76`: `audit_flush(1)` then `sti(); hlt()`), the confirmation prompt loop
     (`confirm.c:83-91`) and `exec.c:150`: **interrupts enabled** after the first `sti`, so the timer tick
     (`audit_on_tick`, `audit.c:207`) and the COM3 interrupt (`device_on_irq`) preempt a render in progress.
   - the executor's `agent_recv` wait (`syscall.c:120-123`: `audit_flush(1); sti(); hlt(); cli();`): the
     syscall gate runs with **interrupts off** (F1, D-50) and only opens them for the `hlt`; a render there is
     not preempted, the pending tick and COM3 interrupts are taken right after it, at the `sti`.
2. **`audit_on_tick()`, `audit.c:231`**, when the tick interrupted ring 3 (the kernel's context is parked). It
   runs **inside the tick handler, interrupts off**, after that tick's lease check (`audit_feed(EV_TICK)` and
   `device_auto_halt`, `audit.c:220-222`) and the bounded audit drain. The next tick is delayed by at most the
   pass; a COM3 frame arriving during it is taken at the `iret`, and the tick's poll backstop
   (`device_on_tick`, `audit.c:213`) would read it on the next tick anyway.

So the claim is not "rendering is always preemptible"; it is: the lease check of a tick is never behind a
render (site 1 is preempted by it; at site 2 the check comes first), and where a render is not preemptible
(site 2, the `agent_recv` wait) the hazard path is delayed by at most one pass, which the per-tick budget
bounds (`GFX_TICK_BUDGET` 66,000 pixel units; measured p99 at or under 1.3 ms, a tick is 10 ms). The text
screen has the same two sites with a 35 µs pass.

### (b) Hazard -> AUTO_HALT -> FAN 0 under continuous redraw (`latency.txt`)

`test_t18_halt_latency_under_redraw`: the same trials first in a text-mode (`screen`) session, then in a
`gfx=1` session with the rotor at 60 % (a redraw every tick, the age line too). Hazard = the hand at 50 mm sent
as one STATE frame at once (`emit_state`); latency = host time from that frame to the board's FAN 0, and the
kernel-internal gap = ticks from the frame's tape record to the AUTO_HALT record. Two scenarios: the executor
running in ring 3 (`cellcmd ... wait 300`, render site 2) six times, and the executor blocked in
`agent_recv` for a slow scripted model (test_inv_16_halt's scenario, the interrupts-off wait) once.

| run | text, ring 3 (6 trials) | gfx, ring 3 (6 trials) | text, agent_recv | gfx, agent_recv | internal gap |
|---|---|---|---|---|---|
| 1 | median 27.5 ms, max 50.2 | median 26.4 ms, max 45.4 (5 ticks) | - | - | 0 ticks, all |
| 2 | median 34.1, max 45.3 | median 21.1, max 33.5 (4 ticks) | - | - | 0 ticks, all |
| 3 | median 41.7, max 48.2 | median 29.4, max 44.1 (5 ticks) | - | - | 0 ticks, all |
| 4 | median 27.1, max 36.4 | median 30.8, max 45.6 (5 ticks) | 4.0 ms | 1.0 ms (1 tick) | 0 ticks, all |
| 5 (evidence run, `latency.txt`) | see file | see file | 1.9 ms | 46.1 ms (5 ticks) | 0 ticks, all |

The host-side figures carry the host's socket and scheduling jitter in both modes (single trials from 1 to
50 ms in either mode and either scenario, the sim thread and QEMU's chardev in the path); the two modes are
indistinguishable. The assertions:
every gfx trial within the bound text mode is tested against (`test_inv_16_halt`: FAN 0 within 250 ms = 25
ticks of the hand), the `agent_recv` scenario within the same bound, and the kernel-internal gap 0 ticks in
both modes and both scenarios. No `max <= 2 ms` assertion remains; the host-stall outliers are reported, not
hidden.

### (c) Render time as measured (`render-time.txt`)

Whole service pass, TSC, interrupt-handler time inside a pass taken out (`max_gross_us` keeps it; equal in
every run). From the latency test's gfx session (2,500-2,800 passes, rotor turning throughout, seven hazard
cycles with decisions, console lines and ticker updates):

| session | avg | p99 (100 µs buckets, upper edge) | max | passes >= 1 ms | passes >= 2 ms |
|---|---|---|---|---|---|
| run 1 | 244 µs | <= 1.3 ms | 1.52 ms | 131 / 2523 | 0 |
| run 2 | 237 µs | <= 1.3 ms | 2.03 ms | 130 / 2529 | 1 |
| run 3 | 247 µs | <= 1.3 ms | 2.18 ms | 141 / 2539 | 1 |
| run 4 (with the bridge thread) | 253 µs | <= 1.3 ms | 3.54 ms | 159 / 2819 | 6 |
| run 5 (evidence run) | 253 µs | <= 1.3 ms | 1.60 ms | 161 / 2824 | 0 |
| text mode, same trials | 33-34 µs | - | 183-318 µs | 0 | 0 |

The histogram (`screen-hist:` line of the `screen` command) is in `latency.txt`. The passes above 2 ms are
0.0-0.2 % of passes; their work is the same as the 1.3 ms ones (the budget caps a pass at the rotor plus the
age line, one banner strip, or the needle; the native cost of any region is about 10 µs), so they are the
emulated CPU stalling on the host, as the earlier runs showed. Idle boot with the sim cell: avg 160-176 µs,
max 381-722 µs (`render-time-boot.txt`).

## Sim-cell PNGs (A4)

`sim-boot.png`, `sim-idle.png`, `sim-presence.png`, `sim-noecho.png`, `sim-stale.png`, `sim-ask.png`,
`sim-fan60.png` (+ `-1..-3`), `sim-estop.png`, `sim-after-fault.png`; host renders `host-*.png`. No banned
words (INV-11 scans `kernel/ui/gfx.c` and the tests); original shapes, no franchise symbols or fonts.

## Checkpoint (2 h) and D-105

`make run-cell-gfx` composes the same QEMU line as `run-cell-screen` with `-vga std` and `-append "gfx=1"`;
the screen shows the real sim-cell data with readable text (the PNGs above are that screen, taken headless
through the same monitor). The real board was not attached in this session; the GTK window was not opened
here (no filming run), the command is in the Makefile for the user. The lead's D-105 (continue; replace the
flaky "max <= 2 ms" assertion with the lease property; rebase; "ready to merge") is the section above. The
measurement change stays: `max_us` leaves out interrupt-handler time, `max_gross_us` keeps it, and the
`screen` command prints a 100 µs histogram (`screen-hist:`) for the percentiles.

## Acceptance

| | Result | Evidence |
|---|---|---|
| A1 | **pass**: full default suite on this branch, rebased onto main `a639f42`, **978 passed, 6 skipped** on a quiet host (two earlier runs while another session's suite shared the host each had one text-mode timing flake, listed in `pytest.txt`; both pass 3/3 alone) (946 before + 32 T-18; text mode default; replay-match tests inside); policy hash `4101a8cc...` unchanged; ruff clean; `check-paths` OK | `pytest.txt` |
| A2 | **pass** (D-105 form): golden-image, damage and fan tests pass; hazard -> FAN 0 latency under continuous redraw within text mode's tested bound in both scenarios, internal gap 0 ticks; render avg 0.24-0.25 ms, p99 <= 1.3 ms, max 1.5-3.5 ms as measured; per-tick budget kept | `latency.txt`, `render-time.txt`, `host-*.png` |
| A3 | `fbpoke` / `bbpoke`: PROC_FAULT vector 14 at the window address, 0 PANIC; `-vga none` falls back to text | `sim-after-fault.png`, test transcript |
| A4 | sim-cell PNGs of every state; no banned words; original design | `sim-*.png` |
| A5 | worktree `gfx` only; not merged | this README |
