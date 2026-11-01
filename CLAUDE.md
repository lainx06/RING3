# RING3

A small 32-bit x86 kernel written from scratch in C. Its shell takes plain-English requests: a remote AI model
(Meta Muse Spark) proposes one action at a time, a ring-3 executor (`agentsh`) performs it through system calls,
and the kernel decides every call (capabilities, taint, human confirmation, audit). Runs in QEMU and drives a
miniature work cell on an Arduino Uno through a kernel driver; a camera + SAM 3.1 witness checks real movement.

## Start every session
1. Read `docs/STATUS.md`, then your card `docs/tasks/T-xx.md`. Do only that card.
2. Look things up as needed: `docs/ARCHITECTURE.md` (design), `docs/INVARIANTS.md` (rules + tests),
   `docs/DECISIONS.md` (why).

## Commands (WSL2 Ubuntu, repo root)
```
make                      # build/kernel.elf (+ user programs)
make run                  # boot in QEMU, COM1 console on this terminal
make test-kernel          # in-kernel ktests, headless, exits via isa-debug-exit
make clean
uv sync                   # Python env for tests + bridge
uv run pytest             # default tests: boot QEMU headless, no network, no model calls
uv run ruff check . && uv run ruff format --check .
tools/check-toolchain.sh  # versions + freestanding i686 compile check
uv run ring3 ...          # bridge CLI (from T-03): link, run, spend, verify, replay, fuzz-link
```
Debug: `make run QEMU_EXTRA="-s -S"` then `gdb build/kernel.elf -ex "target remote :1234"`.

## Never (hard rules; docs/INVARIANTS.md)
- Never weaken an invariant, a check or a test to make something pass. Stop and report.
- Never let model output become code: no eval/exec/compile/subprocess on model text; operations come only from
  the allowlist.
- Never parse ACTION bodies or JSON in the kernel. Kernel = bounded frames only; ring 3 parses actions.
- Never run the executor (or any untrusted logic) in ring 0, even as a fallback (D-14).
- Never dereference a user pointer in the kernel without `uaccess` checks.
- Never open, print, hash or list `.env` / `.env.*`. The key is `MODEL_API_KEY`, read only in
  `bridge/ring3bridge/models/`; it never goes into logs, events, blobs, exceptions, fixtures or the screen.
- Never make a paid model call without a spend reservation and a user-set `--budget-usd`. Ask the user before the
  first paid call of a session.
- Never write "success", "secure", "safe" or "real-time" in user-facing text. Outcomes are
  DONE / REFUSED / STOPPED / ERROR plus the executed operations.
- Never guess a Meta model ID, endpoint or field: `config/models.toml` + official docs; else `[UNVERIFIED]`.
- Never copy code from tutorials, wikis or other repos (ATLAS, Radar, BENCHPROOF). Facts from docs are fine.
- Never flash the Arduino or run `arduino-cli upload`; the user does (`arduino-cli compile` is fine). Never let any process
  write COM3 directly or weaken the device limits/interlock (INV-13). The AI never writes firmware.
- Never push. Never install plugins, MCP servers or hooks. Say so in the report if you add a dependency.

## Where things live
| Path | What |
|---|---|
| `kernel/arch/i386/` | boot, GDT/IDT/TSS, ISR stubs, PIC/PIT, paging, ring-3 entry |
| `kernel/core/` | kmain, panic, printk, pmm, kmalloc, exec (executor lifecycle), syscall, uaccess, confirm, audit, objstore, agentlink, ktest |
| `kernel/policy/` | pure-C `decide()`: all allow/deny logic; also built for the host replay tool |
| `kernel/drivers/` | serial (COM1 console, COM2 link), device (COM3 Arduino), vga |
| `firmware/ring3cell/` | Uno work-cell firmware (hand-written, trusted; user flashes) · `hardware/` wiring + gauge |
| `kernel/lib/` | string, cobs, crc16 |
| `user/` | crt0, libu syscall wrappers, prog/ (init, agentsh, demo jobs), test/ (fault programs, fuzzer) |
| `bridge/ring3bridge/` | link, framing, models/ (only place the key is read), strictjson, ledger, eventlog, replay, fuzz, cli |
| `tests/` | QEMU harness, `test_inv_NN_*.py` |
| `config/` | models.toml (IDs, prices), budget.toml (project cap) |
| `docs/context-packs/` | versioned model prompt; SHA-256 logged per run |
| `docs/evidence/T-xx/` | evidence per card · `runs/` logs (git-ignored) · `replay/` bundles (committed) |

## How to work
- One card per session. Read Do / Don't / Acceptance first; plan briefly; small commits.
- Long cards: commit and add a line to `docs/STATUS.md` at each milestone so a fresh session can resume.
- Tests first for anything an invariant touches. Every QEMU test fails on an unexpected `PANIC`.
- Integers only in event records (ticks, ms, ns, bytes, µUSD).
- No user names or absolute paths in committed files or bundles.
- If you're stuck on one bug for 45 minutes, stop and report (the lead may switch models, D-10).
- Commit messages: `T-xx: <what>` + trailer `Builder: <model>/<effort>`.
- Two sessions at once only on disjoint directories; commit only your own paths (`git add <paths>`).
- Finish by updating `docs/STATUS.md` and reporting: acceptance table (A1..An pass/fail + evidence path),
  anything `[UNVERIFIED]`, anything surprising, and what the next card needs.

## Stop and report (don't work around) if
- A step needs an invariant weakened, a paid call outside the cap, or a push.
- Official docs (Intel SDM, QEMU, Meta) contradict `docs/ARCHITECTURE.md`.
- The toolchain or QEMU behaves differently from the card's assumptions.
