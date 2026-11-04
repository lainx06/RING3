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

## Render time (`render-time.txt`, `render-time-boot.txt`)

Whole service pass (snapshot + render + rectangle copies), TSC-measured, interrupts that land inside a pass
taken out (`max_gross_us` keeps them in; in every run the two were equal, so interrupts were not the cause of
the maxima). Idle boot with the sim cell: avg 160-176 µs, max 381-722 µs. Rotor turning at 60 % for 2 s with
the beam and the needle jumping (the T-14 scenario): **avg 215-240 µs, max 1,402-1,816 µs, 0 passes ≥ 2 ms in
8 of 13 runs**; the other 5 runs had one pass of 2,146-3,403 µs (`over_2ms=1`).

What those outliers are: the native cost of the most expensive region is about 12 µs (host benchmark of the
same code: rotor 10.6 µs, two banner strips 12.3 µs, needle 9.6 µs, beam 8.9 µs); the per-tick budget caps a
pass at the rotor plus the age line, or one banner strip, or the needle; the typical worst pass under TCG is
1.4-1.8 ms, and a 3.4 ms pass with the same work and no interrupt inside is the emulated CPU stalling (host /
hypervisor scheduling on WSL2), not drawing. **The test asserts `over_2ms == 0` as the card says and is
therefore flaky on this host (passes about 60 % of runs); it was not weakened.** Options for the lead: accept
the measured steady-state figure (max 1.4-1.8 ms) with the budget as the control, lower the budget further
(it cannot remove host stalls), or measure under `-icount` (deterministic virtual time, immune to host
stalls) in a second test. `GFX_TICK_BUDGET` is 66,000 pixel units (area drawn + area copied) per tick.

Text mode is unchanged: the T-08 / T-14 render-time tests still assert < 1 ms and pass.

## Sim-cell PNGs (A4)

`sim-boot.png`, `sim-idle.png`, `sim-presence.png`, `sim-noecho.png`, `sim-stale.png`, `sim-ask.png`,
`sim-fan60.png` (+ `-1..-3`), `sim-estop.png`, `sim-after-fault.png`; host renders `host-*.png`. No banned
words (INV-11 scans `kernel/ui/gfx.c` and the tests); original shapes, no franchise symbols or fonts.

## Checkpoint (2 h)

`make run-cell-gfx` composes the same QEMU line as `run-cell-screen` with `-vga std` and `-append "gfx=1"`;
the screen shows the real sim-cell data with readable text (the PNGs above are that screen, taken headless
through the same monitor). The real board was not attached in this session; the GTK window was not opened
here (no filming run), the command is in the Makefile for the user.

## Acceptance

| | Result | Evidence |
|---|---|---|
| A1 | text-mode default suite: see `pytest.txt` (this branch) ; policy hash unchanged; replays in the suite | `pytest.txt` |
| A2 | golden-image and fan tests pass; render: steady-state max 1.4-1.8 ms, outliers from host stalls, see above | `render-time.txt`, `host-*.png` |
| A3 | `fbpoke` / `bbpoke`: PROC_FAULT vector 14 at the window address, 0 PANIC; `-vga none` falls back to text | `sim-after-fault.png`, test transcript |
| A4 | sim-cell PNGs of every state; no banned words; original design | `sim-*.png` |
| A5 | worktree `gfx` only; not merged | this README |
