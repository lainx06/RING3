# T-07 evidence: camera witness, Part A (offline)

Builder: Opus 5.5 / high, auto mode (D-57) · 2026-11-02, finished 11:20 ET, second terminal (T-03b ran in the
other one on disjoint paths; scoped test runs only). **Offline only:** fake transport, no camera, no paid call,
no `MODEL_API_KEY`, no network except the package install. No change to `kernel/`, `user/`, `firmware/` or
`bridge/ring3bridge/models/`. Part B (camera, real cell, paid SAM calls) has not started.

## Acceptance (Part A)
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | Gauge PDF + geometry generated; INV-14 tests and the synthetic-fixture tests pass; parser rejects malformed SAM lines; ledger reserves one image before every SAM call (fake transport) | **PASS.** `hardware/gauge/make_gauge.py` writes `ring3_gauge.pdf` (Letter), `ring3_gauge_a4.pdf`, `ring3_gauge.png` (300 dpi) and `geometry.json`, deterministically; tests check geometry.json is in sync, markers are detected in the PNG **and in the PDF's own raster** within 0.3 mm of geometry.json, and the true page size. `test_inv_14_cant_verify_cases` (13 cases) + `test_inv_14_never_upgraded` + `test_inv_14_not_in_decision_path` pass. Fixtures: 10 verdict cases × 3 camera/light variants, 12 accuracy variants, dead-zone and off-axis cases. Parser: **48 malformed lines + 8 malformed envelopes rejected** (`SAM_FORMAT`), 16 accepted. Spend: the fake transport re-reads the ledger file **at post time** and fails the test unless exactly one image (2500 µUSD) is reserved and unsettled; no price / no budget / run cap / project cap → nothing sent. **witness 253 passed, 2 skipped by design; + INV-01 (29) = 282 passed, ~10 s.** 15/15 mutations caught. | `pytest.txt`, `fixtures.md`, `gauge.png`, `mutation.txt` |
| A2–A4 | Part B | not started | |
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
