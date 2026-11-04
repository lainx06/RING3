# T-09 evidence: Gate A fixes (Codex findings F1-F4, D-74)

Builder: Fable 5.1 / high, D-57 auto mode, 2026-11-03 evening. Offline: no paid call, no hardware, no key read,
no flashing. Reviewed head `5291cf3`; step 0 `fa7dda6` (card + D-73/D-74 rows); kernel fixes `fae44c4`
(F1, F4); bridge fixes `747fa2d` (F2, F3); this evidence `is the fourth commit`.

`decide()`, the policy state layout, `abi/` and `firmware/` are untouched (`policy-hash-and-replay.txt`:
`git diff --stat fa7dda6..747fa2d -- kernel/policy abi` is empty; policy SHA-256 still
`4101a8cc305078722afb56a4eb09e2b4c3705e2d2c697b418c7ef02885cdef0b`, the one in the T-04 bundles).

## The four findings and what changed
| # | Finding (Codex, `5291cf3`) | Fix | Test (fails before, passes after) |
|---|---|---|---|
| F1 BLOCKER | After a `y` gives CONFIRM_YES, `confirm_run()` returned with interrupts on; a COM3 STATE with `estop=1` taken in that gap latched the E-stop and sent FAN 0, then the syscall still sent FAN 60 on the stale decision | `kernel/core/confirm.c`: interrupts go off before the answer is fed to `decide()` and stay off; `confirm_run()` returns with them off, the syscall path writes the command and returns to ring 3 before anything re-enables them (`syscall.c` comment), the console's `estop reset` sends ESTOP_RESET inside the same section (`console.c`). A frame that would latch or halt is decided before the answer (the rules re-run at the answer's tick: DENY_ESTOP) or after the command, never between. Test seam (test image only): `confirmgap on` prints `confirmgap: waiting for COM3` after a CONFIRM_YES and holds, interrupts as the code left them, until a COM3 byte is waiting | `tests/test_t09_f1_confirm_atomic.py` |
| F2 BLOCKER | `HttpsTransport._scrub` replaced the key's raw bytes only; a key written with JSON escapes in the envelope or inside the embedded action JSON survived and reappeared after decoding (FINAL text, events, timeline) | `models/transport.py`: `Redactor` (the key's only other holder): the literal key in any letter case, and the key as `\u00XX` escapes (either hex case, all or some characters) inside any JSON string token, decoded and re-encoded at up to 3 nesting levels; untouched bytes stay byte for byte. The transport scrubs bodies with it; `MuseSparkClient.ask()` applies it to the decoded reply strings before returning. `run.py` untouched; the key never leaves `models/` | `tests/bridge/test_t09_f2_redaction.py` |
| F3 BLOCKER | Two processes (two runs, or a run and `calibrate`) each loaded the ledger and could both reserve past the project cap | `ledger.py`: `reserve()` = reload + cap check + append under an exclusive `flock` on `<ledger>.lock` next to the ledger; `settle()` takes the same lock and re-checks "already settled" on the reloaded state | `tests/bridge/test_t09_f3_ledger_lock.py` |
| F4 MAJOR | A COM3 stream of valid STATE frames alternating seq 1 / 10000 is a BOARD_BOOT every frame (synchronous FAN 0 + LCD + alarm); queued input kept `pump()` in interrupt context with interrupts off and stalled the policy tick | `kernel/drivers/device.c`: a per-tick receive budget (512 bytes, 8 policy-bound frames; a previous boot's frames cost bytes only), refilled by the tick, spent by the IRQ handler and the tick's own poll; once spent, `pump()` returns and the rest waits in the UART for the next IRQ/tick (`rx_deferred` on the `cell:` status line); the init drain is bounded too. 115200 baud delivers at most ~115 bytes (5 frames) per tick, so a real board never meets the budget | `tests/test_t09_f4_com3_flood.py` (two tests) |

## Before / after, per finding
"Before" = `fa7dda6` plus only the test seams (`confirmgap`, `device_rx_ready`), in a separate worktree
(`before/before-t09-kernel.txt`); the bridge tests ran on `fae44c4` with the three fixed modules stashed
(`before/before-t09-bridge.txt`). "After" = the same tests at `747fa2d` in a clean worktree (`after/`).

| Finding | Before | After |
|---|---|---|
| F1 | board saw **FAN 0 then FAN 60** (`assert [0, 60] == [60, 0]`): the stale CONFIRM_YES went out after the latch | board sees **FAN 60 then FAN 0**; tape: `CONFIRM_YES first_fan` seq 4 then `AUTO_HALT estop` seq 5, same tick; second phase (E-stop before the answer): `confirm DENY_ESTOP estop`, result -200, the latch's FAN 0 only |
| F2 | `validate(reply.text)` gave `Done(summary=<the key>)`: the escaped key came back whole after decoding | the key (any case, any fragment of its last 16 chars) appears nowhere in blobs raw or decoded two levels down, events, the returned reply, the validated Done/Refuse (`[REDACTED]`), or the 401's exception text; every reply blob holds `[REDACTED]`; chain verifies |
| F3 | both processes reserved: `[('a', 'reserved', 8200), ('b', 'reserved', 8200)]` against a 12,300 µUSD cap, 3 of 3 attempts | exactly one reserves, the other is refused `project_cap`, 3 of 3; a stale instance is refreshed under the lock; settle goes through it |
| F4a (alternating-seq flood, 3,000 boots) | **2,678 audit records dropped** (`assert (2678 == 0)`), the tick stalled while the pump looped | uptime 68 -> 219 ticks over 1.51 s of wall clock; 1,208 boots in 151 ticks = 8.0 per tick (the budget); `rx_deferred` 9 -> 453; `status` answered during the flood; FAN 0 only after the first boot; 0 dropped, no PANIC, exit 33 |
| F4b (junk flood with the fan on) | the tick-lease `AUTO_HALT stale` never came (timeout waiting for it): the tick was stalled | `AUTO_HALT stale` from the tick at tick 111, FAN 0 at the board within 1.5 s of the last frame; 3,000 chunks rejected and audited (`FRAME_DROP` x 3000), 0 dropped |

## Acceptance
| # | Result | Evidence |
|---|---|---|
| A1 | **pass**: F1-F4 each have a test that fails before and passes after (table above) | `before/`, `after/`, the four test files |
| A2 | **pass**: full default suite in a clean worktree at `747fa2d`: **779 passed, 6 skipped** (hw + 2 by design), 185 s; E1-E3 pass (`make evals --suites e1,e2,e3` on `747fa2d`: must-pass rows all hold, 0 kernel panics (E1 8 programs x 5 boots per build, E2 18 hand cases + 10,000 fuzz calls per build, E3 3 x 10,000 frames per build)); the three `replay/` bundles verify and `REPLAY MATCH`; policy hash unchanged | `pytest.txt`, `evals.txt`, `policy-hash-and-replay.txt` |
| A3 | **pass**: no FAN > 0 / SERVO frame after a latch or AUTO_HALT without a new decision: the F1 test (both phases), the F4 tests (FAN 0 only after the flood's first boot / the stale halt), and the existing INV-13/16 tests (28 passed on the fixed kernel, then the full suite) | `after/f1-confirm-atomic.txt`, `after/f4-*.txt`, `pytest.txt` |
| A4 | **pass**: four commits, only this card's paths (`docs/tasks/T-09.md`, `docs/DECISIONS.md` rows, `kernel/core/{confirm,console,syscall}.c`, `kernel/drivers/device.c`, `kernel/include/{confirm,device}.h`, `bridge/ring3bridge/{ledger.py,models/transport.py,models/muse_spark.py}`, `tests/`, `docs/evidence/T-09/`, `docs/STATUS.md`), `Builder: Fable 5.1/high` trailer on each | `git log fa7dda6..` |

## Test-program changes (none loosened)
- `tests/sim_cell.py`: two helpers, `emit_state()` (one STATE frame now, stream paused or not) and
  `write_fast()` (a flood); `_tick()` now writes through `_emit()`; behaviour of every existing test unchanged.
- `tests/test_t09_f4_com3_flood.py` reads the `cell:` status line with `rx_deferred` optional, so the same
  test runs on the pre-T-09 kernel (it then fails on the stall, not on the regex).
- No existing test, limit or check was changed. The `cell:` status line gained a trailing `rx_deferred=` field;
  the existing regexes end in `.*` and still match.

## Notes for the lead
- F1's remaining window is physical, not logical: a frame already on the wire when the answer is decided is
  processed after the command. The policy's own order holds: the command was decided on the state known at
  the answer's tick, and the frame's latch is decided and sent (FAN 0) right after. The firmware drops a FAN
  under its latch as before (D-50 F2); the kernel no longer relies on it.
- F4's budget is per tick, shared by the IRQ handler and the tick poll, rather than per call only: with a
  per-call budget the UART re-raises the IRQ as soon as its FIFO refills, so the main loop (console, audit
  flush) would still have been starved. The numbers (512 bytes, 8 frames) are above anything 115200 baud can
  deliver in 10 ms; the first `test_inv_13_hw.py` run after this change should confirm the boot resync timing
  on the real board is unchanged (expected: the stale pre-reset frames at open are previous-boot frames, which
  cost bytes only).
- F2's `Redactor` also matches the key case-insensitively (a key with its letter case changed is the same
  secret), at both layers.
- `make evals` rewrites `docs/evals/raw/*.json` and `results.md` for the measured commit; T-05a's committed
  results were restored after the run and the T-09 rerun's rendered file is kept here as `evals-results-747fa2d.md`.

## Real-board recheck (D-75, user, build `747fa2d`; files committed in T-11)
- `hw-recheck.txt`: `test_inv_13_hw.py`, interlock (+0 ms AUTO_HALT, board fan 0 +0 ms) and watchdog (fan off at
  most 50 ms after the last frame) pass; the E-stop test failed in that run.
- `hw-recheck-estop.txt`: the E-stop test alone, pass (AUTO_HALT +0 ms, reset refused while pressed, CONFIRM_YES
  after release).
- `hw-recheck-attempt-failed.txt` (saved as `hw-recheck.tx`, renamed in T-11; it differs from `hw-recheck.txt`): an
  earlier attempt, 3 failed. D-75: the setup, not the kernel (QEMUs left by failed tests sharing the Uno's port; a
  snapped motor lead). The harness fixes D-75 asked for (`HwCell` stops its QEMU when setup fails; prompts say the
  script answers) are in T-11.
- The `rootdir:` line of all three was replaced by `<repo>` (no absolute paths in committed files).
