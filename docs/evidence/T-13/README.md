# T-13 evidence: final docs pass (items 1-4)

Builder: Opus 5.5 / high, main working tree (D-93). Docs only: `README.md`, `docs/video/script.md`,
`docs/claims/claims.md`, `docs/INVARIANTS.md` (the two lead-authorised wording edits). No code, tests, `tools/` or
`docs/evals/` touched; no paid call, no hardware. **Step 5 (line counts, claims line references at T-12's release
commit, header commit, final scan) is not done yet:** it waits for T-12 to be merged.

## Acceptance (items 1-4; A2 and the line-count part wait for step 5)
| # | Result | Evidence |
|---|---|---|
| A1 | **PASS for items 1-3**: every item applied (tables below); script still 180 s with timecodes, every scene at most 2.5 words/s, every caption at most 8 words | this file; `docs/video/script.md` |
| A2 | **open (step 5)**: every changed sentence has its claims row (item 4, below), each new reference checked against the tree at `bf86664`; the full re-check at T-12's release commit and the header commit are step 5 | `docs/claims/claims.md` |
| A3 | **PASS at this commit**: `test_inv_11_banned_words` 2 passed; manual whole-word scan of `docs/video/`, `docs/claims/`, `README.md` 0 hits; no franchise, military, medical or political framing added | scan output below |
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

## Scan output (at this commit)
```
$ uv run pytest -q tests/test_inv_11_banned_words.py
..                                                                       [100%]
2 passed in 0.25s
$ grep -rniwE "success|secure|safe|real-time|realtime" docs/video docs/claims README.md
(no output, exit 1)
```

## Step 5 (to do after T-12 is merged)
Line counts (`wc -l`, T-05's file set) into `docs/evidence/T-05/linecount.txt` and the README numbers; every
claims.md line reference against HEAD (results.md moves with T-12's S8); header commit; banned-word test and
manual scan again.

## Step 6 (queued by the lead; after filming)
Replace the README's three D-81 screenshots ("What it looks like": `docs/evidence/T-08/real-{ask,estop,idle}.png`
and their `.txt`) with the new T-14 screen shots from the filming run; the lead gives the file names. Update the
captions and claims rows R11-R14 to the new files and run id, and re-run the banned-word test and scan.
