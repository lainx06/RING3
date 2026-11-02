# T-01B evidence: real ring3cell firmware → WSL (usbipd) → QEMU COM3

Builder: Opus 5.5 / high, 2026-11-01 (ET). Board: genuine Arduino UNO (2341:0043), `ring3cell` firmware `222d26d`
(flashed in T-06, not reflashed), module on (D-48). Only `PING` frames were sent by anything in this card; no
`FAN`, `SERVO`, `ESTOP_RESET`, `LCD` or `ALARM`.

## Acceptance
| | Result | Evidence |
|---|---|---|
| A1 `make test-link` 3 runs in a row with the real board: ≥ 20 valid `STATE`, `ack` advancing on `PING` | **PASS**: 3/3 back to back (exit 33), each with 77 contiguous valid `STATE` (0 gaps, 0 restarts, 0 UART errors) and 8/8 `PING` acked (`ack_seq` 0 → 1 … 8, each within 1–5 ticks) | `link-run-1.txt`, `link-run-2.txt`, `link-run-3.txt`; also `link-pytest.txt` |
| A2 reset-to-first-`STATE` time and kernel-observed `STATE` rate | **PASS** (recorded, see Measurements): first `STATE` of the new boot 615 / 669 / 616 ms after QEMU start; rate 18.9 Hz on the kernel clock (device clock 20.0 Hz) | `link-run-*.txt`, `link-pytest.txt` |
| A3 exact usbipd + QEMU commands and the COM3 IRQ | **PASS**: commands below; COM3 = `isa-serial` index 2, iobase 0x3e8, **IRQ 4** (shared with COM1, as D-39a expects) | this file, `qemu-qtree.txt` |
| A4 default `uv run pytest` unaffected; link test opt-in, marked `hw`, skipped without the board | **PASS**: `uv run pytest --ignore=tests/bridge` → 20 passed, 1 skipped (`tests/test_link_hw.py`: "set RING3_HW=1") in 31 s; with `RING3_HW=1` it passes against the board | `tests/test_link_hw.py`, `tests/conftest.py` (marker) |
| Step 5: the fan never moves | **PASS**: `fan_max=0` in every run (asserted in the kernel and in the test); servo stayed at 90 (park) in every `STATE` | `LINK SUMMARY` lines |

## Commands
Windows (usbipd-win). Observed state: `usbipd list` → `4-11  2341:0043  Arduino Uno (COM3)  Attached`. The bind and
attach were done by the user before this card, following D-20 / T-06. Their documented form (the exact
invocation the user typed was not observed by this session):
```
usbipd list
usbipd bind --busid 4-11                         # once, admin
usbipd attach --wsl --busid 4-11 --auto-attach   # window stays open (T-06)
```
Ubuntu (WSL2): the board is `/dev/ttyACM0` (`cdc_acm`, under `vhci_hcd`), then `sudo chmod a+rw /dev/ttyACM0`.
"COM3" in the Windows device name is a coincidence: it's the Windows port name (the one the D-20 fallback
`-serial COM3` would use), not the guest's COM3.

QEMU 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18), exactly as `make test-link` runs it (`CELL_PORT` overrides the port):
```
make KTEST_LINK=1        # build/kernel-link.elf
qemu-system-i386 -m 64 -display none -serial stdio -monitor none -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
  -no-reboot -serial null -serial /dev/ttyACM0 -kernel build/kernel-link.elf -append "linktest"
```
The third `-serial` is the guest's COM3 (0x3E8); COM2 is `null` until T-03b puts the bridge there. `make run-link`
is `make run` with the same two extra ports (for T-04). Opt-in pytest: `RING3_HW=1 uv run pytest tests/test_link_hw.py`
(`RING3_CELL_PORT` overrides the port).

## What the probe does
`kernel/drivers/linkprobe.c`, built only with `KTEST_LINK=1` (the production image has neither the probe nor the
codec, `nm` checked). COM3 polled: 115200 8N1, IER 0, FIFO on, MCR = DTR|RTS (no OUT2). Then:
1. wait ≤ 3 s (300 ticks) after COM3 init for the first valid `STATE` **of the new boot** (`t_ms` < 3000);
   earlier whole frames are logged as `STATE_STALE`, cut frames as `FRAME_DROP`; neither fails the run;
2. ≥ 40 valid `STATE` with contiguous `seq`, with nothing sent yet: `ack_seq` must stay 0;
3. 8 `PING` (seq 1–8) every 250 ms; each must show up as `STATE.ack_seq` within 300 ms;
4. fail if any `STATE` had `fan_duty` ≠ 0, the stream broke, or a `PING` wasn't acked; `isa-debug-exit` 33/35.

Decoding uses the kernel's own `cobs_decode` / `crc16_ccitt_false` and the layout from
`firmware/ring3cell/protocol.h`, included directly (plain macros, compiles under the kernel's freestanding
`-Wall -Wextra -Werror`), so there's no mirrored copy to drift; two `_Static_assert`s pin the layout assumptions.
Codec: `kernel/drivers/cellframe.c` (receiver rules in the protocol.md order). New ktests `cell_frame_vectors`
(both `STATE` vectors decode to the documented fields; `PING` and `FAN 40` encode byte-equal to
`firmware/test/link_vectors.txt`) and `cell_frame_rejects` (cobs, oversize, short, len, crc, type, body_len,
field incl. both reserved flag bits and `estop = 2`; command frames to the kernel are `type`). `make test-kernel`:
19/19 pass.

## Measurements
| Run | QEMU start → first new-boot `STATE` (host clock) | COM3 init → that `STATE` (kernel ticks, 10 ms) | first `STATE` | stale / cut frames before it |
|---|---|---|---|---|
| link-run-1 | 615 ms | 27 | seq 0, t_ms 16 | 4 / 2 |
| link-run-2 | 669 ms | 33 | seq 1, t_ms 66 (seq 0 was cut) | 1 / 2 |
| link-run-3 | 616 ms | 28 | seq 0, t_ms 16 | 1 / 1 |
| link-pytest | 611 ms | 27 | seq 0, t_ms 16 | 14 / 4 |

Host clock = timestamps on each COM1 line (`stamp` wrapper / the pytest reader), from the QEMU command line.
The kernel's PIT tracks host time (25 ticks = 249 ms; tick 52 printed at 617 ms after a 97 ms boot).

`STATE` rate over the 77-frame runs: **18.9 Hz on the kernel clock** (76 intervals in 402–403 ticks;
`rate_mhz=18905`/`18858`); the frames' own `t_ms` advance exactly 50 ms each (`dev_period_us=50000`, 20.0 Hz).
T-06 measured the same gap from the host with `cellctl` (19.1–19.4 Hz host clock vs 20.0 Hz device clock,
`docs/evidence/T-06/cellctl.txt`), so it's on the board side (its `millis()` clock or loop pacing), not the
kernel or QEMU. Cause [UNVERIFIED].

## Findings for T-04 (surprising, not on the card)
1. **Stale frames are delivered after the port opens, sometimes several times.** Before the new boot's first
   `STATE` the kernel receives whole `STATE` frames from the *previous* boot (`t_ms` = that boot's age, `ack` = the
   previous run's last `PING`), and in some runs the same block of 6 frames arrives 3 times (`link-pytest.txt`:
   seq 1847–1852 ×3; another run: 962–967 ×3). 6 frames ≈ 126 bytes, which suggests a ~128-byte buffer being
   delivered again somewhere on the 16U2 → USB/IP → `cdc_acm` path. Where exactly is [UNVERIFIED]. `cellctl`
   doesn't see this because it calls `tcflush` right after `open`. Consequence: the driver must not treat the
   first valid frame as live. It syncs on the new boot (`t_ms` small, `seq` restarting, as the probe does) and
   must tolerate `seq` going backwards before that sync.
2. **The reset follows QEMU opening the port, not the guest's UART programming.** With `linkdelay=0/100/200`
   (no COM3 access for 0/1/2 s) the stale frames' `t_ms` always match "previous boot running until this QEMU
   started", and every new boot starts at `seq 0, t_ms 16`. See `reset-experiment.txt`. Writing MCR = DTR|RTS
   once, as the host already holds them, caused no second reset in any run.
3. **First new-boot `STATE` ≈ 0.6 s after QEMU start, vs the ≈ 1.62 s T-06 measured from `cellctl`'s open.**
   Faster than the card assumed, so the 3 s wait has a wide margin. Why the bootloader phase is shorter under
   QEMU (when the DTR edge lands, or optiboot leaving early) is [UNVERIFIED]: `strace` isn't installed and this
   card didn't install it.
4. **If COM3 isn't read, the stream backs up (it isn't lost), and the kernel's view lags.** With
   `linkdelay=200`, 56 stale frames came out first, and the first `PING`'s ack showed up later than the 300 ms
   window (PINGs 2–8 then acked within 1–5 ticks; that run failed, as it should have). T-04's IRQ + per-tick
   drain (D-39a) avoids the backlog. Ack timeouts need to allow for any backlog left at startup.
5. Every run rejects the first 1–4 frames (`cobs`, once `len`): frames cut by the reset. That's expected and
   counts as `FRAME_DROP`; it isn't a link fault.
6. After the probe exits, the firmware's 1 s link watchdog sets `LINK_LOST` and (with the alarm parts wired)
   beeps 3 times with red blinking. That's designed behaviour and nothing moves (fan 0, `fan_cmd` forgotten).

## Test-program fixes
| # | What was wrong with the test | Change | Expectation unchanged? |
|---|---|---|---|
| 1 | Probe v1 (never committed) took the first valid `STATE` as the new boot. Run 1 got two whole frames from the *previous* boot (`t_ms 147616`, `ack 4` from the T-06 `cellctl` session) and failed on its own "`ack_seq` = 0 before any kernel frame" check. | Before the first new-boot frame (`t_ms` < 3000, the same rule as `cellctl`'s `BOOT_WINDOW_MS`), `STATE`s are logged as `STATE_STALE` and counted, and the run waits for the new boot (still ≤ 3 s from COM3 init). | Yes: ≥ 20 (40) contiguous valid `STATE`, `ack_seq` 0 before any `PING` and = each `PING`'s seq after, fan 0 all the time, 3 s first-`STATE` limit. All still hard checks; the stale frames are counted and reported, not hidden. |

## Files
| File | What |
|---|---|
| `link-run-1.txt` … `link-run-3.txt` | A1: three back-to-back `make test-link` transcripts, host-timestamped (repo path replaced by `<repo>`) |
| `link-pytest.txt` | `RING3_HW=1` pytest run, host-timestamped, exact QEMU argv |
| `qemu-qtree.txt` | A3: `info qtree` / `info chardev` with the board as the third serial port |
| `reset-experiment.txt` | Finding 2/4: `linkdelay=0/100/200` runs (LINK/stale/drop lines) |
