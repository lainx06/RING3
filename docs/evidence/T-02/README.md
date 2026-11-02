# T-02 evidence

Builder: Fable 5.1 / high · 2026-11-01 (pulled forward, D-44; started 19:36 ET next to T-06). Transcripts are
COM1 captures from QEMU 8.2.2 written by the pytest harness (`tests/qemu.py`); `qemu-command.txt` holds the
exact command. Exit codes: 33 = pass, 35 = fail, 37 = panic. Paths in `build.txt` / `pytest.txt` have the
repository root replaced by `<repo>`.

## Acceptance
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | `make`, `make TEST=1`, `make KTEST=1`, `make POLICY=permissive`, `make host-policy` build with zero warnings under `-Werror` | PASS (0 warnings in `build.txt`) | `build.txt`, `toolchain` section |
| A2 | `uv run pytest` (15 tests) and `uv run ruff check .` / `ruff format --check .` pass; default suite < 120 s | PASS, about 20 s wall time | `pytest.txt` |
| A3 | Seven fault programs end with `PROC_FAULT` (null 14, kwrite 14, cli 13, hlt 13, ud2 6, div0 0, stack 14 in the guard page), the next request runs, uptime advances, 0 panics; `spin` → `LIMIT` action quota; `multi` (crosses action boundaries) → `LIMIT` request quota | PASS | `faults.txt`, `quotas.txt` |
| A4 | `badptr`: 18 cases (D-41 set + mapped-to-unmapped gap after `_mem_end` read and write, null, `len == 0`, `ptr == USER_TOP`, real wrap, length cap, three valid edges); `lcd`: unterminated / 17-char / unmapped / control-char strings → `-EINVAL` / `-EFAULT`; fuzzer 10,000 seeded calls, 0 panics, 0 bad results, uptime advancing | PASS | `badptr.txt`, `fuzz.txt` |
| A5 | Host `policy_test`: 593 checks (564 permissive), 0 failed: object table rows, syscall-capability rows, default deny, no escalation, taint forces confirmation and is not clearable, `cell_halt` always, every device rule incl. `0xFFFF`, stale, E-stop latch + reset-with-button-released, ramp, tick `AUTO_HALT`, and one test per R1–R8 | PASS | `policy-host.txt` |
| A6 | QEMU: `y`/`yes` allow, `n`/other/timeout deny, each audited (`CONFIRM_YES`/`CONFIRM_NO` with reason `answer` or `timeout`); a `y` typed before the prompt does not answer it (R10); a prompt is not killed by the action quota but is by the request quota (R2); taint demo: `maint_notes` read → `scratch_0` write prompts `[tainted]`, `audit_log` write still `-EACCES` | PASS | `confirm.txt`, `taint.txt`, `quotas.txt` |
| A7 | Scripted QEMU run (objects, strings, no-cap, taint, confirmations, device rules with injected telemetry, E-stop reset, bad frame, LIMIT, timeout, fault): every audited decision reproduced in order by `build/host/policy_test --replay` from the printed tape; all 16 decision codes exercised | PASS | `replay-match.txt` (kernel), `replay-host.txt` (host) |
| A8 | `make POLICY=permissive`: banner `policy: PERMISSIVE (EVAL ONLY) sha256=…` with a different hash; every audit line ends with `PERMISSIVE (EVAL ONLY)`; confirmations auto-answer yes | PASS | `permissive.txt` |
| A9 | Commits carry the `Builder:` trailer and touch only `kernel/`, `user/`, `tests/`, `Makefile`, `docs/evidence/T-02/`, the T-02 rows of `docs/STATUS.md`; `tools/check-paths.sh` clean | PASS | `git log`, `build.txt` |

## Skeptic pre-pass (step 0)
`skeptic-prepass.md`: 14 findings; the four rule gaps (R3, R5, badptr, INV-06 coverage) and the latent DF bug
were fixed in M1, each with a test.

## Test-program fixes made during the card (the expected outcome never changed)
| Program | What was wrong with the test | Expected outcome (unchanged) |
|---|---|---|
| `user/test/stack.c` | clang refused unconditional infinite recursion under `-Werror`; an unreachable depth bound (2^30) was added. | `#PF` vector 14 in the guard page (INV-06). |
| `user/test/objs.c` | The test read `scratch_1` back and then expected a plain `ALLOW` on the next scratch write; `scratch_*` is untrusted (ARCHITECTURE §3 table), so the read taints and the write correctly prompted. Reordered: the untainted write comes before the tainting read. | Scratch write `ALLOW`; untrusted read sets `tainted=1` (INV-15). |
| `user/test/lcd.c` | Pointed `cell_lcd` at the top 4 bytes of the (zeroed) stack and expected `-EFAULT`; the copy found a NUL first and the kernel correctly allowed an empty string. The test now writes four `'A'` bytes there so the copy really runs into `USER_TOP`. | `-EFAULT` for an unmapped page (R6). |
| `user/test/fan.c` | After the rule reorder (E-stop before ramp rules, decreases free; see below) the same-tick servo/fan commands hit the rate rule before the interlock, and the 105-tick wait exceeded the suite's 1 s action quota. The program now waits out the rate windows and its wait loop crosses action boundaries (the `agent_recv` mechanism, R2). | `-ESAFETY` for presence / no echo / E-stop; `-ENOSYS` for an allowed motion with no driver. |
| `tests/host/policy_test.c` | My synthetic sequences forgot that telemetry goes stale after 500 ms, that the action quota fires at +500 ticks, that an LCD `line` argument fails R5 first, that a hand in a telemetry sample auto-halts the fan before a "decrease" can be tested, and that the permissive variant allows every object rule (§7). | All as in ARCHITECTURE §3/§3a and D-47. |
| `tests/test_inv_05_confirm.py` | The prompt regex did not allow the ` [tainted]` marker ARCHITECTURE §3 puts before `. Allow?`. | Prompt text as specified. |

## Policy decisions made in this card (documented, not in the card text)
- **Rule order inside `decide()`** (R5, R3): argument ranges → `uaccess_ok` → capability → object rule → E-stop
  latch → static limits (range, step, rate) → stale → presence/no echo → confirmation → allow. The E-stop latch
  outranks the ramp rules so a latched stop is reported as `DENY_ESTOP`.
- **Fan decreases** (incl. 0) skip the step, rate, interlock and E-stop rules (D-47 "decreases always allowed",
  INV-16); a tainted decrease to a non-zero duty still needs confirmation (card step 4: "every motion
  (`cell_fan` > 0)"). An increase is relative to the commanded duty, which `AUTO_HALT` and `cell_halt` set to 0.
- **`AUTO_HALT` fires only while the fan is commanded on** (ARCHITECTURE §3a "while anything is moving"), plus
  once when the E-stop latch engages, so a boot without a device does not audit a halt per tick.
- **`estop reset`** goes through `decide()` as a console event; the typed line is the human's answer (no extra
  prompt). Refused with `DENY_ESTOP` on no / stale telemetry or a pressed button, `DENY_RULE not_latched` otherwise.
- **`uptime` and `exit` are not audited** (no authority involved; `EXEC_END` records the exit). Every other
  syscall, every `EXEC_START`/`EXEC_END`, every `LIMIT`, confirmation and lease decision is.
- **`LIMIT` appears twice** in the log for a killed request: the tick decision (reason `action_quota` /
  `request_quota`) and the `EXEC_END` boundary record.
- **Allowed `cell_*` calls return `-ENOSYS`** until T-04 adds the driver; the policy state still records the
  commanded duty/angle (the audit record's `result` shows `-38`).
- **Test images** (`make TEST=1`) embed the test programs, add `run`, `tele`, `tape on|off`, `panic` and the
  `taction= trequest= tconfirm=` boot options, and print `TAPE`/`AUDIT` lines on COM1. The production image has
  none of these: quotas are fixed in code (5 s / 60 s / 30 s). Test-only syscalls 100–102 (pattern write,
  action boundary, telemetry injection) exist only in test images.
- **Policy hash** = SHA-256 of `policy.h`, `policy.c` and the variant header actually compiled
  (`variant_default.h` or `variant_permissive.h`), so the permissive build hashes differently.
- **Tape pruning**: the kernel prints a `TAPE` line only for events that audit or change state; silent unchanged
  ticks are dropped losslessly (R2/R8), which is why the host replay reproduces every decision from the pruned tape.
- **COM1 output is paced by QEMU at the UART's baud rate**: 10,000 audited fuzz calls would take minutes to print,
  so the fuzz test turns the tape printing off (`tape off`); the `audit_log` object still receives every record.

## Not done / handed on
- `cell_status` returns `-ENOSYS` after the decision (T-04 fills the struct).
- Spurious-IRQ check and the serial THRE wait bound (skeptic nits 9, 13) left for T-03b/T-04.
- INV-11 banned-word scan of kernel strings is T-04/T-05's test; the policy's name tables are checked by
  `policy_test` (`names`).

## T-02F (Nov 2): kernel fixes from the Nov 1 skeptic review (D-50, D-52)
Full detail, acceptance table and transcripts: `docs/evidence/T-02F/README.md`. Each fix landed with a test
that fails on the Nov 1 code (recorded in `docs/evidence/T-02F/f*-before.txt`).
| F | Finding | Fix | Test |
|---|---|---|---|
| F1 | LIMIT tick in ring 0 (audit drain) only flagged the kill; the program resumed and could not be ended | `syscall_dispatch()` abandons a flagged kill before the `iret`; no blind `sti()` in syscall handling | `audspin` + `test_quota_kill_while_kernel_drains_audit` |
| F3 | halt / fan(0) behind the pending / killing gates; decreases skipped E-stop / interlock and prompted when tainted | `decide()`: in-request → halt / fan(0) (no cap, no prompt, pending preserved) → killing → pending → rest; non-zero decreases keep every gate but ramp and the taint prompt | host `F3_energy_reducing` |
| F4 | an answer in the timeout's wake-up returned 0 for a call that never ran | stray answer → `-ECANCELED`; no non-negative result for a non-executed decision; `confirmrace` knob | `test_answer_in_the_timeout_wakeup` |
| F5 | `line_start` never true; unbounded drain with `sti()` inside syscalls; a full ring dropped | CR LF fix; bounded polled drains (4 per syscall, 2 per tick from ring 3); synchronous drain instead of drop; `dropped > 0` fails every run | ktest `serial_line_start`, `test_audit_drains_at_line_start`, `test_irq_side_records_drained_per_tick` |
| F7 | telemetry `AUTO_HALT` audited target 0 (aliasing in `lease()`) | duty copied to a local first | host `F7_autohalt_target` |
| F8 | quota 0 meant unlimited | `EXEC_START` with a quota ≤ 0 → `DENY_RULE`; `RUN REFUSED` instead of a panic | host `F8_quota_arguments`, `test_zero_quota_is_refused` |

Two T-02 policy notes above are superseded: "a tainted decrease to a non-zero duty still needs confirmation"
(now never prompts, D-50) and "fan decreases skip the interlock and E-stop rules" (now only `fan(0)` and
`cell_halt` do; a non-zero decrease keeps them, D-52). The T-02 transcripts in this folder are from the T-02
commit; the suite rewrites them on every run.

