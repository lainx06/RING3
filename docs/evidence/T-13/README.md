# T-13 evidence: final docs pass (items 1-4)

Builder: Opus 5.5 / high, main working tree (D-93). Docs only: `README.md`, `docs/video/script.md`,
`docs/claims/claims.md`, `docs/INVARIANTS.md` (the two lead-authorised wording edits). No code, tests, `tools/` or
`docs/evals/` touched; no paid call, no hardware. Items 1-4: `00e3f10`; S9 caption: `b7d5f37`; step 5 (after T-12 and T-14 were merged,
main at `26780eb`): the commit after it, which also brings `docs/video/shots.md` up to the T-14 screen words and the
STATUS rows for T-12 and T-14 (lead's instructions).

## Acceptance
| # | Result | Evidence |
|---|---|---|
| A1 | **PASS for items 1-3**: every item applied (tables below); script still 180 s with timecodes, every scene at most 2.5 words/s, every caption at most 8 words | this file; `docs/video/script.md` |
| A2 | **PASS at `26780eb`** (main after T-12's release commit and T-14): every claims.md line reference re-checked; 7 references pointed at the wrong lines (pre-existing, not moved by T-12) and are fixed; header names `26780eb` | step 5 below; `docs/claims/claims.md` |
| A3 | **PASS at the step 5 commit**: `test_inv_11_banned_words` 2 passed; manual whole-word scan of `docs/video/`, `docs/claims/`, `README.md` 0 hits; no franchise, military, medical or political framing added | scan output below |
| A4 | **PASS**: commit adds only this card's paths, `Builder:` trailer; STATUS line added | `git show --stat` |

## Item 1: README and script accuracy (D-90, D-91)
| Where | Before | After |
|---|---|---|
| README rule 10 | "Every run is logged in a hash chain and can be replayed decision by decision." | "Every run started with the bridge attached first (D-70) is logged in a hash chain and can be replayed decision by decision: 8 of 8 in E5." |
| Script S8 | "... No key, no network. Every decision matches." | "... No key, no network. In all eight replayable runs, every decision matches." (+ narration note: two early runs are not replayable, D-70) |
| README E2 intro | "18 hand-written bad-pointer cases and 10,000 random system calls (one fixed seed) per build." | "18 hand-written pointer-boundary cases (15 bad pointers, 3 valid edge cases) and 10,000 random system calls (one fixed seed) per build." |
| README witness | "it proves each camera frame was taken after the request (an older frame is refused), and every unusable image ends in CAN'T VERIFY with a named reason, never in PASS or FAIL." | "it checks that each camera frame was taken after the request (an older frame is refused), and in our tests and runs every unusable image ended in CAN'T VERIFY with a named reason, never in PASS or FAIL." |
| Script S7 | "Now I kill the whole virtual machine mid-task. The board notices the silence and stops the fan by itself. In our recorded test, within 0.9 seconds." | "Now I kill the virtual machine mid-task. The board notices the silence and stops the fan by itself. In a separate recorded test, with the kernel muted, it stopped within 0.9 seconds." (narration note rewritten to match) |
| Script S9 | "Our camera check with SAM 3.1 didn't reliably find the gauge arm on this setup. So it says CAN'T VERIFY, and never guesses." | "On this setup, SAM 3.1 didn't reliably find the gauge arm. So the camera check reports CAN'T VERIFY instead of a pass or fail." ("it" made explicit because the subject moved to SAM; shortened to stay at 2.4 words/s) |
| Script S10 | "Forty crashing program runs, ..." | "Forty faulty-program runs, ..." (narration note: 35 crashes from 7 programs + 5 endless loops from `spin`) |
| claims.md | no rows for `int 0x80` or COBS + CRC-16 | R3a (`int 0x80`, INV-3: `idt.c:40`, `isr.S:72`, `crt0.S:16`) and R3b (COBS + CRC-16, INV-2: `agentlink.c:236`, `:254`, `abi/link_abi.h:30-32`) |

| Script S9 caption (lead, after items 1-4) | "Camera witness: CAN'T VERIFY, by design" | "Unsure? It says CAN'T VERIFY" (5 words; credits only the refusal, not SAM's misses); claims V11 updated |

## Item 2: INVARIANTS.md (lead-authorised, the only two changes)
| Rule | Before | After |
|---|---|---|
| INV-13 (line 20) | "goes to the safe state on its hardware watchdog" | "goes to its halt state (fan 0) on its hardware watchdog" |
| INV-7 (line 14) | "(120 s wall clock, D-58, prov.)" | "(120 s wall clock, D-58)" |

Not changed (outside the authorisation; the lead confirmed the test name stays, a rename is out of scope): the test name `test_inv_13_watchdog_safe_state` in the INV-13 row, and
INV-11, which quotes the banned words as the rule itself. Line count of INVARIANTS unchanged, so claims.md's
"rule n is on line 7+n" still holds.

## Item 3: plain English (D-91)
**README intro.** Before: "RING3 is a hobby kernel with a hardware-isolated executor for actions proposed by a remote
AI model. ... What runs in ring 3 (the CPU's user mode) is the executor, `agentsh`, which turns each proposal into
ordinary system calls. JSON is handled only by a host-side bridge program; the kernel only moves bounded,
CRC-checked binary frames; ..." After: a plain paragraph (you type a request; Muse Spark proposes the steps on
Meta's servers; the unprivileged helper `agentsh` receives one proposed action at a time and asks the kernel to
carry it out; the bridge handles the JSON and sends RING3 small, size-limited, checksummed messages; the kernel
allows, refuses or asks the human), then one sentence "For OS readers" (ring 3, `int 0x80` system calls with
page-by-page pointer checks, bounded COBS frames with a CRC-16 on COM2, ACTION body parsed in ring 3).

Adapted from the card's basis for accuracy: the card says the bridge sends "small, fixed-size" messages. Only the
ACTION body is fixed (16 bytes); the bridge also sends TEXT (at most 65 bytes), FINAL (at most 201) and STOP (1)
frames (`abi/link_abi.h` lines 61-68). The README therefore says "small, size-limited, checksummed messages (each
action is a fixed 16-byte record)".

**README "Why a new kernel".** Before: one dense paragraph ("Linux with seccomp isolates processes and filters
system calls, but the rules that matter ... are not process permissions ..."). After: seccomp filters individual
system calls and sees each call's number and arguments; it doesn't track what the agent did during a task and
can't ask a human by itself without a separate supervisor process (T-05's corrected scope kept); the three example
rules as a list; then RING3's answer (fixed capabilities nothing can raise; taint; from then on every move that adds
energy and every write needs a human "y"; physical rules re-checked every 10 ms on each timer tick even between
commands; `decide()` in one place; replay conditioned on bridge-first). The research-prototype sentence is kept.

**Script first 30 s** (cold open unchanged: typed `RING3, set the fan to 100 %`, D-91; the shot list's plain-wording
fallback is unchanged):
| Scene | Before | After |
|---|---|---|
| S0 (14 s) | "A seccomp filter on Linux sees system call numbers and arguments. It can't know the agent just read an untrusted note and ask a human first. RING3 makes that kernel policy, below the model." | "On Linux, a seccomp filter checks each system call on its own. It can't know the agent just read an untrusted note. RING3's kernel tracks that, and makes a human say yes first." (seccomp named once) |
| S1 (12 s) | "Meta's Muse Spark runs on Meta's servers. It proposes one action at a time. A user-mode program carries it out with system calls. The kernel decides every call." Caption: "Model proposes. Kernel decides." | "The AI, Meta's Muse Spark, runs on Meta's servers. It suggests one action at a time. A helper program inside RING3 asks the kernel. The kernel decides." Caption: "Ring-3 helper, system calls. Kernel decides." (the precise term moved to the caption) |

"Safety" kept where it names rules (D-33); no use of the banned "safe" as a claim.

**Script timing after the edits** (words / seconds, caption words):
```
C0  6 s 10 w 1.67/s cap 6     S5  12 s 25 w 2.08/s cap 6
S0 14 s 33 w 2.36/s cap 5     S6  23 s 39 w 1.70/s cap 6
S1 12 s 27 w 2.25/s cap 6     S7  15 s 32 w 2.13/s cap 6
S2 18 s 36 w 2.00/s cap 6     S8  15 s 31 w 2.07/s cap 5
S3 15 s 32 w 2.13/s cap 6     S9  10 s 24 w 2.40/s cap 6
S4 20 s 50 w 2.50/s cap 6     S10 20 s 37 w 1.85/s cap 5
total 180 s
```

## Item 4: claims.md
Rows reworded or added, each marked "(T-13)" in the ledger: R2, R3, **R3a** (new, `int 0x80`), **R3b** (new,
COBS + CRC-16), **R4a** (new, seccomp scope; framing, sourced to T-05's correction), R5, R6, R7, R9 (bridge-first
condition, E5 8 of 8), **R32a** (new, rule 10 wording), R37, R59, R60, V1, V2, V3, V9, V10, V11, V13. The header
says the line references are re-verified in step 5. Every newly cited line was checked against the tree at
`bf86664` (DECISIONS lines 39, 79, 95, 105; T-07 README 404-406 and 434; T-05 README 28-29; results.md lines 26
and 138; `idt.c` 39-40; `isr.S` 72; `crt0.S` 16; `link_abi.h` 30-32 and 61-68).

## Scan output (re-run at the step 5 commit)
```
$ uv run pytest -q tests/test_inv_11_banned_words.py
..                                                                       [100%]
2 passed in 2.32s
$ grep -rniwE "success|secure|safe|real-time|realtime" docs/video docs/claims README.md
(no output, exit 1)
```

## Step 5 (at `26780eb`, after T-12 and T-14 were merged)
**Line counts** (T-05's commands, `docs/evidence/T-05/linecount.txt`): kernel 6,827 -> **7,019** (T-14's
`kernel/ui/` panel), bridge 5,643 -> **5,648**; policy 883, user 1,268, firmware 884 unchanged. README
"Built during the hackathon" and claims R80 updated; the intro's "883 lines" is unchanged.

**results.md** (T-12, S8) changed only in place on lines 9, 31, 49 (measured-build commit `e8de3f2` -> `985f0d5`)
and in the E7 section at the end (from line 187), so no cited line moved and no E1-E6 number changed. Every
README evidence table row is found verbatim in results.md (script check: 0 rows missing). Raw E4 fields cited by
R41-R52 re-read from the JSON at HEAD: unchanged (settled 48529 / 92091 / 8358 µUSD, CHAIN/REPLAY strings, O1
`fan_set` 100 -> -200, O2 dist 51 and 183 presence frames, O3 latched at start and after, O5 `prompted` 1, O2
re-run 217 presence frames).

**Every line reference checked** (DECISIONS: 22 `D-xx (line n)` references + the 7 "DEC lines" in R82, all
correct; INVARIANTS rule n on line 7+n; results.md; ARCHITECTURE; policy.h; policy.c; words.c; models.toml;
RING3_CONTEXT; wiring.md; context pack; E4 transcripts; T-04/T-06/T-07/T-09/T-10/T-05 evidence; Makefile;
THIRD_PARTY_NOTICES; tests by line and by test name). Seven were wrong; all were already wrong at `335481d`
(the lines they cite did not move since), so T-05's ledger had them:
| Row | Was | Now | Why |
|---|---|---|---|
| R0, R2 | ARCH §1 lines 23-24 | lines 20-21 | 23-24 are the §2 heading; the quote is on 20-21 |
| R20 | `agent-v2.md` line 3 | lines 3-4 | "two-line LCD panel" is on line 4 |
| R39, R73 | R line 65 | R line 66 | line 65 is blank; "n = 1 per row" is on 66 |
| R66 | `agent-v2.md` lines 25-28 | lines 25-29 | the `refuse` reply kind is on line 29 |
| V5 | `bridge-timeline-3.txt` lines 13-14 | lines 16-17 | 13-14 are `cell_status`; `fan_set(duty=100)` and `DENY_LIMIT (fan_range)` are 16-17 |

**Lead's extras in the same commit:** `docs/video/shots.md` "Expected screen" lines now use the T-14 panel words
(`kernel/ui/screen.c` lines 399-404, 483, 523-527, 529-530; console box 5 lines, `screen.h` line 14): S0 idle panel
(`STOPPED`, `CLEAR`, `E-STOP RELEASED`, `LINK ok`); S2 console box keeps 5 lines, so film T2 for the full action
list, panel `TURNING` / `STOPPED`; C0/S3 panel stays `STOPPED`; S4 `TURNING` after `y`; S5 `PRESENCE YES` ->
`PRESENCE`, `STOPPED`; S6 `E-STOP PRESSED, LATCHED` then `E-STOP LATCHED`, fan `HALTED (E-STOP)`, after the reset
`E-STOP RELEASED` (was `E-STOP released`); S7 `TURNING` before the kill, the panel goes with the window. The reason
word `E-STOP latched` in the decision box is unchanged (`words.c` line 48). Claims row S5 (shots) added for these.
STATUS: T-12 row (from T-12's "Summary for STATUS" + D-95: estop-latch test wait, 20/20; full suite 887 passed),
T-14 row "merged at `26780eb`, 913 passed", T-13 rows.

**Scan (step 5 commit):** see the "Scan output" block above, re-run at this commit with the same result.

## Step 6 (queued by the lead; after filming)
Replace the README's three D-81 screenshots ("What it looks like": `docs/evidence/T-08/real-{ask,estop,idle}.png`
and their `.txt`) with the new T-14 screen shots from the filming run; the lead gives the file names. Update the
captions and claims rows R11-R14 to the new files and run id, and re-run the banned-word test and scan.

## Codex Gate B findings (lead, D-96)
| Finding | Before | After | Source |
|---|---|---|---|
| (2) README rule 13 | "Physical limits are enforced twice, by the kernel and by the firmware: ranges, ramp rates, presence interlock (stale or missing data counts as presence), E-stop latch." | Kernel: ranges, fan and servo step and rate limits, presence interlock (stale or missing data counts as presence), E-stop latch. Firmware, independently: clamps (servo 15-165°, fan at most 80 %), fan cut on presence (< 100 mm, or 3 missing echoes in a row while it runs), fan cut and latch in the E-stop interrupt, fan 0 and servo 90 after 1 s of kernel silence, hardware watchdog resets a stuck board, which boots with the fan off. The firmware has no step or rate limit, so "twice" no longer covers them | `firmware/ring3cell/ring3cell.ino` lines 6-16, 62-68, 292-297, 310-318, 518-527, 585, 609; `protocol.h` lines 78-86; claims R32b |
| (3) README "Why a new kernel" | "the fan may rise at most 20 points per step" | adds "(its first start from 0 may go straight to 50-60 %)" | `policy.h` lines 76, 78, 79; `policy.c` lines 234, 306; claims R4 |

The rule-13 scope fix is README-only: INV-13 in `docs/INVARIANTS.md` already lists the step and rate limits under
the kernel driver and the clamps, E-stop interrupt and watchdogs under the firmware, so it was not edited.
Banned-word test re-run after these edits: 2 passed; manual scan of `docs/video/`, `docs/claims/`, `README.md`: 0
hits.

## Step 7: the lead's founder-voice narration, with the lead's decisions (main at `21757f6`)
Builder: Sonnet 5.5 / high, main tree, docs only (`README.md`, `docs/video/`, `docs/claims/`, `docs/evidence/T-05/linecount.txt`,
this file, STATUS). No code, tests, `tools/`, `docs/evals/` or `docs/evidence/T-20/` touched; no paid call, no hardware.
Banned-word test: 2 passed; whole-word scan of `docs/video`, `docs/claims`, `README.md`: 0 hits; ruff clean.

**Script.** The lead's table, re-timed: 0:00-2:59 = 179 s, every scene at most 2.5 words/s (the draft had two scenes at
3.4 and 3.1). Changes to the lead's text, each listed in `script.md`: "I wrote" -> "built" (R81); S4 says the replies are
recorded and the taint and confirmation checks are off (a host replay on a simulated cell, fan 100 % still denied, D-99);
replay line scoped to "all eight replayable runs"; "forty faulty-program runs"; the lead's decisions: C0 "I built the part
that tells it no" kept, camera line "that's not where it is", self-test line "every step checked by the kernel, and I confirm
the first fan start" (timing S5 1:40-1:57, S6 1:57-2:07, S7 2:07-2:33 so S7 stays at 2.46 words/s).

**shots.md** rewritten for C0, S0-S9: spoken cold open on the red-team pack (`heard:` line and Enter in frame;
`run-cell-console` shows the text screen, the Makefile has no `gfx=1` for it, so the graphics screen is S0 and the other
takes), `make run-cell-gfx`, the permissive replay (caption "Recorded replies, simulated cell, permissive build"), the
recorded loose-wire zone check, "never speak / no voice input" removed. Budget: caps sum to $0.33; the ledgers are per
checkout, so the voice and vision spend is not in the main ledger.

**README** (new sections, each a claims row R83-R100): camera witness as measured (two backends; vision 2/6, 1/3, 3/6, 3/6;
21 images, 9 PASS, 10 FAIL, 2 CAN'T VERIFY, no false PASS; MID 8/8, LOW 2/5, HIGH 0/6; 4 of the 10 FAILs after the wire fix;
D-102 loose wire); spoken requests with the host-side guards and the one live request; the graphics screen; the
counterfactual; "Try it" commands; Meta models list (vision, voice); limitations (witness off, voice one request,
counterfactual is a replay); spend over three ledgers. Spend (`uv run ring3 spend`, read in each checkout, offline):
main $0.470496 (20 runs) + `~/dev/RING3-t16` $0.086560 (4 runs, 21 requests) + `~/dev/RING3-voice` $0.013592 (2 runs) =
**$0.570648 over 26 runs**. Line counts at `21757f6` (kernel 8,454, user 1,292, bridge 8,015; policy 883 and firmware 884
unchanged) in `docs/evidence/T-05/linecount.txt`, README "Built during the hackathon" and R80. Step 6 (screenshots) is not
in the lead's list and is still waiting for file names.

**Claims re-pinned to `21757f6`.** Of the files the ledger cites by line, only the `Makefile` changed since `26780eb` (R62:
line 95 -> 103); `DECISIONS.md` gained rows D-95..D-106 after line 108; `config/models.toml` gained lines after 43. Every other
cited file is byte-identical between the two commits (`git diff --name-only 26780eb 21757f6` against every path in
the ledger), so the references verified at `26780eb` hold. New rows V0-V20 (script) and R83-R100 (README) cite lines read at
`21757f6`. Not independently verifiable here: the T-19 helper self-test output, and the camera/zone-check run folders (git-ignored).

## Step 8: step 6 (README screenshots), lead's file choice

- README "What it looks like": main picture is `docs/evidence/T-08/film-ask.png` (real cell, `gfx=1`, filming run);
  then `docs/evidence/T-18/sim-estop.png` and `sim-idle.png` captioned "Simulated cell"; below them the D-81
  real-cell text-mode `real-estop.png` and `real-idle.png` captioned "Real cell, text mode". `real-ask.png` is no longer
  shown (its text file stays and claims V8 still cites it).
- Claims: R11 and R12 rewritten, R13 and R14 labelled "real cell, text mode", R102 and R103 added for the two simulated shots.
- Caveats written into R11 and R12: `film-ask.png` has no run id; its text dump `film-ask.txt` is blank (the graphics
  screen draws pixels), so it is not linked and not committed. The picture is taken after the human's y
  (`CONFIRMED`, `TAINTED: NO`), so it shows ASK and CONFIRMED in the decision list, not the open prompt. The caption says so.
- `docs/evidence/T-20/` untouched.
