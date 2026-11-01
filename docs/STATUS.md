# Status

Updated: 2026-11-01 (lead, kickoff review). The builder updates this at the end of every card and at each T-01
milestone.

## Now
- Phase: **T-01 complete** (2026-11-01 13:45 ET). Kernel boots, ring-3 isolation slice works, **kill check
  K1–K4 passed at 13:22 ET** (well before the 9 PM deadline; Plan B not needed). 13 ktests, 5 pytest tests, ~6 s.
- Open from T-01: `.claude/settings.json` must be installed by the user (the builder session can't edit its own
  permissions).
- Next card: **T-02** (Fable 5.1 / high).
- Second terminal Nov 1: **T-06** (Opus 5.5 / high) once T-01 has made its first commit (D-42).
- User, while T-01 runs: `SETUP.md` step 8 (usbipd + Uno + arduino-cli in Ubuntu), then wiring per
  `hardware/wiring.md` in the afternoon.
- Open: track fit (D-18) → ask in Discord. usbipd/QEMU COM3 path unverified (T-01B). WSLg unverified (setup).
- Spend: $0 of the $30 project cap (estimate, not billing).

## T-01 progress (builder adds one line per milestone)
| Milestone | Time | State | Next / open bug |
|---|---|---|---|
| M1 | 2026-11-01 13:15 ET | Boot (Multiboot, `-kernel`), COM1 printk, GDT/IDT/TSS, PIC/PIT 100 Hz, CPL-0 panic dump + QEMU exit codes (33 pass / 35 fail / 37 panic), pytest harness `tests/qemu.py`, 3 boot tests green, ruff clean. `.claude/settings.json` NOT written (permission classifier blocks self-modification; proposed content handed to the user). | Step 8: frame allocator, paging (CR0.WP), ring-3 entry, syscalls, embedded programs, kill check K1–K4 |
| M2 (kill check) | 2026-11-01 13:22 ET | **K1–K4 PASS.** Bitmap frame allocator, paging with CR0.WP (16 MiB supervisor-only identity map), ring-3 entry via iret (IOPL 0), saved-context abandon path with EOI first, syscalls exit/cons_write/uread/uptime behind the ordered `uaccess` check. Transcript: hello (CPL 3, exit 0, data+bss ok), kwrite → PROC_FAULT 14, priv → PROC_FAULT 13, badptr 8/8 EFAULT, spin → LIMIT at 100 ticks, KERNEL ALIVE. `tests/test_isolation.py` green, evidence `docs/evidence/T-01/isolation.txt`. | Steps 9–11: kmalloc, CRC-16/COBS, ktests, evidence README, M3 |
| M3 | 2026-11-01 13:27 ET | kmalloc/kfree (headers, 16-byte alignment, coalescing, double-free + corruption panics), CRC-16/CCITT-FALSE, COBS encode/decode with bounds, 13 ktests (`make test-kernel`), `tests/test_ktest.py`, evidence files + `docs/evidence/T-01/README.md`. A1–A8 pass. Suite ~6 s. | Step 12 (skill vendoring) attempted next; T-02 next card |
| Step 12 | 2026-11-01 13:45 ET | Skills vendored byte-for-byte into `.claude/skills/` + `.agents/skills/` (+ `references/`), licences beside each, `THIRD_PARTY_NOTICES.md`, byte-check in `docs/evidence/T-01/skills-vendoring.txt`. No plugin, no hooks. | T-01 complete; next card T-02 |

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
| T-01 | 2026-11-01 13:06 ET | Fable 5.1 / high | 2026-11-01 13:45 ET; K1–K4 pass 13:22 ET; A1–A8 pass | `docs/evidence/T-01/README.md` |

## Last evidence
`docs/evidence/T-01/` (isolation transcript, ktest, panic dump, build, pytest, toolchain, skills byte-check).
