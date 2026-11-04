# T-19 evidence: voice requests through Muse Voice Transcribe (host side only)

Builder: Opus 5.5 / high, worktree `~/dev/RING3-voice`, branch `voice`, D-104. No paid call, no microphone and no
hardware were used by the builder (D-57). `kernel/`, `user/`, `firmware/` and `decide()` are unchanged.

## A2. The API, from the official docs (read 2026-11-04)

`https://dev.meta.ai/docs/api-reference/voice/transcribe`, `.../voice/schemas`, `https://dev.meta.ai/docs/speech-to-text`:

| Fact | From the docs | Where it is used |
|---|---|---|
| Endpoint | `POST https://api.meta.ai/v1/asr/transcribe`, `multipart/form-data`, `Authorization` bearer header | `config/models.toml` `[voice]`, `models/transport.py` |
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

### A code fix an existing test forced (the test unchanged)
The first full run had 1 failure of 1,132: `test_key_is_read_only_in_models_and_never_from_env_files` (INV-09) flagged
`console.py` for reading `RING3_VOICE_DIR` through `os.environ`. Only `models/` may touch the environment. The
console now takes `--voice-dir` as an argument, like `--witness-dir`; the shell expands the variable.
