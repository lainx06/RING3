# T-07 evidence: camera witness (Part A offline; Part B code + simulated cell done, live steps open)

**Part B status (2026-11-03, Opus 5.5 / high, D-57):** everything up to the live steps is built and tested on
the simulated cell; A2-A4 need the real camera and paid SAM calls, which are the user's (D-57). See
"Part B" at the end. Part A text below is unchanged except where a Part B test change is logged.

Builder: Opus 5.5 / high, auto mode (D-57) · 2026-11-02, finished 11:20 ET, second terminal (T-03b ran in the
other one on disjoint paths; scoped test runs only). **Offline only:** fake transport, no camera, no paid call,
no `MODEL_API_KEY`, no network except the package install. No change to `kernel/`, `user/`, `firmware/` or
`bridge/ring3bridge/models/`. Part B (camera, real cell, paid SAM calls) has not started.

## Acceptance (Part A)
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | Gauge PDF + geometry generated; INV-14 tests and the synthetic-fixture tests pass; parser rejects malformed SAM lines; ledger reserves one image before every SAM call (fake transport) | **PASS.** `hardware/gauge/make_gauge.py` writes `ring3_gauge.pdf` (Letter), `ring3_gauge_a4.pdf`, `ring3_gauge.png` (300 dpi) and `geometry.json`, deterministically; tests check geometry.json is in sync, markers are detected in the PNG **and in the PDF's own raster** within 0.3 mm of geometry.json, and the true page size. `test_inv_14_cant_verify_cases` (13 cases) + `test_inv_14_never_upgraded` + `test_inv_14_not_in_decision_path` pass. Fixtures: 10 verdict cases × 3 camera/light variants, 12 accuracy variants, dead-zone and off-axis cases. Parser: **48 malformed lines + 8 malformed envelopes rejected** (`SAM_FORMAT`), 16 accepted. Spend: the fake transport re-reads the ledger file **at post time** and fails the test unless exactly one image (2500 µUSD) is reserved and unsettled; no price / no budget / run cap / project cap → nothing sent. **witness 253 passed, 2 skipped by design; + INV-01 (29) = 282 passed, ~10 s.** 15/15 mutations caught. | `pytest.txt`, `fixtures.md`, `gauge.png`, `mutation.txt` |
| A2 | Calibration residual reported and under the limit, or the witness disables itself | **open (user, live)**. `ring3 calibrate` built and run end to end on the simulated cell: 5 of 5 points, every move ALLOWed by the kernel, fit under the unchanged 3.0 deg limit; a missing point / refused move / bad fit disables the witness with the message | `sim-calibrate.txt`, `tests/test_witness_loop.py`, `tests/witness/test_part_b.py` |
| A3 | Blocked arm and unplugged signal never PASS; covered marker CAN'T VERIFY; PASS rate over 10 moves | **open (user, live)**. Simulated: held arm -> `FAIL WRONG_ZONE` (board says 50, camera says MID) and the scripted model halts; `ring3 calibrate --checks` normal round 3/3 PASS, held round 50 and 130 FAIL; covered marker -> `MARKERS` (Part A) | `sim-witness-held.txt`, `sim-checks.txt` |
| A4 | Live closed loop: the model receives the witness result and reacts to FAIL; `CHAIN OK`; spend <= budget | **open (user, live)**. Simulated with a scripted model: witness observation in the prompt (`PASS` / `FAIL` / `CAN'T VERIFY` + reason), `halt` after `FAIL`, SAM 401 -> STOP `model_auth`, run ends; `CHAIN OK` | `sim-witness-loop.txt`, `sim-witness-held.txt` |
| A5 | Commits: real `Builder:` trailer, only this card's paths | PASS for Part A: see `git log` (card + DECISIONS merge first, then deps/config, gauge, witness + tests + helper, evidence + STATUS) | `git log` |

`test_inv_14_never_upgraded` is skipped for `boundary band` and `command on a boundary`: the test replaces the
verdict stage with one that always says PASS, and those two CAN'T VERIFY reasons come from the verdict stage
itself (`test_judge` covers them directly).

## What was built
| Step | Files | Notes |
|---|---|---|
| 1 Gauge | `hardware/gauge/make_gauge.py` → `ring3_gauge.pdf`, `ring3_gauge_a4.pdf`, `ring3_gauge.png`, `geometry.json` | Dial radius 80 mm, LOW 30–70 / MID 70–110 / HIGH 110–150, black ±5° boundary bands at all four edges, OUT regions below 25° / above 155°, ticks every 10°, pivot crosshair, SG90 footprint (dashed), RING3 title, a 100 mm scale bar, 4 × 30 mm DICT_4X4_50 markers (IDs 0–3) with quiet zones. Gauge frame: mm, origin at the pivot, y up, angles CCW from +x. PDF: a 600 dpi gray raster filling the page (stdlib zlib, no PDF library), `/PrintScaling /None`. Everything is placed relative to the pivot, so Letter and A4 share one geometry. |
| 2 Dial | `witness/dial.py` | ArUco detector (sub-pixel corners); IDs 0–3 exactly once in **this** frame, else `MARKERS`; 16-point homography; worst corner error > 2 mm *(prov.)* → `MARKERS`; warp-crop x −92…92, y −22…92 mm at 5 px/mm (920×570). No stored reference frame. |
| 3 SAM | `witness/sam.py`, `config/models.toml` `[sam]` | Request per D-56 (one user message: `input_text` + `input_image` base64 data URL, `stream: false`, `metadata.include_confidence: "true"`). Bounded regex record parser with a look-ahead record end (mask data may hold `,`, `|>`, `<|box`); strict integers ≤ 6 digits, no leading zeros; `c` → integer ppm, 0…1; boxes inside the image; `w`/`h` within 1 of the corners; ≤ 16 records; unique IDs. Injected `Transport` + `ImageSpend`; reserve → post → settle (full) → parse. Any non-200 (401/403 included) or transport error → `SamStop` (run stops; client stopped for good), never CAN'T VERIFY. Price $2.50 / 1,000 images = 2500 µUSD/image, source + date in the config. |
| 4 Angle | `witness/angle.py` | D-58: box → gauge mm; arm-shape filter (pivot inside, an edge near the pivot, far corner near the arm radius, centre away from the pivot); 0 records → `NOT_FOUND`, none plausible → `SHAPE`, ≥ 2 plausible → `AMBIGUOUS`; angle = pivot → **box centre**, integer millidegrees. Confidence never removes a candidate (a low-confidence ghost can't be dropped to make the answer unique); it only blocks the one candidate (`LOW_CONFIDENCE`, also when `c` is missing). |
| 5 Verdict | `witness/verdict.py` | Integer millidegrees; bands inclusive at 30/70/110/150; < 25° or > 155° → FAIL; constants checked against `geometry.json`. |
| 6 Calibration | `witness/calibrate.py` | ≥ 5 distinct commands spanning ≥ 40°; least squares stored as integers (slope ppm, offset mdeg), residual recomputed from them; disabled on a missing point, residual > 3.0° *(prov.)*, slope outside 0.85–1.15, or dial at a 90° command more than 15° from 90. Disabled → every check `CALIBRATION` before any spend. |
| 7 Tests | `tests/witness/` | `synth.py` renders the real gauge + an SG90-like horn into a 1280×960 frame (4 perspectives, blur, 3 lightings, noise, JPEG); the fake SAM box is the arm label's box after the **detected** dial warp. `test_inv_14_witness.py`, `test_fixtures.py`, `test_sam_parser.py`, `test_sam_client.py`, `test_angle_verdict.py`, `test_calibrate.py`, `test_capture.py`, `test_gauge.py`; `make_fixtures_md.py` writes `fixtures.md`. |
| 8 Capture | `tools/witness_capture_win.py`, `witness/capture.py` | File protocol in one shared folder (path from `RING3_WITNESS_DIR` on the Windows side; passed in as an argument on the bridge side). Nonce = 128-bit hex; helper grabs 6× before `retrieve()`, embeds `ring3-nonce:<nonce>` as a JPEG COM segment, writes `<nonce>.tmp.jpg` → `os.replace` → `<nonce>.jpg`; drops requests older than 10 s. Bridge: missing / older than the request / other or no nonce / undecodable → `STALE_FRAME`. No listener, no subprocess. Tested in WSL with a fake camera; **not run on Windows yet**. |
| — | `witness/pipeline.py` | `check()`: commanded zone → calibration enabled → markers → SAM → angle → calibrated angle → verdict; the first `CantVerify` returns, so nothing upgrades it. `measure()` is reusable for `ring3 calibrate` (Part B). |

## Findings (surprising; the lead should read these before Part B)
1. **The D-58 box-centre angle has a dead zone around 90° with the SG90 horn.** The hub (≈ 7 mm across) is wider
   than the arm is offset sideways for angles within about ±5° of 90°, so the box's x-extent is the hub's and the
   reading sticks at 90°. Combined with the hub-vs-tip asymmetry elsewhere, the raw error on perfect synthetic
   frames is an S-curve of about ±4° (`fixtures.md`). Its effect on calibration depends on the mounting offset:

   | offset (deg) | −7 | −5 | −4 | −2 | 0 | +2 | +4 | +5 | +7 |
   |---|---|---|---|---|---|---|---|---|---|
   | residual (deg) | 4.04 | 3.88 | 3.40 | 2.75 | 2.34 | 2.40 | 3.71 | 4.05 | 4.11 |
   | 3.0° limit | disabled | disabled | disabled | ok | ok | ok | disabled | disabled | disabled |

   I kept the 3.0° *(prov.)* limit and did **not** raise it after seeing this. With offsets ≥ 4° the witness
   disables itself with a message, which the card allows ("or the witness disables itself"). Re-seating the
   horn can't fix it (one SG90 spline tooth = 14.4°), but **rotating the paper** can. So Part B setup: command
   90°, then turn the gauge until the arm points at the 90 tick. After calibration the error at zone centres is
   ≤ 4.4° on the synthetic frames, inside the 15° margin to each band (`fixtures.md`, one camera variant). Options for the lead if Part B can't get
   under the limit: accept a wider limit (it must stay below the 5° band half-width; see below), or measure the
   angle from the mask once a real SAM line shows its encoding.
2. **Parallax matters more than the card assumes. Put the camera's axis over the pivot.** The horn is ~27 mm
   above the paper; seen from ~400 mm it is enlarged ~7 % about the point under the camera. With an 18 mm arm,
   a 1 mm sideways shift of the apparent hub is ~6°. Synthetic raw errors: camera over the pivot ±3.7°; 22 mm
   off ≈ +10°; over the dial centre (35 mm off) up to ±20°. `test_off_axis_camera_never_passes_a_wrong_zone`
   shows that for 4 off-axis positions the calibration either disables the witness or the verdicts never PASS a
   wrong zone. With the camera centred over the pivot (C922 at ~400 mm), all four markers are still in view.
3. **Why a residual limit must stay below the band half-width:** if every measurement is within 5° of the truth,
   a reading can't land clear inside a zone the arm isn't in (it lands in the right zone or in a band). A limit
   ≥ 5° loses that guarantee.
4. A commanded angle inside a band (e.g. `servo_set 70`) gives `CAN'T VERIFY: COMMAND_BOUNDARY` before any spend:
   the commanded zone itself is ambiguous. **Decision for the lead** (not in the card). Context pack agent-v2
   should command zone centres (50 / 90 / 130).
5. The ledger prices tokens. One SAM image is recorded as 0 input tokens + 1 output token at
   `2500 × 1,000,000` µUSD per 1M tokens, which is exactly 2500 µUSD and settles in full (`LedgerImageSpend`).
   Totals and caps are right; Part B may prefer a units-based `reserve` in `ledger.py` (T-03a's file).

## `[UNVERIFIED]` (from the card's facts only; no call to Meta was made)
- Whether SAM's box `x2`/`y2` are exclusive (`w = x2 − x1`) or inclusive. The parser accepts `w`/`h` within 1;
  the angle takes edges as pixel boundaries (worst case 0.1 mm).
- The reply envelope for `sam-3.1`: parsed like the Responses API (`status: "completed"`, exactly one
  `output[].content[]` item of type `output_text`). An empty `output_text` = no records. A reply with no
  `output_text` item → `SAM_FORMAT`.
- Whether `c` is always present with `include_confidence: "true"`. A record without `c` → `LOW_CONFIDENCE`
  (conservative; revisit with a real line).
- The mask payload encoding (kept opaque, never decoded).
- Arm model numbers (`ArmModel`: 18 mm, tolerances) and the 2 mm marker-fit limit are *(prov.)*. Measure the
  real horn in Part B.
- The Windows helper has not run on Windows (protocol logic tested in WSL with a fake camera).

## Test-program fixes (per the logging rule; none loosened a limit in the code)
| # | Test | What was wrong with the *test* | Change | Expectation |
|---|---|---|---|---|
| 1 | `tests/witness/synth.py`, `test_fixtures.py::test_calibration_from_synthetic_session` | The fixture simulated a 4° mounting offset, which puts the method's 90° dead zone on a calibration point (residual 3.71° > 3.0°). It also compared the 0° *intercept* with the offset, which the fitted slope moves. | `OFFSET_DEG = 4.0` → `2.0` (paper aligned by hand, the Part B setup); assertion `abs(cal.offset_mdeg - OFFSET_DEG*1000) <= 6000` → `abs(cal.predict_dial_mdeg(90) - 90_000 - OFFSET_DEG*1000) <= 3000` (tighter). **New** `test_dead_zone_offsets_disable_the_witness[−7, −5, 4, 5, 7]` keeps the 4° case and asserts its real outcome: disabled + every check `CAN'T VERIFY: CALIBRATION`, no SAM call. | Residual limit unchanged (3.0°); the verdict table is unchanged. The 4° setup's expectation went from "calibrates" to "disables itself", which is what the code does and what the card allows. |

Code fixes found by tests or probes (code, not tests): the offset limit now applies at a 90° command, not at the
0° intercept (a bug); the marker tile was drawn half a pixel off (`Canvas.blit`); box edges convert with the
half-pixel convention (`angle.box_mm`). Two docstrings were reworded because the INV-14 token scan flagged the
words "environment" and the key's name in prose (the test is unchanged).

## Dependencies added (tell the user)
`numpy 2.5.4` and `opencv-python-headless 5.0.0.93` (`pyproject.toml` `dependencies`, `uv.lock`). The Windows
helper needs `py -m pip install opencv-python` on the Windows side (Part B).

## For Part B
- Setup: camera straight down **with its axis over the pivot**; command 90°, rotate the paper until the arm
  points at the 90 tick; then `ring3 calibrate`.
- Wire `HttpsTransport` + `LedgerImageSpend` into `SamClient` (only `bridge/ring3bridge/models/` reads the key);
  `measure()` for calibration and `check()` for the `witness` op; add the `witness` record type to `eventlog.py`
  (integer millidegrees; image hash as a blob). The INV-14 import allowlist will need `ring3bridge.eventlog`
  then (not a link writer).
- Add `uv run ring3 calibrate` to the settings deny list (user edit, D-57).
- First real SAM line: compare with the grammar (card stop rule), check `c`, the box convention and the mask
  encoding; replace the *(prov.)* arm numbers with measured ones.


# Part B (2026-11-03, Opus 5.5 / high, auto mode under D-57)

No paid call, no key read, no network by the builder. `uv run ring3 run` and `uv run ring3 calibrate` are denied
to builder sessions in `.claude/settings.json` (`aea614c`, lead's item 3); `calibrate --dry-run` is therefore
exercised through `cli.main([...])` in pytest only, the way `test_cli.py` covers `run --dry-run`.

## Lead decisions applied in this session
| Decision | Applied as |
|---|---|
| D-60 note: 8 ruff long-line errors in the witness files | `9e6158b`: strings rewrapped in `tests/witness/make_fixtures_md.py`; regenerated `fixtures.md` byte-identical |
| D-70: live and filmed runs start `ring3 run` first, then `make run-cell` | in the live steps below (and `ring3 calibrate` the same way: it waits 30 s for COM2) |
| agent-v2 and agent-v2-redteam stay pinned; witness guidance in a new pack | `agent-v3` = agent-v2 + "Camera witness (this pack)" before "How to work", heading v3, nothing else; SHA-256 `c98d2955...` pinned in `test_context_pack.py`; logged in `run_start` like every pack; `--pack agent-v3` (`70514fc`) |
| Deny `uv run ring3 calibrate` (+ ` *`) | `.claude/settings.json`, separate commit `aea614c` |
| D-63 / D-64 / D-64b: rebuilt cell; servo on the main board's motor rail; gauge on the desk, servo on it, putty only on the servo's sides | setup text below; no code depends on it |
| D-72: default calibration points 60/70/90/110/120 (replaces D-59's "avoid 80-100"); limit stays 3.0 deg | `calibration.CAL_POINTS`; `--points` overrides; `synth.CAL_CMDS` follows (test change 2) |
| Measured horn: 15 mm shaft centre to tip | `ArmModel.length_mm = 15.0`; synthetic horn tip centre 13 mm + tip r 2 mm (test change 5) |

## What was built
| Piece | Files | Notes |
|---|---|---|
| `witness` op in the run loop | `bridge/ring3bridge/run.py` | Intercepted before ACTION encoding: **zero COM2 frames** (asserted on the frame log). Needs a kernel-allowed `servo_set` earlier in the same request (observation result >= 0); the last one's angle is the command. Then a telemetry frame read after the op began must show the board's `STATE` servo at that angle within 3 s, + 500 ms more, else `CAN'T VERIFY: NO_MOVE`. Then `Witness.check` (cheap checks, fresh frame, one image reserved, SAM, verdict). `witness` record: commanded deg/zone, measured zone, verdict, reason, detail, dial and calibrated angle in integer mdeg, crop SHA-256, spend, board servo, ms; frame, crop and SAM line as blobs. Observation for the model: verdict line, `reason= commanded_deg= commanded_zone= measured_zone= measured_deg=`, detail; the SAM line never goes to the model. Counts as one of the request's actions. SAM non-200/transport error -> STOP (`model_auth` for 401/403, else `transport`), budget refusal -> STOP `budget`; both end the run (INV-09). Without `--witness-dir` the old `ENOSYS` answer stays. |
| Witness service | `witness/service.py` | `Witness.check` / `.measure`, `FolderCamera` (the helper protocol), `frame_report` (setup check). Inside the INV-14 static scan (no link, no key, no writes). |
| `ring3 calibrate` | `bridge/ring3bridge/calibration.py`, `cli.py` | Scripted model (no Muse call) through the normal bridge -> kernel -> agentsh path. Start = the board's `STATE` servo in a fresh telemetry frame (`Runner.fresh_servo`). Plan: steps <= 30 deg, >= 300 ms apart, every point approached from the same side (pre-move 10 deg past the first point when needed), <= 7 moves (agentsh ends a request after its 8th ACTION), never a zero-length move; from 90: `60, 50, 60*, 70*, 90*, 110*, 120*`. A witness op after each point (`calibration_point`: dial angle, no verdict). A refused move ends the script and names it. Fit -> `calibration` record + `calibration.json` (integers). `ring3 run --calibration <run>` re-verifies the chain, checks the file hash against the logged record and the geometry hash, and **re-fits the stored points with the current limits**. |
| `ring3 calibrate --checks` | same | Card step 4 without Muse spend: per console request, routes to `--targets` (default 50, 90, 130) and runs a real witness check after each, with `--calibration`; prints a table (request text = condition label, verdict, reason, measured zone, crop hash) and totals. Under the `calibrate` deny rule. |
| `ring3 witness-frame` | `cli.py` | Free setup check: one frame through the helper, markers + fit, the gauge point under the image centre (camera axis vs pivot, <= 5 mm *(prov.)*), frame + crop saved under `runs/`. No SAM, no key, no network. |
| `ring3 run --witness-dir --calibration` | `cli.py` | The witness for a Muse run; the loaded calibration is logged as a `calibration` record; dry run names the witness and the per-image reservation. |
| Event log | `eventlog.py` | record types `witness`, `calibration`. |

## Tests
`tests/witness/test_part_b.py` (offline): plan for every start 30-150 (step, range, one approach side, < 8
moves), the script (moves, a witness after each point only, refusal stops it, NO_MOVE -> missing point, no
telemetry -> not started, checks mode), calibration file round trip / tampering / other geometry / disabled stays
disabled, disabled calibration and stale frame spend nothing, PASS observation text, CLI dry runs.
`tests/test_witness_loop.py` (QEMU + simulated cell, real run loop and kernel): witness before a move -> NO_MOVE
with no frame and no spend; LOW in two moves -> PASS; a refused move -> NO_MOVE; frame log has exactly one ACTION
per servo_set; held arm -> FAIL and the model's halt executes; SAM 401 -> STOP and no second request;
calibration through the kernel 5 of 5, all ALLOW; scripted checks normal + held.
At `6edd716` in a clean worktree: witness + pack + e2e 414 passed, 2 skipped by design; full default suite in
the STATUS line (731 passed, 6 skipped, 161 s).

## Test-program changes (logging rule: cause, diff, unchanged expectation; no limit changed)
| # | Test | Why the *test* had to change | Change | Expectation |
|---|---|---|---|---|
| 2 | `tests/witness/synth.py` `CAL_CMDS` | The fixture calibrates on the points the setup uses; D-72 changed them. With the real 15 mm horn the old set fails the 3.0 deg limit in 10 of 12 synthetic variants. | `(60, 75, 90, 105, 120)` -> `(60, 70, 90, 110, 120)` | calibration ok under 3.0 deg (unchanged; residual now 2.04 deg in `fixtures.md`) |
| 3 | `tests/witness/synth.py` `calibrate()` | The helper called `measure()` and let a `CantVerify` escape; the real calibration (`pipeline.calibration_point`) records it as a missing point, which disables the witness. Surfaced by `test_off_axis_camera_never_passes_a_wrong_zone` (camera 35 mm off-axis -> SHAPE at a point). | `try: ... except CantVerify: pairs.append((c, None))` | the off-axis test's own expectation, unchanged: "disabled, or never PASS a wrong zone" |
| 4 | `test_inv_14_witness.py` case "boundary band" | The case must put the *reading* in the 70 band. With the 15 mm horn an arm at 74 deg reads ~78.5 (clear MID; commanded LOW -> a truthful FAIL), so it no longer tested the band. | `Scene([74.0])` -> `Scene([69.0])` (reads 70.2) | `CAN'T VERIFY: BOUNDARY` (unchanged) |
| 5 | `tests/witness/synth.py` `ARM_LEN` | The synthetic horn must be the real one (user: 15 mm shaft centre to tip). | `18.0` -> `13.0` (tip-circle centre; + tip r 2 mm = 15 mm outer end) | every verdict expectation unchanged |

## Findings (the lead should read these)
1. **D-59's "avoid 80-100" made calibration worse; D-72 fixes it.** The box-centre error is a step at 90, not a
   smooth curve: with the 15 mm horn +5..+6 deg just below 90 and -5..-7 just above (12 synthetic variants).
   Two-sided sets without 90 fail most variants (45/60/75/105/120: 0 of 12 with 15 mm); 60/70/90/110/120
   passes 12 of 12, worst residual 2.08 deg. One-sided sets fit tightly but would extrapolate ~10 deg wrong into
   the other half, so they were rejected. Real data decides (D-72).
2. **15 mm horn and the arm-shape filter.** On-axis: box centre >= 5.72 mm from the pivot (filter 4.0 mm), far
   side 16.9 mm (window 10.5-25.75 mm), box ~85 x 73 px in the 920 x 570 crop: fits. 22 mm off-axis still fits
   (>= 4.35 mm); 35 mm off-axis (camera over the dial centre) -> 3.5 mm -> `SHAPE` (refused; conservative). So
   the camera-over-pivot setup matters more with the short horn; `ring3 witness-frame` reports it for free.
3. Raw error up to ~7 deg near 95 with 15 mm (5 deg with the old 18 mm). Zone checks at the centres keep a
   >= 15 deg margin to the bands; a wrong-zone PASS needs a reading > 10 deg off. The 3.0 deg residual is only
   checked at the 5 points.
4. **agentsh ends a request after its 8th ACTION** (it does not wait for a FINAL), so the last move of a
   request cannot be followed by a witness op if it is the 8th ACTION. Calibration and checks therefore plan
   <= 7 moves and read the start from telemetry instead of a `cell_status` ACTION. A Muse request that wants
   to verify must also leave room: agent-v3 says witness counts as one of the 8 operations.
5. Telemetry EVENT frames reach COM2 only while a request is open; `Runner.fresh_servo` waits for one.
6. `ring3 tape` set `func=` instead of `fn=`, so it raised `AttributeError` (T-04 code). Fixed at the lead's
   request (T-05a needs it) with the regression test `test_cli.py::test_tape_prints_tape_lines`.
7. The witness record's `measured_zone` for a hand-held arm can be a real zone (MID) while the board says 50:
   that disagreement is the evidence.

## `[UNVERIFIED]` (still)
Everything in Part A's list (real SAM line grammar, `c`, box convention, mask encoding, envelope), the
Windows helper on Windows, and the 5 mm camera-axis advice *(prov.)*.

## Live steps (the user's, D-57) — commands in the session report and in STATUS
1. Windows: OpenCV + the helper, `ring3 witness-frame` until markers 4/4 and the axis is <= 5 mm off.
2. `ring3 calibrate` (5 images, ~$0.0125) -> `calibration.txt`.
3. `ring3 calibrate --checks` rounds: normal x2, held, unplugged (signal wire), marker covered, camera nudged
   -> `checks.md` (table + image hashes; images showing a hand stay in `runs/`).
4. `ring3 run --pack agent-v3 --witness-dir ... --calibration ...` with `--budget-usd 0.50`: "move the gauge
   to LOW and confirm it really moved", then the same to MID with the arm held at LOW -> FAIL -> halt.

## First real calibration: `cal-20261103-194227` (user, 2026-11-03) and diagnosis (builder, no paid call)
Run: bridge first (D-70), camera 1.3 mm from the pivot, horn re-seated at 90, lamp from the far side. Every
servo move `ALLOW`, `CHAIN OK` (1046 records), $0.0125 (5 images). **All 5 points `CAN'T VERIFY: NOT_FOUND`
("SAM found no arm") -> witness disabled** (`no measurement at commanded [60, 70, 90, 110, 120] deg`). Card
stop rule: the arm was not found with the short prompt.

1. **SAM replies.** `NOT_FOUND` with that detail is raised only for zero records, and no witness record has a
   `sam_line_blob` (written only for a non-empty line): every reply passed the envelope check (`completed`,
   exactly one `output_text`) and its line was empty = "concept not present", a documented normal result.
   The parser and the arm filter rejected nothing; there were no records to compare with the grammar.
   **Gap:** the raw reply body was not logged, so its bytes can't be shown. Fixed: `SamClient.last_reply`
   -> `WitnessResult.sam_reply` -> `sam_reply_blob` in every witness record, also when the reply does not
   parse (and `spend_uusd` is now kept in that case too).
2. **Crops.** Marker fit 1.03 mm; the frame resolves **4.84 px/mm** (horn ~73 px in the 920 x 570 crop).
   Grey levels (0-255): white paper 187-199, inner dial 150-155, horn/hub 152-170. The horn is **darker
   than the paper and almost the same grey as the dial under it** (lit from the far side, so the camera sees
   its unlit face; soft focus). Median-subtracting the five crops leaves only a ~6 mm pale patch beside the
   hub that changes between angles (mean change 2-3.5 grey levels in the pivot region; 60 vs 110 differ by
   1.1, the noise floor); no 15 mm bar is distinguishable at any angle, also in a 12 px/mm re-crop. SAM's
   "not present" is a fair answer for these images: the problem is the image, not the parser or the filter.
3. **Proposal (smallest first; 1-2 are free to check with `ring3 witness-frame`, which now prints px/mm and
   saves `pivot.jpg`, a 20 px/mm zoom of the pivot):**
   1. Light the horn's top face: lamp next to the camera / overhead, not from the far side.
   2. Contrast: blacken the horn's top face with a permanent marker (no cutting, no fabrication; it changes
      the horn's colour, so it is a lead call next to D-24), phrase then "black plastic arm".
   3. Camera a little closer (all 4 markers span 186 x 142 mm: at most ~6.5 px/mm at 1080 p).
   4. Phrase and crop: test with `ring3 calibrate --probe` (new; paid; <= 5 images; saved frames, no kernel)
      on a new frame after 1-3, or on the saved 90 deg frame as a control.

## Real SAM format (probes `probe-20261103-202522`, `probe-20261103-202557`, user, 2026-11-03)
Arm probes ("black plastic arm", "servo horn"; full and tight crop; blackened horn): 4 empty replies (802 B
envelopes, `output_text` ""). Control probe: "blue servo motor" and "black square" returned records that the
Part A parser rejected (`record 1 not understood at byte 0`). Raw replies (no key, no `Authorization`/`Bearer`,
no long tokens) are committed as `tests/witness/recorded/`. First bytes of the servo reply's text:
`<0f>0<|box;x1=393;y1=425;x2=563;y2=569;w=920;h=570;c=0.8672199249267578|><|mask;x=0;y=0;c=0.8672199249267578;data=145,171,~!!!T@#K{$iEAqi$e'q#...`

**The real output matches the official page** (https://dev.meta.ai/docs/sam/reading-segmentation, re-read
2026-11-03); **D-56's paraphrase on the card, which the Part A parser followed, differs in five points:**
| | card / Part A parser | official docs + recorded replies |
|---|---|---|
| line start | records directly | frame marker `<0f>` (`FRAME ::= "<" INT "f>"`) |
| id | `<id> <\|box` (space) | `<id><\|box` (no space), ids from 0, records joined by `,` |
| box `w`/`h` | box width/height | image width/height, repeated on every record |
| `x2`/`y2` | exclusive (assumed) | inclusive: mask `data=H,W` = `y2-y1+1`, `x2-x1+1` (docs example 353..453 -> 101; recorded 393..563 -> 171; `x2=919` in a 920 px crop) |
| `c` | <= 6 decimals | 16 decimals seen |
| payload | opaque, any printable | `~` (lossless) or `!` (one-bit) + base85; alphabet = printable ASCII minus `" , ; < > \ \| } ~` (inferred: exactly these 85 in 12,520 recorded payload characters; the docs say only "base85") |

**Parser fixed** (`witness/sam.py`): frame `<0f>` required (other frame -> SAM_FORMAT), id + `<|box` with no
space, `w`/`h` must equal the image sent, `0 <= x1 <= x2 < W` (inclusive), mask origin literally `0`, mask
`H,W` must equal the box, encoding `~`/`!`, payload only from the 85-character alphabet (so a payload can't
contain `|>` and a record can't swallow the next one), separator exactly `,`, `c` <= 20 decimals, <= 16
records, unique ids; `SamRecord.x2/y2` stay exclusive edges (value + 1), so `angle.py` is unchanged.

**Recorded replies through the fixed parser** (full crop, the box -> gauge-mm mapping the arm angle uses):
| phrase | record | gauge mm | matches | arm filter |
|---|---|---|---|---|
| black square | 1 | x 63.5..91.9, y -21.9..-12.3 | marker 2 (63..93, -42..-12) clipped by the crop at -22: within 0.5 mm | pivot not inside |
| black square | 2 | x 63.7..91.9, y 70.3..92.1 | marker 1 (63..93, 70..100) clipped at 92: within 0.7 mm | pivot not inside |
| black square | 0, 3 | 5.0 x 4.6 and 5.0 x 2.0 mm | single 5 mm cells inside marker 0 | pivot not inside |
| blue servo motor | 0 | x -13.5..20.7, y -21.9..7.1 (34 x 29 mm), c 0.867 | servo body outline (-6.1..6.1, -17..5.5) + putty on both sides (D-64) | SHAPE: far side 30.1 mm |
So the coordinates are read right to < 1 mm on real replies; the arm probes were genuinely empty.

Test-program changes (logging rule): **6** `test_sam_parser.py` ACCEPT/REJECT rewritten to the official
grammar (cause: the old cases encoded the card's paraphrase); every old rule kept or tightened, new rejects
for the old grammar itself, frame != 0, `w`/`h` not the image, inclusive overflow, mask/box mismatch, unknown
encoding, payload characters outside the alphabet; the four "odd payload" accepts (commas, `|>`, `<|box`
inside) became rejects; plus 3 tests on the recorded replies. **7** `synth.sam_line` writes the real grammar
(ids from 0, image `w`/`h`, inclusive corners, hex stand-in payload); `FAR_BOX` (INV-14) and
`test_sam_client.LINE` likewise. Expectations unchanged: every verdict and angle in `fixtures.md` is the same
(only the displayed SAM lines and two detail texts changed).

## Horn height after the D-78 remount (calculation, no SAM)
D-78: the servo had been on its side (horn seen end-on: the x -7.7..+4.4 mm stub in frame `7855cbe2624a`,
which is void). Remounted on its back, shaft toward the camera on the PIVOT mark, horn flat, plane ~25 mm above
the paper. `height_check.py` / `height_check.txt`: with a pinhole camera at H whose axis is |c| from the
pivot, the whole horn plane maps back to the paper scaled by k = H/(H-25) about the camera axis, so the
measured box centre becomes k m - (k-1) c. H = 281 mm (4.84 px/mm and the C922's 78 deg diagonal FOV, f =
1360 px). **At |c| = 1.3-1.7 mm the worst raw error is 1.22-1.59 deg (2.24 deg even at H = 200 mm), and
<= 0.25 deg after the D-72 linear fit; under 3 deg, so no height parameter is needed.** The analytic bound
agrees: (k-1)|c| / (k |m|min) = 0.098 x 1.7 / (1.098 x 5.44) = 1.60 deg. The larger risk is the shaft not
on the PIVOT mark: 0.5 / 1 / 2 mm off -> 6.9 / 12.2 / 23.3 deg raw, 1.3 / 2.6 / 7.0 deg after the fit
(2 mm makes the calibration fail its own 3.0 deg limit, which disables the witness rather than mis-judging).
The apparent hub moves only (k-1)|c| = 0.17 mm from parallax, so `pivot.jpg` shows the placement directly.

## Hub-offset readout in `ring3 witness-frame` (D-79; local image analysis, no SAM, no spend)
`witness/service.py` `find_hub`: in the 20 px/mm pivot zoom, white = bright (>= 0.85 x the 95th-percentile
value) and unsaturated (S <= 60); a candidate must not touch the zoom border (the white paper does), be 2-5 mm
in radius, round (circularity >= 0.75, fill of its enclosing circle >= 0.70) and centred within 6 mm of the
printed pivot. Exactly one candidate -> `hub offset: dx .. mm, dy .. mm, d mm OK` (d <= 0.5 mm) or `MOVE: move
servo LEFT/RIGHT/UP/DOWN x mm (as seen in pivot.jpg, dial on top)`; none or several -> `hub offset: not
found (...)`, never OK. `pivot.jpg` gets a green 0.5 mm ring on the printed pivot and the detected cap in red;
`witness-frame` exits 0 only with the camera axis and the hub both OK. The reading includes the parallax
shift of the cap, (k-1)|c| ~ 0.17 mm at 1.7 mm camera offset (height check above).
Tests: per direction (+-1.2 mm in x and y) and a centred case (0.28 mm -> OK) under 3 camera/light variants,
all within 0.2 mm; no cap, cap merging with white paper, two caps, cap 9 mm away -> not found. **Real frames:**
all 126 recorded frames (calibration, probes, every `runs/witness-frame-*`) predate the D-78 remount and say
`not found` (or stop at missing markers); none says OK. A positive detection on a real frame is therefore
**not yet shown**: the first `witness-frame` after the remount is the real test (if it says not found,
measure `pivot.jpg` by hand).

## Stale frames (D-80): fresh-frame proof from the helper
`witness-frame-20261103-211527` showed the side mount after the remount, while 211814 / 211823 already show
the upright servo. Each frame carried its own request nonce, so the old protocol only proved when the helper
*wrote* the file, not when the camera *captured* it: an OpenCV backend can hand out a queued frame on
`grab()`. Fix (`tools/witness_capture_win.py`, `witness/capture.py`):
- helper: `CAP_PROP_BUFFERSIZE=1` where supported; per request it notes when it saw the request, then grabs
  and discards; a grab that blocks >= 12 ms waited for a new sensor frame ("live"); it captures only after
  >= 6 grabs, >= 2 live ones and >= 100 ms since it saw the request, else (3 s or 300 grabs) writes nothing;
  the JPEG comment is now `ring3-frame:v2;id=<request id>;seen_ns=..;cap_ns=..;live=..`.
- bridge (`check_frame`, used by `witness-frame`, `calibrate` and the live witness): `CAN'T VERIFY:
  STALE_FRAME` for a frame without the v2 proof (an old helper's nonce-only frame says "restart it"), another
  request id, a capture before the helper saw the request, a capture before the bridge's request (2 s
  clock-skew allowance; the helper's times are the Windows clock), fewer than 2 live grabs, an old file, or
  an undecodable one.
- tests: mismatched id, buffered (captured 600 s before the request), captured before seen, no live grab,
  old helper, no comment, undecodable, old file -> STALE_FRAME; a correct fresh v2 frame accepted; helper
  round trip with a camera that queues 4 frames; a camera that never blocks -> no file; the flush loop is
  bounded even with a bad clock (a hang found while writing the tests, fixed).
**The Windows helper must be restarted** to pick this up (it is a long-running Windows Python process that
reads the script once); until then every capture is `STALE_FRAME` ("nonce-only frame from an old helper").

### Hub readout v2: the screw hole (lead request after `witness-frame-20261103-213006`)
On the real upright frames the white hub merges with the surrounding white plastic into a ~15 mm irregular
blob, so the round-cap test said `not found` everywhere. `find_hub` now finds the **dark screw hole** in the
middle of the hub (= the shaft centre): a 3 mm black-hat of the brightness, threshold 40, then candidates of
0.5-2 mm equivalent radius (the thresholded core of a ~1-1.5 mm blurred hole is ~0.7 mm), circularity >= 0.6,
within 6 mm of the printed pivot, and >= 70 % white plastic in the ring 0.5-2 mm outside it (rules out the dark
lines at the horn's edges). Same rule: exactly one candidate, else `not found`, never OK. **Real frame 213006:
`hub offset: dx -2.3 mm, dy -0.1 mm, 2.26 mm MOVE: move servo RIGHT 2.3 mm, UP 0.1 mm`** (lead's hand
measurement about -2.4 / 0.0). The other upright frames of that session (211814...212913) and the side-mount
fixture still say `not found` (fails safe; not tuned further, time-box). Test-program change 8: the synthetic
hub scene draws the screw hole (r 1.2 mm) every real horn has; the negative cases became no hole / two hubs /
hub 9 mm away / no hub; the recorded side-mount frame's expected text follows the new reason; new test on the
real frame (committed as `tests/witness/recorded/frame-upright-213006.jpg`).

### Helper timeouts after the D-80 restart (user, 2026-11-03): v3 freshness proof
Symptom: every `witness-frame` timed out (`no frame within 5.0 s`), the helper printed nothing per request,
the folder was left empty. Two causes in the D-80 helper could each produce exactly that, silently:
(a) the only proof was ">= 2 grabs that waited >= 12 ms". If a backend's `grab()` returns the newest
frame without waiting (DSHOW, which the helper selects, is believed to behave so `[UNVERIFIED]` on this
C922), that never passes; worse, instant grabs burnt the 300-grab limit in milliseconds (reproduced in a
test); (b) the helper dropped requests whose file time looked > 10 s old, comparing a WSL-stamped file time
with the Windows clock, and the bridge compared Windows capture times with its clock within 2 s: WSL2's
clock can drift from Windows'. Fix (`tools/witness_capture_win.py`, `witness/capture.py`):
- two same-clock proofs: queue backends -> >= 2 grabs that waited; latest-frame backends -> >= 250 ms after
  the helper saw the request and the frame differs from one retrieved after seen (a frozen stream repeats it
  exactly, so it is refused); grabs that do not wait are paced 5 ms, so the 300-grab limit spans >= 1.5 s;
  give-up at 3 s; the comment is v3 (`...;live=..;changed=..`);
- no file-time comparison in the helper (requests present at start are deleted instead); the bridge's
  cross-clock checks remain only as a gross-age guard (120 s); the same-clock proof plus causality (the
  helper can see a request only after the bridge wrote it) carries the freshness;
- one log line per request: request id, camera open time, backend, buffer-size result, grabs, waiting grabs,
  grab-time range, changed, `wrote frame (ms)` or `wrote nothing: <reason>`; `--backend dshow|msmf|any`;
  `--selftest` (3 captures without a request, same lines, exit 0 only if 3/3);
- bridge timeout `REQUEST_TIMEOUT_S` = 3.0 + 0.05 + 1.0 + 2.0 s margin = 6.05 s (test: >= helper limit +
  poll + 1 s + 1 s).
Tests: queued frame (no waiting grab, unchanged) and a too-early latest-frame proof -> STALE_FRAME; both
proofs accepted; never-waiting camera with a live stream -> frame via the latest-frame proof; frozen stream
-> nothing and a log line; the grab limit and give-up bound with a fake clock; old request file time still
served; leftovers deleted at start; per-request log line; selftest 3/3 and 0/3.
Not shown yet: the real C922 behaviour under DSHOW and MSMF; `--selftest` prints it.

### 244 ms refusal on the real C922 (08baf2c) -> one proof on recorded fields
`--selftest` passed 3/3 on DSHOW (0 waiting grabs, changed=1), but `witness-frame` was refused: `no freshness
proof (0 waiting grab(s), changed=1, 244 ms after seen)`. The helper decided "250 ms" on its monotonic clock
from a reference taken just before it recorded `seen_ns`; the bridge checks `cap_ns - seen_ns` on the recorded
wall-clock fields (Windows' wall clock can also be coarse). Fix: `proof_ok(live, changed, seen_ns, cap_ns)`
with the same arithmetic in `witness/capture.py` and `tools/witness_capture_win.py` (a test keeps them equal on
a grid of edge values); the helper writes only when its `proof_ok(..., margin 10 ms)` holds on the exact
values it records (>= 260 ms for the latest-frame proof; capture strictly after seen for both), otherwise it
keeps grabbing. The bridge's 250 ms is unchanged. Regression test: recorded `seen_ns` 6 ms behind the helper's
reference, non-waiting grabs, changing frames -> the helper does not write at a recorded 244 ms, and the
frame it writes passes `capture.proof_ok`. Test-program change: `test_helper_counts_live_grabs...` gave the
helper a constant wall clock (`wall_ns=lambda: 5`); with the proof now on recorded fields that can never show
capture after seen, so it gets a wall clock that follows its fake time (expectation unchanged).

### Phrase "black bar" (probe `probe-20261103-220356`, upright mount, arm taped black)
Calibration `cal-20261103-220157`: NOT_FOUND x5 with "white plastic arm". The probe on the latest frame:
"black bar" and "black rectangle" -> 11 records each, "black stick" / "black lever" -> 0. Decoded (free):
"black bar" record **id 4** (c 0.58) is the arm: x -3.9..+4.7, y +5.5..+20.7 mm, centre direction 88.3 deg,
near end **5.5 mm** from the pivot (the tape stopped at the white hub), so the unchanged pivot-in-box filter
refuses it (SHAPE). The other 10 are the 8 black boundary bands (~55 mm out, two per zone edge) and two marker
parts. "black rectangle" returned the same bands and markers but not the arm. `DEFAULT_PROMPT` is now
"black bar" (also the probe's default); test on the recorded reply (`tests/witness/recorded/
sam-black-bar-upright.json`): exactly one record near the pivot, at 5.5 mm and ~88 deg, and `arm_angle` still
says SHAPE. Note: the arm's confidence 0.58 is close to the 0.50 floor (`min_conf_ppm`, unchanged).

### Recalibration `cal-20261103-220845` ("black bar", tape over the hub, ~6 mm tail): SHAPE x5 = arm not returned
Decoded (free): every reply has 10 records, the 8 boundary bands and 2 marker parts; the nearest is 54.6 mm
from the pivot. **No arm record at any point**, so no shape threshold rejected an arm; the SHAPE verdict is
"only non-arm records". The crops show the black arm moving correctly (up-right at 60, up-left at 120).
Geometry check (model: bar 8.6 mm wide as measured in `probe-20261103-220356`, tip 15 mm, parallax 1.098,
60-120 deg) through the unchanged filter: tail 6 mm -> passes with thin margins (centre >= 4.9 vs 4.0, nearest
edge <= 7.4 vs 8.0); tail 1-2 mm -> passes with wide ones (centre >= 7.1-7.7, edge <= 3.3-4.3); far side <= 20.7
vs 25.75 in all cases. No threshold change proposed. Open: SAM's detection of the fully taped arm with
"black bar" (it found the half-taped arm at c 0.58).
