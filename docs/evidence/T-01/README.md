# T-01 evidence

Builder: Fable 5.1 / high · 2026-11-01. All transcripts are COM1 captures from QEMU 8.2.2 (`qemu-command.txt`
holds the exact command; the pytest harness in `tests/qemu.py` writes `boot.txt`, `panic.txt`, `isolation.txt`
and `ktest.txt` on every run). Exit codes: 33 = pass, 35 = ktest failure, 37 = panic (`kernel/include/qemu.h`).

## Kill check (D-37)
| K | Check | Result | Evidence |
|---|---|---|---|
| K1 | `hello` makes a syscall from CPL 3 and exits 0 | PASS: `SYSCALL pid=1 nr=1 cpl=3`, `EXIT pid=1 code=0` | `isolation.txt` |
| K2 | `kwrite` → `PROC_FAULT vector=14`, kernel continues | PASS: `PROC_FAULT pid=2 vector=14 eip=0x40000040 err=0x7` then `RUN pid=3` | `isolation.txt` |
| K3 | `spin` → `LIMIT` with ticks advancing | PASS: `RUN pid=6 ... uptime=30`, `LIMIT pid=6 ticks=100`, `END ... uptime=130` | `isolation.txt` |
| K4 | `KERNEL ALIVE` afterwards, uptime > before `spin`, no `PANIC`, transcript test passes | PASS: `KERNEL ALIVE uptime=135`; `tests/test_isolation.py` green | `isolation.txt`, `pytest.txt` |

## Acceptance
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | `make` builds `build/kernel.elf`, zero warnings under `-Werror`; toolchain output saved | PASS | `build.txt`, `toolchain.txt` |
| A2 | `uv run pytest` passes; `uv run ruff check .` clean | PASS (5 tests) | `pytest.txt` |
| A3 | ktests: CRC `0x29B1`, COBS zero bytes / 253-254-255 runs / max frame, frame allocator, kmalloc reuse + double free, memory-map parsing | PASS (13 ktests) | `ktest.txt` |
| A4 | Deliberate CPL-0 fault prints vector, error code, EIP, CS, EFLAGS, CR2 and exits with the failure code; no triple fault | PASS: `vector=14 err=0x00000002 ... cr2=0xfffff000`, exit 37 | `panic.txt` |
| A5 | Isolation transcript in order: hello (CPL 3, exit 0, data+bss), kwrite 14, priv 13, badptr all `-EFAULT`, ticks after faults and after LIMIT, spin LIMIT, KERNEL ALIVE | PASS (8 badptr cases, see note) | `isolation.txt` |
| A6 | No `PANIC` in any run except the deliberate one | PASS: harness fails any run with an unexpected `PANIC`; the two `PANIC:` lines in `ktest.txt` are the double-free detections a test provokes and traps (KTEST builds only) | `pytest.txt`, `ktest.txt` |
| A7 | Default suite < 90 s | PASS: about 6 s wall time | `pytest.txt` |
| A8 | First commit ≥ 2026-10-31, `Builder:` trailer on every commit, LF endings, `tools/check-paths.sh` clean | PASS (checked before each commit) | `git log`, `tools/check-paths.sh` |

## Notes
- `badptr` covers the six cases in the card plus two guard-page cases. The "buffer that starts mapped and runs
  into the unmapped guard page" case is realised as a buffer in the top stack page that runs past `USER_TOP`
  (case 4), since the guard page sits below the stack and buffers grow upward; the guard page itself is case 7
  and a buffer starting in it is case 8.
- `uaccess_check` returns `-EFAULT` for every rejected range, including the length cap, so all cases are
  uniform; the order of checks is the one in D-41a.
- Paths in `build.txt` and `pytest.txt` have the repository root replaced by `<repo>` (A8).
- `.claude/settings.json` was not written: the Claude Code permission classifier refuses to let a session edit
  its own permissions. The verified content was handed to the user to install.
