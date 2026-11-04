# T-05 evidence: README, claims ledger, video script and shot list

Builder: Opus 5.5 / high · 2026-11-03 night (parallel with T-11, disjoint paths) · no code, test or eval file
changed; no paid call; `.env` not read; nothing pushed. Lead additions taken mid-card: README title line, a typed
cold open (0:00-0:06) reusing the scene (b) take, and its claim (V0).

Files: `README.md`, `docs/claims/claims.md`, `docs/video/script.md`, `docs/video/shots.md`, and here `spend.txt`
(`ring3 spend`), `linecount.txt` (`wc -l`), `first-commit.txt` (`git log --reverse`), `banned-words.txt` (scan).

## Acceptance
| # | Criterion | Result | Evidence |
|---|---|---|---|
| A1 | README covers every bullet of item 1, plain English, no banned word | **PASS**: one-liner + D-16 description; "Why a new kernel" in 5 sentences with "research prototype, not a replacement operating system"; 3 real screenshots + captions + parts list; Mermaid diagram (request -> bridge -> Muse Spark -> ACTION frame -> agentsh -> syscall -> `decide()` -> COM3 -> Uno, console, event log/replay); INV 1-16 in plain words with test links; E1-E6 tables copied from `results.md` with sample sizes and raw links, E4 "Model declined" and "Kernel denied" as separate columns; T-10 counterfactuals labelled as host replays of a recorded tape; witness CAN'T VERIFY (D-83); Try it (no hardware/key; replay; cell + key + `--budget-usd`); Meta models + spend; limitations; built during the hackathon. `test_inv_11_banned_words` (already scans `README.md`) passes | `README.md`, `banned-words.txt` |
| A2 | Every number or result sentence in README and script is in `claims.md` with a real file reference | **PASS**: 83 README rows (R0-R82) and 16 script rows (V0-V15), plus the shot-list planning numbers (S1-S4). Every referenced file exists in the repo at `335481d` (`runs/` items are cited only through committed copies) | `docs/claims/claims.md` |
| A3 | Script <= 180 s with timecodes; first 20 s answer "why not Linux + seccomp" | **PASS**: 12 scenes, 0:00-3:00 = 180 s; cold open 0:00-0:06, seccomp answer 0:06-0:20; narration 1.7-2.5 words/s; every caption <= 8 words (6 max) | `docs/video/script.md` |
| A4 | Shot list: every scene has commands, physical steps, expected screen, budget, fallback; total <= $1.50 | **PASS**: 10 shot entries (C0 + S3 one take, S4 + S5 one take); 5 paid takes; caps $0.38 per pass, $1.14 with two retakes each; hard stop at $1.50 measured with `ring3 spend`; bridge first, `pkill -f qemu-system-i386` before each | `docs/video/shots.md` |
| A5 | Commits only this card's paths, real `Builder:` trailer | **PASS**: `git add README.md docs/claims docs/video docs/evidence/T-05` and later `docs/STATUS.md` only | `git log` |

## Banned words and scrub (item 5)
`tests/test_inv_11_banned_words.py` reads a fixed tuple (`TEXT_FILES = ("README.md",)`), not a config file, so its
coverage was **not** extended (card: only if it reads file lists from config). It already scans the README and
passes. `docs/video/` and `docs/claims/` were scanned by hand with the test's own regex plus a scrub pattern
(home paths, user names, banned-topic words from `RING3_CONTEXT.md` §1). Output: `banned-words.txt`, 0 hits.

## Claims cut or rewritten for lack of proof
1. "Agents today usually run with all of the user's permissions" (CTX impact framing): no file proves it; cut
   from the README and the script.
2. "seccomp can't ask a human": seccomp's user-notification mode can hand a call to a supervisor process, so the
   claim is narrowed to what a seccomp filter sees (system call numbers and arguments).
3. D-86 says "O3 E-stop clear attempted"; the raw file shows the model sent `fan_set(60)` under the latch (there
   is no reset operation). README and script say that.
4. Card item (f) "board drops to fan 0 by itself (<= 900 ms)" for the QEMU kill: 900 ms was measured with
   `cellmute` (kernel silent); the kill was confirmed by eye only (E6). Narration says "in our recorded test";
   the shot list forbids putting a time on the kill take.
5. Card item (i) "0 beyond policy over 18 rows": 6 of the 18 rows are benign and have no beyond-policy column;
   written as "18 tasks, 12 of them over-reaching: 0 operations beyond policy", with the first O2 attempt shown as
   NOT RUN and replaced by its re-run.
6. "60,000 bad frames, 0 panics" (draft) counted the permissive build: now 30,000 on the RING3 build. "40 broken
   programs" (draft) is 8 programs x 5 boots: now "40 crashing program runs".
7. "16x2 LCD": no file names the size; now "two-line LCD".
8. "The model never sees the console": not tested as such; replaced by "the bridge has no way to answer" (INV-05
   test).
9. Interlock "in the same tick" for the filmed take: only the recorded test is timed (E6); the filmed narration
   gives no number.
10. Beep count "3 beeps" (the T-09 test prompt): T-06 records 2 or 3, unconfirmed; not stated.
11. Gemini "reviewed the claims": not yet done; written as "is the claims reviewer (D-11)".
12. Diagram draft had the kernel writing the event log; the bridge writes it from the kernel's tape: fixed.
13. Test-suite totals (e.g. 779 passed at `747fa2d`) left out: the release count is T-11's.

No claim the brief asks for was left without an evidence file.

## Notes for the lead
- **Line counts:** D-33 says `cloc`; `cloc` isn't installed and the card forbids adding dependencies, so the README
  gives `wc -l` counts with the method stated (`linecount.txt`). Replace them or cut them if you prefer.
- **Untested wording in the shot list:** the cold-open request `RING3, set the fan to 100 %` and `set the fan to 60 %`
  (S6, S7) were not run in this exact form. Close recorded requests exist and are named as fallbacks.
- **Not in the record:** the S5 interlock with a card while the fan runs. The recorded proof is E6 (a hand).
  S6's request 1 under the red-team pack is new; O3 (request 2) is recorded.
- T-11 added `LICENSE` (MIT) while this card ran; the README links it and says nothing more.
- `results.md` did not change after T-11's O3 re-score. The raw red-team file now records `estop_latched_at_start:
  true` for O3, cited in claims R44.
