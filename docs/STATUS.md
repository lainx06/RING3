# Status

Updated: 2026-11-01 (lead, kickoff review). The builder updates this at the end of every card and at each T-01
milestone.

## Now
- Phase: **setup done** (WSL, tools, Git identity, key, docs in `~/dev/RING3`, VS Code Remote-WSL, Claude Code,
  private GitHub repo `RING3`). Planning docs revised (D-37 to D-42). No commits yet.
- Next card: **T-01** (Fable 5.1 / high, new terminal). M1 by Nov 1 2 PM ET; **kill check K1–K4 by Nov 1 9 PM ET**
  (D-14, D-37). Plan B in D-37.
- Second terminal Nov 1: **T-06** (Opus 5.5 / high) once T-01 has made its first commit (D-42).
- User, while T-01 runs: `SETUP.md` step 8 (usbipd + Uno + arduino-cli in Ubuntu), then wiring per
  `hardware/wiring.md` in the afternoon.
- Open: track fit (D-18) → ask in Discord. usbipd/QEMU COM3 path unverified (T-01B). WSLg unverified (setup).
- Spend: $0 of the $30 project cap (estimate, not billing).

## T-01 progress (builder adds one line per milestone)
| Milestone | Time | State | Next / open bug |
|---|---|---|---|

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

## Last evidence
(none yet)
