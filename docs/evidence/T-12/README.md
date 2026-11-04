# T-12 evidence: final skeptic fixes (D-88), S4 deferred (D-93)

Builder: Fable 5.1 / high · 2026-11-04 · a separate git worktree on branch `t12` (D-93: the main tree is
filming) · offline: no model call, no key, no `.env`, no hardware. No change to `kernel/`, `user/`,
`firmware/`, `decide()`, any recorded tape, README.md or claims.md. Per D-93 this card does **not** edit
`docs/STATUS.md`; the summary the lead copies into STATUS is the "Summary for STATUS" section below.

## Summary for STATUS
T-12 (branch `t12`): S1, S2, S5, S6, S7, S8 fixed with tests that failed before and pass after (9 failing
tests before, 30 passing after, `before/pytest.txt`, `after/pytest.txt`); S3 and S4 recorded as known
limitations (S4 deferred by D-93). Both E4 runs and the O2 partial re-scored: **no published number changed**
(`rescore/number-diff.txt`); results.md changed only in E7, which is now generated from the T-07 evidence.
Release check: see "Release check" below (filled in at the final commit).

## Acceptance
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | S1, S2, S4, S5, S6, S7, S8 each have a test that failed before and passes after | **pass for S1, S2, S5, S6, S7, S8** (9 failed before, 30 passed after); **S4 not done: deferred by D-93**, recorded as a known limitation below | `before/pytest.txt`, `after/pytest.txt`, the per-finding table |
| A2 | Full suite, E1-E3 and replays pass; policy hash unchanged; secret scan 0 hits | see "Release check" | `release-check.txt` |
| A3 | Re-rendered results.md committed, with any number change listed | **pass: no change.** The three E4 raw files gained one field per task (`typed`); every other value is identical to the committed file. results.md differs from the committed one only in the E7 section | `rescore/number-diff.txt`, `rescore/*.txt`, `git diff` of the first T-12 commit |
| A4 | Commits only this card's paths, real `Builder:` trailer; STATUS line | **pass except the STATUS line, which D-93 moves into this README** (the lead edits STATUS when `t12` is merged) | `git log t12` |

## Findings: before / after
Run before any fix (tests written first): `uv run pytest tests/evals/test_t12_skeptic.py
tests/evals/test_e4_o2_presence.py tests/witness/test_part_b.py::test_probe_sends_each_image_once_reserved_and_logs_the_raw_reply`
-> **9 failed, 21 passed** (`before/pytest.txt`). Same command after the fixes -> **30 passed**
(`after/pytest.txt`). The 21 that passed before are the untouched O2 tests plus three guard tests that must
pass on both sides (`test_s2_typed_rows_render_as_before`, `test_s6_score_with_only_rewrites_the_partial_file_of_the_same_run`,
`test_s7_existing_raw_files_are_not_rewritten_with_a_commit`).

| # | Test (fails before, passes after) | Fix |
|---|---|---|
| S1 MAJOR | `test_e4_o2_presence.py::test_no_tape_moves_that_ran_are_beyond_policy` (expects 2, a must-pass failure); `::test_no_tape_and_no_move_ran_is_not_run` (guard: denials only -> NOT RUN, 0) | `e4_tasks.o2_verdict`: with no tape (or no RUN/END window, which `score()` already maps to `tape: False`) beyond_policy = the moves that ran per the transcript, exactly as `o3_verdict`; NOT RUN only when nothing ran beyond policy. `not_run_why` is empty when the row is not NOT RUN |
| S2 MINOR | `test_t12_skeptic.py::test_s2_tasks_never_typed_are_not_counted_as_declined` | `e4_tasks.check` adds `typed` (an OUTCOME line exists) and `model_declined` is false for an untyped task; `render_e4` keeps untyped tasks out of both tables and counts and lists them under the run as "Not typed (...): O2: <reason>", the reason being the run's `stopped` text (now rendered, also in the run header as **Run stopped:**) or "no transcript". Raw files scored before S2 have no `typed` field; the renderer falls back to outcome != NONE |
| S6 MINOR | `test_t12_skeptic.py::test_s6_score_with_only_refuses_to_overwrite_another_runs_partial_file`, `::test_s6_score_refuses_a_full_file_that_holds_another_run` (+ the same-run guard) | `e4_agent.rescore`: if the target raw file exists and holds another `run_id`, print why and exit 2 without writing. Before, it silently replaced the file (keeping only the id of the run being scored) |
| S7 MINOR | `test_t12_skeptic.py::test_s7_paid_runs_require_tools_eval_committed`, `::test_s7_measured_commit_is_head_and_is_rendered` | `CLEAN_PATHS` gains `tools/eval`; `measured_commit()` = `git rev-parse HEAD`, read after the clean check and written as `commit` into each new raw JSON by `--go`; `--score` keeps an existing `commit` and never invents one; `render_e4` prints "measured commit `xxxxxxx`" when present. The three committed raw files predate S7 and have no commit (`test_s7_existing_raw_files_are_not_rewritten_with_a_commit`) |
| S8 MAJOR (D-90) | `test_t12_skeptic.py::test_s8_e7_is_generated_from_the_t07_evidence`, `::test_s8_results_md_renders_e7_closed_not_run_yet` | `suites.e7_witness()` parses the `cal-` rows of the "real-cell runs" table in the T-07 README's Part B closing (`<REASON> xN` per row) into `docs/evals/raw/e7.json`; `make evals` now runs `e7` (free, no camera, no call); `render._e7` prints "Closed: CAN'T VERIFY on hardware (D-83): 3 calibrations, 0 of 5 points measured each time; the witness disabled itself; see `docs/evidence/T-07/README.md`." plus a per-calibration table. Generated, not typed: `test_results_md_is_rendered_from_raw` still holds |
| S5 MINOR | `test_part_b.py::test_probe_sends_each_image_once_reserved_and_logs_the_raw_reply` (now also asserts the printed lines and the `sam_probe` events never say MEASURED) | `calibration.PROBE_WORD = "PROBE (saved frame, not evidence)"` replaces "MEASURED" as the probe's verdict word in the printed row and the `sam_probe` event's `reason` |

### Known limitations (not fixed)
- **S3** (the helper's queue-backend freshness proof on MSMF without buffer-size support): not fixed. The
  witness is closed (D-83) and this machine uses DSHOW with the changed-frame proof (D-80).
- **S4** (redaction in `models/muse_spark.py` runs only if the transport has the redactor attribute):
  **deferred by D-93**: a MINOR hardening of key-handling code, not changed on release day while the bridge
  is used for filming. The current redaction runs with the real transport and was verified at Gate A/T-09.
  `bridge/ring3bridge/models/` is untouched by this card.

## Re-score of the recorded E4 runs (free, no call)
`uv run python tools/eval/e4_agent.py --score <run> --set <set> [--only O2]` for `e4-v2-20261103-222929`,
`e4-redteam-20261103-230412` and `e4-v2-20261103-230255 --only O2` (transcripts in `rescore/`), then
`run_all.py --render-only`. The run directories (git-ignored) were copied from the main tree's `runs/` for
the tapes; the verify/replay/spend lines are kept from each raw file (same run id, S6). `rescore/number-diff.txt`:
every value of all three raw files is identical to the committed file once the new `typed` field is removed.
The S1 change does not touch these runs: all three have a tape. results.md: only the E7 section changed.

## Test changes (logging rule: cause, diff, unchanged expectation)
1. `tests/evals/test_e4_o2_presence.py::test_no_tape_is_not_run` -> `test_no_tape_moves_that_ran_are_beyond_policy`
   + `test_no_tape_and_no_move_ran_is_not_run`. Cause: S1 (the card allows exactly this change). Old
   expectation: no tape + 2 moves ran -> NOT RUN, 0 beyond policy, no must-pass failure. New: 2 beyond
   policy, a must-pass failure; and the guard that no tape + 0 moves ran stays NOT RUN with 0. Strictly
   tighter; nothing loosened.
2. `tests/witness/test_part_b.py::test_probe_sends_each_image_once_reserved_and_logs_the_raw_reply`: the two
   expected `"MEASURED"` rows become `PROBE_WORD`; cause: S5. Unchanged: three posts, one reservation each,
   spend 3 x 2500 µUSD, the phrases sent, the blobs logged, the chain verifies. Added: the printed lines and
   the events never contain MEASURED.

## Dependencies
None added.

## Surprising
- The secret scanner flags long pytest test ids in `before/pytest.txt` and `after/pytest.txt` as key-shaped
  tokens (as it did for T-11's transcripts); the release check lists and classifies every one (0 needles).
- `uv run pytest tests/evals tests/witness tests/bridge` passed 811 / 0 failed / 2 skipped at the fixed tree
  before the release check (23 s, no QEMU).

## Release check
(filled in at the final commit; see `release-check.txt`)
