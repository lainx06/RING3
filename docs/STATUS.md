# Status

Updated: 2026-11-01 (lead, kickoff review). The builder updates this at the end of every card and at each T-01
milestone.

## Now
- Phase: **T-01 complete** (2026-11-01 13:30 ET). Kernel boots, ring-3 isolation slice works, **kill check
  K1–K4 passed at 13:22 ET** (well before the 9 PM deadline; Plan B not needed). 13 ktests, 5 pytest tests, ~6 s.
- Open from T-01: `.claude/settings.json` must be installed by the user (the builder session can't edit its own
  permissions).
- **T-02 complete** (2026-11-01 20:11 ET): executor lifecycle, hardened syscalls, pure-C policy with device rules,
  taint, confirmation, host build + decision match, permissive build. 15 pytest tests (~20 s), 16 ktests,
  593 host policy checks. Next: T-03a / T-03b (lead re-plans the evening of Nov 1).
- **T-02F complete** (2026-11-02): D-50 kernel findings F1, F3, F4, F5, F7, F8 fixed, each with a test that
  fails on the Nov 1 code. 20 pytest tests (~31 s), 17 ktests, 674 host checks (633 permissive); any
  `dropped > 0` or `PANIC` fails every QEMU run. `docs/evidence/T-02F/README.md`. T-03b / T-04 can build on
  `kernel/`.
- **T-01B complete** (2026-11-01 22:55 ET): real `ring3cell` firmware reaches the kernel as COM3 (QEMU third
  `-serial /dev/ttyACM0`, 0x3E8, **IRQ 4** shared with COM1). `make test-link` 3/3 back to back: 77 contiguous
  `STATE`, 8/8 `PING` acked, fan 0. First new-boot `STATE` ≈ 0.6 s after QEMU start; 18.9 Hz on the kernel clock
  (device clock 20.0 Hz). **T-04 notes:** stale pre-reset frames (sometimes the same block repeated) arrive after
  the open, so sync on the new boot (`t_ms`/`seq` restart); the reset follows QEMU's open, not COM3 init.
  `docs/evidence/T-01B/README.md`. usbipd/QEMU COM3 path: verified; D-20 fallback not needed.
- Second terminal Nov 1: **T-06** (Opus 5.5 / high) once T-01 has made its first commit (D-42).
- User, while T-01 runs: `SETUP.md` step 8 (usbipd + Uno + arduino-cli in Ubuntu), then wiring per
  `hardware/wiring.md` in the afternoon.
- Open: track fit (D-18) → ask in Discord. WSLg unverified (setup).
- Spend: $0 of the $30 project cap (estimate, not billing).

## T-01 progress (builder adds one line per milestone)
| Milestone | Time | State | Next / open bug |
|---|---|---|---|
| M1 | 2026-11-01 13:15 ET | Boot (Multiboot, `-kernel`), COM1 printk, GDT/IDT/TSS, PIC/PIT 100 Hz, CPL-0 panic dump + QEMU exit codes (33 pass / 35 fail / 37 panic), pytest harness `tests/qemu.py`, 3 boot tests green, ruff clean. `.claude/settings.json` NOT written (permission classifier blocks self-modification; proposed content handed to the user). | Step 8: frame allocator, paging (CR0.WP), ring-3 entry, syscalls, embedded programs, kill check K1–K4 |
| M2 (kill check) | 2026-11-01 13:22 ET | **K1–K4 PASS.** Bitmap frame allocator, paging with CR0.WP (16 MiB supervisor-only identity map), ring-3 entry via iret (IOPL 0), saved-context abandon path with EOI first, syscalls exit/cons_write/uread/uptime behind the ordered `uaccess` check. Transcript: hello (CPL 3, exit 0, data+bss ok), kwrite → PROC_FAULT 14, priv → PROC_FAULT 13, badptr 8/8 EFAULT, spin → LIMIT at 100 ticks, KERNEL ALIVE. `tests/test_isolation.py` green, evidence `docs/evidence/T-01/isolation.txt`. | Steps 9–11: kmalloc, CRC-16/COBS, ktests, evidence README, M3 |
| M3 | 2026-11-01 13:27 ET | kmalloc/kfree (headers, 16-byte alignment, coalescing, double-free + corruption panics), CRC-16/CCITT-FALSE, COBS encode/decode with bounds, 13 ktests (`make test-kernel`), `tests/test_ktest.py`, evidence files + `docs/evidence/T-01/README.md`. A1–A8 pass. Suite ~6 s. | Step 12 (skill vendoring) attempted next; T-02 next card |
| Step 12 | 2026-11-01 13:30 ET | Skills vendored byte-for-byte into `.claude/skills/` + `.agents/skills/` (+ `references/`), licences beside each, `THIRD_PARTY_NOTICES.md`, byte-check in `docs/evidence/T-01/skills-vendoring.txt`. No plugin, no hooks. | T-01 complete; next card T-02 |

## T-02 progress (builder adds one line per milestone)
| Milestone | Time | State | Next / open bug |
|---|---|---|---|
| Step 0 | 2026-11-01 19:45 ET | Skeptic pre-pass (read-only subagent): 14 findings, 5 fixed with tests (R5 `len == 0`, R3 PDE bits, latent DF-flag bug in `isr_common`, `badptr` rewritten to 18 cases incl. the mapped-to-unmapped gap, 5 new fault programs), 3 nits fixed (NMI/#DF/#MC never user faults, page 0 unmapped, trap-stack canary). `docs/evidence/T-02/skeptic-prepass.md`. | M1 |
| M1 | 2026-11-01 19:55 ET | Request lifecycle (`EXEC_START`/`EXEC_END` events, tick-driven action/request quotas via `decide()`, `exec_action_boundary`), full §3 syscall table through `uaccess` + `decide()`, bounded string copies, `TEST=1` image with `run`/`tele`/`tape` commands and quota boot options, 7 fault programs + DF test, `badptr` 18/18, fuzzer 10,000 calls 0 panics. | M2 |
| M2 | 2026-11-01 20:05 ET | Pure-C `kernel/policy/` (`decide()`, 88-byte state, 28-byte events, no globals/clock), capabilities by profile, 16-slot object store with `cell_spec` / planted `maint_notes` / `scratch_0..3` / bounded `audit_log`, taint, two-phase console confirmation (drain + prompt + tick timeout), audit ring + tape on COM1, `make host-policy` 593 checks. | M3 |
| M3 | 2026-11-01 20:11 ET | Device rules in `decide()` (D-47 fan 0 or 50–80 with first-step/step/rate, servo range/step/rate, presence incl. `0xFFFF`, stale, E-stop latch + console reset on fresh released telemetry, tick lease `AUTO_HALT` once per hazard), kernel-vs-host decision match on a scripted run (all 16 decision codes), permissive build labelled with a different hash. 15 pytest tests, ~20 s. A1–A9 pass. | Report; T-03a/T-03b next |

## Plan (see RING3_CONTEXT.md §5; v0.4 after the kickoff review, D-37, D-38, D-42)
| When (ET) | Card | Model | Goal |
|---|---|---|---|
| Nov 1 | T-01 | Fable 5.1 / high | Boot, faults, timer, harness (M1 2 PM) → **isolation slice (kill check 9 PM)** → memory, lib, ktests |
| Nov 1 (2nd terminal) | T-06 | Opus 5.5 / high | Wiring guide + firmware + bring-up; user wires (E-stop, sensor, servo, fan first) and flashes in the afternoon |
| Nov 1 eve | T-01B | Opus 5.5 / high | QEMU COM3 passthrough smoke test with an echo sketch |
| Nov 2 | T-02 | Fable 5.1 / high | Executor lifecycle + quotas, uaccess, object store, pure-C policy `decide()`, caps, taint, confirmation, host policy build. Checkpoint 9 PM |
| Nov 2 (2nd terminal) | T-03a | Opus 5.5 / high | Bridge host-only Python (framing, strict JSON, ledger, event log) |
| Nov 3 am | T-03b | Opus 5.5 / high | COM2 link, Muse Spark adapter, ACTION frames end to end, event tape, `agentsh` |
| Nov 3 midday | T-04 | Fable 5.1 / high | COM3 driver, cell syscalls, tick lease + AUTO_HALT, E-stop latch, end-to-end fan demo. Checkpoint 2 PM |
| Nov 3 pm | T-07 | Opus 5.5 / high | Gauge print + camera witness (only if the 2 PM checkpoint passed). Codex Gate A + insurance footage in the evening |
| Nov 4 am | T-05a | Opus 5.5 / high | Evals, replay from tape, tables. Gate B |
| Nov 4 12-4 PM | T-05 | (user + Opus) | Video, README, claims review; submit by 6 PM ET |

## Builder log
| Card | Started | Model / effort | Done | Evidence |
|---|---|---|---|---|
| T-01 | 2026-11-01 13:06 ET | Fable 5.1 / high | 2026-11-01 13:30 ET; K1–K4 pass 13:22 ET; A1–A8 pass | `docs/evidence/T-01/README.md` |
| T-02 | 2026-11-01 19:36 ET | Fable 5.1 / high | 2026-11-01 20:11 ET; A1–A9 pass; M3 before the 11:59 PM time-box | `docs/evidence/T-02/README.md` |
| T-02F | 2026-11-02 | Fable 5.1 / high | 2026-11-02; A1–A5 pass (A3: halt / fan(0) under pending and killing covered on the host build; the kernel cannot reach those states with a syscall, see the README); doc merge D-46..D-52 committed first | `docs/evidence/T-02F/README.md` |
| T-01B | 2026-11-01 22:35 ET | Opus 5.5 / high | 2026-11-01 22:55 ET; A1–A4 pass + step 5 (fan never moved); 1 test-program fix logged (stale pre-reset frames) | `docs/evidence/T-01B/README.md` |
| T-06 | 2026-11-01 16:58 ET | Opus 5.5 / high | **21:46 ET: A1 A2 A3 A6 A7 pass; A5 pass except the alarm outputs; A4 part 1 pass. Pending Nov 2 (parts arrive): LCD, buzzer, RGB wiring → `bringup` part 2 (A4) + link-loss alarm (A5.8).** Firmware `222d26d` flashed. Lead decisions D-46..D-50 applied; 10 test-program changes logged, none loosened an expectation | `docs/evidence/T-06/README.md` |

## Last evidence
`docs/evidence/T-01/` (isolation transcript, ktest, panic dump, build, pytest, toolchain, skills byte-check).
