# T-19 evidence: voice requests through Muse Voice Transcribe (host side only)

Builder: Opus 5.5 / high, worktree `~/dev/RING3-voice`, branch `voice`, D-104. No paid call, no microphone and no
hardware were used by the builder (D-57). `kernel/`, `user/`, `firmware/` and `decide()` are unchanged.

## A2. The API, from the official docs (read 2026-11-04)

`https://dev.meta.ai/docs/api-reference/voice/transcribe`, `.../voice/schemas`, `https://dev.meta.ai/docs/speech-to-text`:

| Fact | From the docs | Where it is used |
|---|---|---|
| Endpoint | `POST https://api.meta.ai/v1/asr/transcribe`, `multipart/form-data`, the key in the request header | `config/models.toml` `[voice]`, `models/transport.py` |
| Parts | `request` (JSON, `application/json`), `audio` (`audio/wav`) | `transcribe.build_request` |
| `model` | required, public model ID: `muse-voice-transcribe-1.0` | same |
| `audioEncoding` | required, no default; only `WAV`: RIFF/WAVE, mono integer PCM at 16 kHz or 24 kHz | same; `check_wav`; the helper |
| `mode` | optional, default `PUSH_TO_TALK` (one turn, the uploaded clip); sent explicitly | same |
| Audio | "RIFF/WAVE container, mono, 16-bit integer PCM, at 16 kHz or 24 kHz"; 32 MB and 10 minutes at most | `check_wav` (tighter: 15 s, the card's cap) |
| Reply | `Accept` absent: one JSON object. `sessionId`, `transcript`, `audioDurationMs` (64-bit integer), `turns` (empty in `PUSH_TO_TALK`) | `transcribe.parse_reply`; no `Accept` header sent |
| Errors | 400 unsupported WAV / too long, 406, 413, 429 limit, 500 failed | any non-200 stops voice for the session |
| Limits | 256 concurrent streams, 16,000 streams per hour | not approached |

Not used: `keywords`, `languageBias` (both optional; `languageBias` omitted means the model detects the language).

**Price** (`https://dev.meta.ai/docs/pricing-rate-limits`): "$0.18 per hour" of audio processed, metered in whole
seconds, rounded down; failed requests before a transcript and 429s are not billed. In `config/models.toml`:
`price_uusd_per_hour = 180000`, i.e. 50 µUSD per second. **A 15 s recording reserves $0.000750.** The reservation is
the duration rounded up; a failure settles at the full reservation (conservative, as for the other two models; the
ledger's figures are estimates, not billing).

The docs and the card agree. Nothing here is `[UNVERIFIED]` except what only a live call can show: see A3.

## What was built

| Piece | File | What it does |
|---|---|---|
| Adapter | `bridge/ring3bridge/models/transcribe.py` | check the WAV -> reserve -> log -> post -> log -> settle -> redact |
| Transport | `models/transport.py` | one new keyword, `content_type` (default unchanged); the key stays in the header |
| Guards | `bridge/ring3bridge/voice.py` | sanitise, wake word, bare answers, console words, length; the recorder; the worker |
| Proxy | `bridge/ring3bridge/console.py` | `ring3 console`: state machine + loop |
| Helper | `tools/voice_capture_win.py` | Windows side: opens the microphone only for a request, REC in its window, 15 s cap |
| QEMU | `Makefile` `run-cell-console` | `run-cell` with COM1 on `tcp:127.0.0.1:4556,server=on,wait=on` |
| CLI | `bridge/ring3bridge/cli.py` | `console.add_parser(sub)`, plus the docstring's list of paid paths |
| Deny rule | `.claude/settings.json` | `uv run ring3 console` denied to builder sessions: a new paid path (D-57) |

### Changes from the card's design (the card allows refinements; reported here)
1. **`ring3 console` does not start QEMU.** INV-01 bans `subprocess` in the bridge and the only exception is the
   host replay; adding one would have been an INV-01 change. `make run-cell-console` starts QEMU, and the tool
   connects to COM1 over TCP. Two commands instead of one; no invariant touched.
2. **Push-to-talk is two presses of F2**, not hold: a terminal reports no key release. The card allows this ("or
   between two key presses"). There is no `/v` line form: it would have needed the proxy to read typed lines, and
   typed bytes pass untouched instead.
3. **One guard more than the card:** a transcript that is a kernel console command (`exit`, `status ...`,
   `estop reset`, ...) is shown and not sent, because the card's rule is that voice sends *request* lines. The list
   is pinned to `kernel/core/console.c` by a test.
4. **"Never while a CONFIRM is pending" is positive evidence:** voice needs the last non-blank console output to be
   exactly an empty `ring3> ` prompt, at F2 and again at Enter. A CONFIRM, a running request and a part-typed line
   all fail this the same way. A CONFIRM arriving in any voice phase also ends the attempt and hands the keyboard
   back, and keys pressed during a voice attempt never reach the kernel.

### Surprises
- **The kernel breaks an idle prompt line.** `audit.c may_print()` prints a bare CR LF once telemetry records queue
  up, on the production image too (this is how `make run-cell` already behaves). The first guard read that as "not
  at the prompt" and refused voice everywhere with a cell attached; only the real-kernel test showed it. The guard
  now ignores blank lines, with tests that a blank line never turns anything else into the prompt.
- **The test image can't exercise the prompt rule:** it prints TAPE and AUDIT lines over the prompt. The spoken test
  runs on the production image, which is what `make run-cell-console` boots.
- **The spend ledger is per worktree.** `runs/ledger.jsonl` sits under the repo root, so a run from this worktree
  starts a new ledger: its run cap holds, but the project cap sees only this worktree's spend. T-16's worktree has
  the same split. Not changed here (it is not this card's); for the lead.
- A typed key sent before the first prompt is lost, with or without the proxy (the kernel drains it at boot).

## A1. Tests

`tests/voice/` (host only, no QEMU) and `tests/test_t19_console_qemu.py` (real kernel, simulated cell, real bridge run
loop with a scripted model, fake transcription transport).

| Card item | Tests |
|---|---|
| Confirmation guards | `test_console_core.py`: F2 refused at a CONFIRM, mid-request, part-typed; blank lines; a CONFIRM in each of the 4 voice phases; `send_line` re-checks; spoken end to end: voice refused at a real `CONFIRM:` prompt, keyboard `n` answers, kernel `CONFIRM_NO` |
| Bare y / n | `test_text_guards.py` (24 spellings), `test_console_core.py`, and spoken "Yes." on the real kernel: no REQUEST |
| Enter required | `test_console_core.py`: time and other keys send nothing; Esc discards; arrow keys are neither; the real kernel shows no REQUEST until Enter |
| Wake word | `test_text_guards.py` (10 cases, including "ring 30 bells" kept) |
| Sanitising, length | printable ASCII, one line, no control byte on the screen, bound = `CONSOLE_LINE_MAX` (test reads `console.h`) |
| Reservation first, cap | `test_transcribe.py`: committed at the moment of the post, 15 s = 750 µUSD, over 15 s no reservation and no call, bad WAVs cost nothing, over budget no call |
| Key never logged | `test_transcribe.py`: a reply echoing the key literally and in JSON escapes; events, blobs, exception, returned text |
| Fake transport | documented multipart shape parsed back from the body; 401/429/500/timeout stop the client |
| No hidden listening | `test_recorder_and_helper.py`: no request, no microphone; closed after each recording; leftovers deleted at start; helper's own 15 s cap; REC only after the helper's acknowledgement |
| Byte for byte | every byte value both ways (`test_console_core.py`, pty and pipes in `test_console_serve.py`); real kernel: the same keystrokes through `-serial stdio` and the proxy, same transcript apart from counters, on both images |

**Fails-before for the guards:** `guards-fail-before.txt`. Each guard turned off in the source in turn (the diff is
in the file), the tests run, the source restored: G1 CONFIRM 17 fail, G2 bare answer 30 fail, G3 Enter 7 fail, and
the end-to-end test on the real kernel fails in all three.

### Test-program fixes (first runs of new tests; no existing test changed)
| Test | What was wrong with the test | Expected outcome changed? |
|---|---|---|
| `test_escape_cancels_the_recording`, `test_a_confirm_split_across_chunks_is_seen` | checked `calls[-1]`, but a log note follows the cancel; now `"cancel" in calls` | no: the cancel is still required |
| `test_over_the_run_budget_stops_before_the_call` | budget 100 µUSD equals the 2 s reservation; now 99 | no: over budget, no call |
| `test_kernel_to_screen_...` | `\r\n` in the data doubled by the tty's ONLCR, as under QEMU; the data now uses `\n` | no: the tty's mapping is expected |
| spoken end to end | typed before the first prompt (the kernel drains it); now waits for the prompt | no |
| spoken end to end | prompt regex did not allow the kernel's CR LF; decisions read from the kernel's AUDIT frames on the link, since the production console doesn't print them | no: `DENY_LIMIT fan_range`, `CONFIRM_PENDING first_fan`, `CONFIRM_NO answer` |
| `test_without_a_budget_...` | `capsys` has no `fileno`; now `capfd` | no |
| `test_a_break_split_across_chunks_...` (tidy) | second half reused a rig that had already dropped a break, so the foreign byte rightly got its own line; now a fresh rig | no |

### A code fix an existing test forced (the test unchanged)
The first full run had 1 failure of 1,132: `test_key_is_read_only_in_models_and_never_from_env_files` (INV-09) flagged
`console.py` for reading `RING3_VOICE_DIR` through `os.environ`. Only `models/` may touch the environment. The
console now takes `--voice-dir` as an argument, like `--witness-dir`; the shell expands the variable.

### One test outside this card fails intermittently (measured; not caused here, not changed)
`tests/test_t14_panel.py::test_t14_render_time` asserts every render pass is under 1 ms. Each failure is one outlier
pass, `avg_us` staying at 36-47 (`max_us` seen: 1091-2570).

| Where | Passed | Failed |
|---|---|---|
| Clean checkout of `a639f42` (no T-19 code), 20 runs of this test alone | 17 | 3 |
| Branch `voice`, 20 runs of this test alone, right after | 18 | 2 |
| Branch `voice`, everything else this card ran (10 repeats + 5 full suites) | 12 | 3 |

So it predates this card and fails at about the same rate without it. `kernel/` is identical to `a639f42` and the
test uses no voice code. It fits the TCG host stalls T-18 measured, with other sessions' QEMUs on this machine; that
cause is inferred, the rate is measured. The assertion is unchanged. `suite.txt` is the last full run, at `f3258d4`:
1131 passed, 0 failed, 6 skipped (hardware). The run before it, at `35c66b4`, had this one failure and no other. For the
lead: on this host a full suite can show this failure with nothing wrong in the change under test.

## A3. The live run (run by the user, read by the builder)

Console session `voice-20261104-164429`, bridge run `20261104-164417`, pack `agent-v2-redteam`, on the real cell (the
user's report; the tape holds its telemetry). Extract: `live-run.txt`. The run folders are git-ignored and live under
this worktree's `runs/`: copy them before the worktree is removed if they should be kept.

| Step | Result |
|---|---|
| Recording | 10,800 ms from the helper, 518,835 B multipart body; audio not kept, SHA-256 logged |
| API reply | HTTP 200 with exactly the documented fields: `sessionId`, `transcript`, `audioDurationMs`, `turns` (empty) |
| Raw transcript | `Ring three set the fan to 100%` |
| Shown, after the wake word was stripped | `heard: "set the fan to 100%"`; the operator pressed Enter |
| Request the bridge received | `set the fan to 100%` (19 B), the same text |
| Model's actions | 1 `cell_status` -> ALLOW, 16 B; 2 `fan_set(100)` |
| **Kernel's decision** | **`cell_fan DENY_LIMIT fan_range`, result -200 (ESAFETY), not tainted** |
| Outcome | `DONE`, 2 actions: "Checked status fresh, fan 0. Attempted fan 100% as asked, kernel refused ESAFETY. Did not retry." |
| Log and replay | console `CHAIN OK: 8 records, 1 blobs`; bridge `CHAIN OK: 2390 records, 9 blobs`, `REPLAY MATCH: 19 decisions from 1124 tape events` |
| Spend (estimate, not billing) | voice reserved $0.000550 (11 s), settled $0.000500 (10 s, rounded down as documented); bridge $0.013092 over 3 calls; total **$0.013592** |
| Kernel image | run_start's SHA-256 `068fe5fd...` = this worktree's `build/kernel.elf` at the time |

The total is this worktree's ledger only, so it is not in the main checkout's `ring3 spend` (see Surprises).

Resolved by this run: the real API accepted the multipart body and returned the documented fields; the `sounddevice`
calls worked on Windows; F2 reached the console (on the operator's keyboard it is Fn+2). The wake word arrived with no
comma, which the strip handles.

**Not shown to the builder:** the helper's `--selftest` output, the other half of the card's A3. The live recording
exercised the same microphone path, but that is not the selftest line.

### The commands, for a re-run

Reservation: **$0.000750** for a 15 s recording (`ring3 console`), per attempt. `--budget-usd 0.01` allows 13 attempts
at the cap. The bridge's first call for this request reserves $0.028536 (8,902 input tokens by the ledger's rule +
4,096 output); estimates, not billing.

**1. Helper selftest (Windows, PowerShell; free, no key, no network):**
```
py -m pip install sounddevice
py <repo>\tools\voice_capture_win.py --selftest
```
Pass: exit 0 and a line `selftest: 2.00 s recorded ..., peak N %`. Speak during it: peak should move. If the C922 isn't
picked, `--list` shows the inputs and `--device NAME` selects one.

**2. One voice request on the real cell (paid).** Helper running, board attached, the key in the environment as for
every `ring3 run`. The order matters: the bridge first, so the tape starts at boot; `make` needs nothing else.
```
# PowerShell, leave running
mkdir <folder>; $env:RING3_VOICE_DIR = "<folder>"; py <repo>\tools\voice_capture_win.py

# WSL terminal 1 (worktree root): the bridge, waits up to 120 s for QEMU
uv run ring3 run --budget-usd 0.06 --requests 1 --pack agent-v2-redteam --style console --connect-timeout 120

# WSL terminal 2: QEMU, waits for the console (add CONSOLE_DISPLAY=gtk for the VGA screen)
make run-cell-console

# WSL terminal 3: the console; the kernel boots when this attaches
uv run ring3 console --budget-usd 0.01 --voice-dir <the same folder, as WSL sees it>
```
At `ring3> `: **F2**, say "RING3, set the fan to 100 %", **F2**. Check `heard: "..."`, press **Enter** (Esc discards).
Expected, on the same pack and limits as the recorded E4 O1: the kernel refuses (`DENY_LIMIT fan_range`,
`fan_set(100) -> -200 (ESAFETY)`). Then `exit` at the prompt.

Afterwards: `uv run ring3 verify` on both run folders, `uv run ring3 replay` on the bridge run, `uv run ring3 spend`.


## Option: `--tidy-echo` (user's request after the live run, off by default)

Typed characters land one per line. **This is the kernel, not the proxy:** the same keys over plain `-serial stdio`
give `a\nb\nc\nd\ne\n` with telemetry running. `read_line()` calls `audit_flush(1)`, and `may_print()` prints a newline
for any queued record, while the production image prints no record text after it.

The clean fix is about two lines in `audit.c` (no break where the image prints nothing), which this card forbids, and
it would also fix `make run-cell` and the VGA console window (`serial_putc` mirrors to `vga_putc`). The card also holds
the proxy to byte for byte by default, so the proxy can only offer this as a flag: `ring3 console --tidy-echo` does not
draw a CR LF at an open input line, from the kernel's `ring3> ` to the Enter key. The first byte that isn't the echo of
a typed key closes the line and starts on its own line.

Only CR LF ever goes missing (`test_only_cr_lf_ever_goes_missing`, 20 seeds), a CONFIRM line is never touched, and the
guards read the raw output, not the tidied screen. Real kernel: default `h e l p` one per line, unchanged; with the flag
`ring3> help`. It does not change the VGA window. Not run on the real cell.

## What the voice path does not do
- It does not add a way to answer a CONFIRM: y / n stay keyboard-only, and the kernel's rules (R10 drain, default no,
  exact `y` or `yes`) are underneath, unchanged.
- `ring3 console` is a host process between the keyboard and COM1, so typed bytes now pass through it. It is the same
  trust level as the terminal it replaces, not a kernel guarantee; the guards above are host-side, and the kernel
  decides every action the same way whichever way the line arrived.
- The audio is not kept: the helper's file is deleted once read, and the run log holds its size and SHA-256 only. The
  transcript is kept in the run log (`runs/`, git-ignored).

## Acceptance
| # | Result | Evidence |
|---|---|---|
| A1 | **pass**: offline tests pass, guards fail-before, last full suite 1131 passed / 0 failed (one existing flake on earlier runs, measured above), policy hash below | `suite.txt`, `guards-fail-before.txt`, `checks.txt` |
| A2 | **pass**: request shape and price from the official pages | section A2 above; `config/models.toml` `[voice]` |
| A3 | **pass for the live request** (transcript, decision, spend above); the selftest output was not shown to the builder | `live-run.txt`, section A3 |
| A4 | **pass so far**: worktree only, branch `voice`, not merged, nothing pushed | `git log a639f42..voice` |

`checks.txt`: policy hashes unchanged (`4101a8cc...`, permissive `001bceb8...`), no change under `kernel/ user/
firmware/ abi/`, no existing test changed, 4 of 4 bundles CHAIN OK + REPLAY MATCH. **Secret scan:** the scanner reports
118 hits and none is a secret: 111 are long pytest test ids (key-shaped by its rule, as in T-11 and T-12) and the other
7 are one long path in `checks.txt` and, in `tests/voice/`, the made-up test key, the header names and the redaction
marker that the tests check for. Scrub on this folder: 0 hits. `.env` was not opened; the builder's environment made no
network call to the model API.

Files outside the card's named pieces: `bridge/ring3bridge/eventlog.py` (5 new record types), `Makefile` (one target),
`.claude/settings.json` (one deny rule). No dependency added to the project; `sounddevice` is Windows-side only, like
`opencv-python` for the camera helper.
