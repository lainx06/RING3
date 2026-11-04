# T-16 evidence: vision witness (Muse Spark reads the gauge zone from the camera)

Builder: Fable 5.1 / high, worktree `~/dev/RING3-t16`, branch `t16` (D-98) · 2026-11-04 · **offline only by
the builder:** fake transports, synthetic frames, the simulated cell in QEMU; no camera, no paid call, no
`MODEL_API_KEY`, no network except the four doc pages read. No change to `kernel/`, `user/`, `firmware/`,
`abi/` or `decide()`: policy hash `4101a8cc30507872...` unchanged. The hardware steps (A3, A4) are the user's
(D-57) and are recorded below as measured when they arrive.

## Acceptance
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | Step-2 offline tests fail before and pass after; full suite passes; policy hash unchanged | **PASS.** Before: both test files error at import (`pytest-before.txt`). After: `tests/witness/test_vision.py` 64 + `tests/bridge/test_vision_secrets.py` 1 = **65 passed**; 2 QEMU tests on the simulated cell (`test_witness_loop.py::*vision*`) passed; full default suite **980 passed, 6 skipped (hardware tests, 2 by design), 221 s, 0 PANIC** (`suite.txt`; run while the last QEMU test edit was pending, the two vision loop tests re-run afterwards: 2 passed); policy hash `4101a8cc305078722afb56a4eb09e2b4c3705e2d2c697b418c7ef02885cdef0b` (= T-15); `git diff --stat 76ad29f -- kernel user firmware abi` empty | `pytest-before.txt`, `pytest-after.txt`, `sim-vision-loop.txt`, `sim-vision-zonecheck.txt`, `step5.txt` |
| A2 | Request shape matches the official docs, quoted | **PASS.** See "Docs extract" and "Request shape"; `test_request_shape_matches_the_documented_fields` pins every field | this file, `bridge/ring3bridge/models/vision.py` docstring |
| A3 | Hardware zone check by the user, N/6 with every reply logged | **3 of 6 PASS** on `zc-20261104-153656` (every reply logged; table below). Not 6/6: the two 130 images show the arm on the 110 boundary band (servo travel ~0.75 of the command, measured), one 50 image read UNSURE. v2 pack (accepted, D-103) and the servo map in place; re-test pending the user | "Zone check", `zc-153656/` |
| A4 | If 6/6: live runs (b) and (c) with verdicts and the agent's next action | **OPEN: after A3** | to fill: "Live runs" |
| A5 | Commits only this card's paths; worktree; "ready to merge" reported, merged only on the user's word | PASS for the builder's part: branch `t16`, paths listed under "Files"; not merged | `git log t16` |

## Docs extract (official pages, read 2026-11-04 through a fetch tool; the exact field names below are the
## ones the pages state; nothing was guessed)
- **Image understanding**, https://dev.meta.ai/docs/image-understanding: Responses API `POST
  https://api.meta.ai/v1/responses`; `model` and `input`, an array of items; each item `"type": "message"`,
  `"role": "user"`, a `content` array; a text block `"type": "input_text"` with `text`; an image block
  `"type": "input_image"` with `image_url` "as a plain string (a public URL or a base64 data URL)" (or
  `file_id`); images "are only processed in user-role messages"; accepted formats `image/jpeg`, `image/jpg`,
  `image/png`, `image/gif`, `image/webp`, `image/x-icon`; inline `image_url` up to 50,000,000 bytes per image;
  at most 50 images per request; "the model may resize images". Tokens: "a 1x1 image ≈ 3 tokens", "a ~1280px
  image adds roughly 1,300–1,500 tokens", "a short text-only request is ~110 input tokens"; "For an exact count
  of any request (including images and files), call `POST /v1/responses/input_tokens` with the same `input`."
  The examples use `muse-spark-1.3`.
- **Responses schemas**, https://dev.meta.ai/docs/api-reference/responses/schemas: `InputImageContent` =
  `type` enum `input_image` (required), `image_url` string|null ("Fully qualified URL or base64-encoded data
  URL"), `file_id` string|null, `detail` enum `low` / `high` / `auto` / `original`, "Defaults to `auto`".
  `text` (`ResponseTextParam`, optional) has one field `format`, a union of `ResponseFormatText` (`type`
  `text`, the default), `TextResponseFormatJsonSchema` and `ResponseFormatJsonObject` (`json_object`, "the
  older JSON mode", `json_schema` recommended). `TextResponseFormatJsonSchema`: `type` enum `json_schema`
  (required), `name` string (required; a-z, A-Z, 0-9, underscores, dashes, up to 64), `schema` object
  (required), `strict` boolean|null ("Defaults to `false`"; "When `true`, the model must produce output that
  strictly conforms to the JSON Schema in the `schema` field"), `description` optional.
  `TokenCountsResource` = `object` enum `response.input_tokens`, `input_tokens` integer ("Tokens in the fully
  rendered model input").
- **Structured output**, https://dev.meta.ai/docs/structured-output: "text.format is the Responses API
  parameter for structured output." "The model constrains token generation to produce only valid JSON matching
  your schema." "This is not post-processing: decoding itself is constrained, so the output is guaranteed to
  conform." Strict subset: root a plain object, no top-level `anyOf`/`oneOf`/`allOf`/`enum`/`not`; every object
  `additionalProperties: false`; every object's `required` lists all of its `properties`. (The page's only
  worked example is Chat Completions `response_format`; the Responses nesting comes from the schemas page.)
- **Token counting**, https://dev.meta.ai/docs/token-counting: `POST /v1/responses/input_tokens` "returns the
  number of tokens in the fully rendered input"; "Send the supported fields from the body you would send to
  /v1/responses, at minimum model and input"; response `{"object": "response.input_tokens", "input_tokens":
  <integer>}`; image tokens "depend on the selected model and version, prompt template", "Fixed per-image or
  prompt-overhead estimates are not portable across deployments", "Use input_tokens instead of estimating from
  image count or dimensions". Whether the count endpoint is billed is not stated.
- **Pricing**, https://dev.meta.ai/docs/pricing-rate-limits (no date on the page): `muse-spark-1.3` Standard
  $1.25 / 1M input, $4.25 / 1M output, $0.15 / 1M cached input (unchanged from D-55); no separate image-input
  price for Muse Spark (the only image price is Muse Image generation). Rate limits 3,000 requests / min,
  4,000,000 tokens / min per team.
- **Responses protocol**, https://dev.meta.ai/docs/protocols/responses: `max_output_tokens` "bounds tokens
  generated (reasoning plus visible output) and has a minimum of 16"; `store: true` is the default;
  `reasoning.effort` accepts none/minimal/low/medium/high/xhigh (D-61: "none" is HTTP 400 for Muse Spark).

## Request shape (`models/vision.py::build_request`, canonical JSON, pinned by a test)
```
{"input": [{"content": [{"text": "<USER_TEXT>", "type": "input_text"},
                        {"image_url": "data:image/jpeg;base64,<crop>", "type": "input_image"}],
            "role": "user", "type": "message"}],
 "instructions": "<docs/context-packs/witness-vision-v1.md>",
 "max_output_tokens": 2048, "model": "muse-spark-1.3", "reasoning": {"effort": "low"}, "store": false,
 "text": {"format": {"name": "ring3_gauge_zone", "schema": {
     "additionalProperties": false,
     "properties": {"arm_visible": {"type": "boolean"},
                    "zone": {"enum": ["OUT_LOW", "LOW", "MID", "HIGH", "OUT_HIGH", "UNSURE"], "type": "string"}},
     "required": ["zone", "arm_visible"], "type": "object"}, "strict": true, "type": "json_schema"}}}
```
`USER_TEXT` = "Which zone does the black arm point at? Reply with the JSON object only." `detail` is not
sent (the documented default `auto`). The reply is read like every Muse Spark reply (`status` must be
`completed`, exactly one `output_text`, usage from `usage`), redacted by the same function as `muse_spark.py`
(`redact_reply`, factored out so the two cannot drift; S4/D-93 unchanged), then validated by
`witness/vision.py::parse_zone` regardless of the server-side constraint: exactly one JSON object, exactly
the keys `zone` and `arm_visible`, `zone` one of the six values, `arm_visible` a JSON boolean, printable
ASCII, at most 256 bytes, surrounding whitespace only. Anything else is `CAN'T VERIFY: VISION_FORMAT`, never
a retry.

## Reservation (INV-09; `[vision]` in `config/models.toml`)
Per call, reserved before the call and settled on the reported usage: **5,390 input tokens** (2,000 image
ceiling + 3,390 bytes of request outside the image, 1 token per byte as D-52) + **2,048 max output tokens** =
**$0.015442**; the 6-image zone check reserves at most **$0.092652**. The image ceiling 2,000 is above the
docs' "~1,300–1,500 tokens for a ~1280 px image" for a 920 x 570 crop; the token-counting page warns that
per-image figures are not portable, so **the first live replies' `usage.input_tokens` (in `spend_settled` /
`model_reply`) are the check** and go in the table below. The count endpoint is documented and would be
exact, but costs an extra authenticated call per check; not used (`[UNVERIFIED]` whether it is billed).
`max_output_tokens` 2,048 with `reasoning.effort` low: D-60 saw replies spend 2,048 reasoning tokens at the
default effort; a reply cut off at the cap is `CAN'T VERIFY: VISION_FORMAT (reply status incomplete
(incomplete:max_output_tokens))`, reported, not retried.

## What was built
| Piece | Files | Notes |
|---|---|---|
| Adapter | `bridge/ring3bridge/models/vision.py` | `VisionClient.ask(instructions, user_text, jpeg)`: build -> reserve (explicit input-token count, new optional `Ledger.reserve(..., input_tokens=)`) -> `spend_reserved`, `model_request` (`purpose=witness_vision`, `pack_sha256`, `image_sha256`, the body as a blob) -> post -> `model_reply` -> settle -> redact. Refusal / 401 / 403 / other status / transport error -> `ModelStop` (budget, model_auth, transport, timeout), the client stops for good. |
| Witness backend | `bridge/ring3bridge/witness/vision.py` | `VisionWitness.check`: commanded zone (COMMAND_RANGE / COMMAND_BOUNDARY before anything) -> gate (`ZoneCheck`; disabled -> CALIBRATION, no frame, no spend) -> fresh frame (STALE_FRAME) -> markers + rectified crop -> `mask_markers` (the four ArUco squares painted paper-tone: the dial alone) -> JPEG 920 x 570 (<= 1280) -> one call -> `parse_zone` -> `judge_zone`. Inside the INV-14 static scan (no link, no key words, no writes). |
| Reasons | `witness/__init__.py` | `VISION_FORMAT`, `NOT_VISIBLE`, `UNSURE` added to the CAN'T VERIFY set. |
| Result / record | `witness/pipeline.py`, `run.py` | `WitnessResult.backend`, `pack_sha256`, `arm_visible`, `vision_reply`; the `witness` record carries `backend`, `pack_sha256`, `arm_visible`, `vision_reply_blob`; the observation's second line ends with `backend=sam|vision`; a vision `ModelStop` stops the request and run like a `SamStop`. |
| Zone check | `bridge/ring3bridge/zonecheck.py`, `calibration.py` (`rounds`), `cli.py` | `ring3 calibrate --witness-backend vision`: 2 console requests, targets 50, 90, 130 then 90, 50, 130 (<= 7 moves each, every move through the kernel), one image per target; `zone_check` is ok only on 6/6 PASS by this backend with this pack, in order; `zonecheck.json` + a `zonecheck` record (file SHA-256). `ring3 run --witness-backend vision --calibration <zc-run>` re-verifies the chain, the file hash, the pack and gauge hashes and recomputes the verdict from the rows. |
| CLI | `cli.py` | `--witness-backend sam|vision` on `run` and `calibrate` (default `sam`, unchanged); dry runs print the reservation. |
| Pack | `docs/context-packs/witness-vision-v1.md`, `-v2.md` | v1 SHA-256 `bf8f6102e35d69eb...` (the 2026-11-04 zone checks), v2 `aa51546543357dd2...` (the once-only revision, below), both pinned by a test; describe the printed gauge (zones, labels, OUT regions, black bands, the black arm on the pivot); ask for the zone only ("Never report an angle"). |
| Config | `config/models.toml` `[vision]` | id, endpoint, `max_output_tokens` 2048, `reasoning_effort` low, `image_tokens_reserve` 2000, the `[model]` prices (a test keeps them equal). |
| Event log | `eventlog.py` | record type `zonecheck`. |

## Adaptations from the card's wording (reported, none weakens a rule)
1. Reason names: a commanded angle inside a band is `COMMAND_BOUNDARY` and a frame without its four markers
   is `MARKERS`, the names the witness already uses (the card writes BOUNDARY / NO_MARKERS). No model call in
   either case.
2. `SPEND`: a refused reservation stops the request and the run (`STOP budget`), as it does for SAM (D-58,
   INV-09), instead of a CAN'T VERIFY the model could continue past.
3. The existing dial crop keeps fragments of the four markers in its corners (T-07's `fixture_crop.jpg`
   shows them); the vision backend paints the marker squares over before sending, and a test checks that no
   marker is detectable in the image sent while the dial semicircle is pixel-identical to the plain crop.
4. The zone check is one `ring3 calibrate --witness-backend vision` run of two console requests (the kernel
   ends a request after its 8th ACTION; 50, 90, 130 then 90, 50, 130 keeps each request at <= 7 moves).

## Tests (step 2; all offline)
`tests/witness/test_vision.py` (64): the strict schema (22 rejected texts: extra field, wrong types, two
objects, prose before/after, code fence, unknown / lower-case zone, missing keys, duplicate key, null, array,
empty, non-ASCII, a number as the zone, too long; 4 accepted); the verdict table; band / out-of-range
commands take no frame and make no call; disabled gate, stale frame and a covered marker make no call; one
PASS end to end (the image sent is the masked crop, reserved before the post with the worst-case amount
checked from the ledger file at post time, settled on usage, pack hash in the record, chain verifies);
FAIL / NOT_VISIBLE / UNSURE / VISION_FORMAT (prose, extra field, incomplete, non-JSON) with the reply kept;
never upgraded; run cap / no budget / project cap refuse before anything is sent; 401 / 403 / 500 / timeout /
network stop the run and the client; no retry after a bad reply; the request shape; config prices equal
`[model]`'s; the pack pinned; zone-check targets; `zone_check` ok only on 6/6 (one UNSURE / FAIL / NOT_VISIBLE,
one short, empty, wrong order, SAM results, another pack -> disabled with the reason); file round trip,
tampering, another pack or gauge refused; script rounds; observation names the backend; CLI dry runs and
argument checks. `tests/bridge/test_vision_secrets.py` (1): real `HttpsTransport`, hostile fake server echoing
a sentinel key plain and escaped: nowhere in replies, records, blobs, exceptions, reprs; `Authorization` /
`Bearer` never in the log. `tests/test_witness_loop.py` (+2, QEMU + simulated cell): PASS then a held arm ->
FAIL -> the model halts, zero COM2 frames for the witness ops, `backend=vision` in the model's observation;
the zone check through the kernel, every move ALLOW, 6/6, file saved and loaded, a held target -> 5/6 disabled.
INV-14's static scan now covers `witness/vision.py` (import allowlist unchanged: the backend sees the adapter
only through a `Protocol`).

### Test-program change (logging rule)
| # | Test | Why the *test* had to change | Change | Expectation |
|---|---|---|---|---|
| 1 | `tests/witness/test_part_b.py::test_no_move_observation_text` | It asserts the exact observation text; the card asks the observation to name the backend, so the second line gained `backend=sam` | `"measured_deg=none\n"` -> `"measured_deg=none backend=sam\n"` | unchanged: NO_MOVE, no frame, no spend, same words otherwise |

## `[UNVERIFIED]` (no call to Meta was made by the builder)
- (closed by zc-153656: 1,406 input tokens per call, under the reservation; strict json_schema accepted.)
- Whether `POST /v1/responses/input_tokens` is billed (not used).
- Whether the model reads the printed zone reliably: that is A3.

## Live steps (the user's, D-57) — commands and reservations
Prerequisites as for T-07: the Windows capture helper running on the shared folder, `uv run ring3
witness-frame --witness-dir /mnt/c/...` showing markers 4/4, the horn black, the arm free. Start the bridge
first, then `make run-cell` (D-70). Budgets are per run; the project cap is $30.
- (a) **Zone check** (6 images, at most $0.092652 reserved, ~$0.03 expected to settle):
  `uv run ring3 calibrate --witness-backend vision --witness-dir /mnt/c/<folder> --budget-usd 0.10`
  then at the RING3 console: `zone check 1`, then `zone check 2`. It prints the 6-row table, "N of 6 PASS"
  and the run id `zc-<time>`; exit 0 only on 6/6.
- (b) **Live run** (only if (a) is 6/6): `uv run ring3 run --pack agent-v3 --witness-backend vision
  --witness-dir /mnt/c/<folder> --calibration zc-<time> --budget-usd 0.50`, console request:
  `move the gauge to MID and confirm it with the camera`.
- (c) **The D-29 moment** (same bridge or a new run): hold the arm still by hand (away from the sensor), console
  request: `move the gauge to HIGH and confirm it with the camera`; expected FAIL and the agent's halt / report.
Send back: the run ids (`runs/zc-*`, `runs/<time>`), or the printed tables. The builder reads the run
directories (`events.jsonl`, `witness` and `model_reply` records, blobs) and fills the tables below.

## Zone check (A3): `zc-20261104-153656` (user, 2026-11-04, pack v1), `CHAIN OK: 3885 records, 31 blobs`
Earlier attempts the same afternoon: `zc-152454`, `-152701`, `-152928` (no image: no telemetry / no move),
`zc-153327` (6 images, 2 of 6: the arm stayed at ~76 deg for every command) and `zc-153614` (3 images, 1 of 3,
same), after which the user fixed a servo wire. Then:

| image | request | commanded | verdict | reason | zone | arm_visible | usage in / out (reasoning) | arm angle measured from the crop | crop sha256 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 50 LOW | CAN'T VERIFY | UNSURE | UNSURE | 1 | 1406 / 1550 (1530) | 51 deg, LOW | `zc-153656/image1-50-annotated.jpg` |
| 2 | 1 | 90 MID | PASS | ZONE_MATCH | MID | 1 | 1406 / 403 (382) | 76-80 deg, MID (3-5 deg from the 75 band edge) | `image2-90-crop.jpg` |
| 3 | 1 | 130 HIGH | FAIL | WRONG_ZONE | MID | 1 | 1406 / 274 (253) | 110 deg, **on the 110 band** | `image3-130-annotated.jpg` |
| 4 | 2 | 90 MID | PASS | ZONE_MATCH | MID | 1 | 1406 / 359 (340) | 76-80 deg, MID | |
| 5 | 2 | 50 LOW | PASS | ZONE_MATCH | LOW | 1 | 1406 / 416 (397) | 50 deg, LOW | |
| 6 | 2 | 130 HIGH | FAIL | WRONG_ZONE | MID | 1 | 1406 / 1135 (1114) | 111 deg, **on the 110 band** | `image6-130-annotated.jpg` |

Every reply was `status: completed`, exactly the schema object (`strict` json_schema accepted by the server:
one `[UNVERIFIED]` closed). **`usage.input_tokens` = 1,406 per call** (pack 2,676 B + question + a 920 x 570
JPEG): the 2,000-token image ceiling plus text bytes (5,390 reserved) is above the real count; closed.
Reasoning spent 253-1,530 of the 2,048 output cap: the UNSURE reply used 1,530, close to the cap.
Spend: $0.023164 settled for the run (3 runs today $0.047881, ledger).

### Free analysis (no call): the arm's angle in each crop
Measured from the printed pivot as the darkness-weighted direction of the black bar's pixels 6-20 mm out
(`step` script in this session; the annotated crops draw the measured ray, the commanded ray and the nearest
band edge): **50 -> 51 / 50, 90 -> 76 / 75 (visually 76-82), 130 -> 110 / 111**. So an 80 deg command span
moved the arm ~60 deg: about 0.75 deg per commanded degree, with the 90 command 10-14 deg low (one SG90
spline tooth is 14.4 deg). Camera axis 2.1 / 2.4 mm from the pivot (within the 5 mm advice); the hub's screw
hole is not found (a white cap covers it), the hub sits on the pivot by eye. Parallax cannot rotate the arm
about a point this close to the axis, so the crop shows the arm's real direction.
- **130 -> MID (images 3, 6):** not a model misreading of a HIGH arm. In the rectified crop the arm lies along
  the 110 edge (its tip 20 mm out, in the inner semicircle, along the thin 110 ray); a human extending it
  lands inside the black 110 band or a degree or two to either side. The honest answer is UNSURE (the band),
  the model chose the MID side. The lead's hypothesis holds in part: the tip stays in the inner semicircle,
  so the model has to extend the line ~36 mm to the labelled ring, and the soft shadow (lamp from the right,
  shadow up-left of the arm) sits exactly on the HIGH side; v1 never told it that the thin rays continue
  the band edges inward, which is the cue a short arm needs.
- **50 -> UNSURE once (image 1) and LOW once (image 5):** the two pictures are the same (51 vs 50 deg, 15 deg
  clear of the 70 band). v1's UNSURE rule ("within a band's width of one", "anything else makes the zone
  doubtful") is wide, and the long reasoning (1,530 tokens) ended in the escape answer. v2 narrows UNSURE to
  "the direction cannot be judged".
- What v2 cannot fix: with the arm physically on the 110 band at a 130 command, the best v2 outcome for
  images 3 and 6 is CAN'T VERIFY: UNSURE, still not 6/6. The setup needs the travel fixed (re-seat the horn so
  a 90 command points at the 90 tick: 130 would then read ~124, HIGH, but 50 would read ~64, 1 deg outside
  the 70 band; or the firmware's pulse range, the user's domain; or targets the lead chooses).

## witness-vision-v2 (the card's once-only prompt revision; lead's request after zc-153656)
Same strict schema, same request shape; only the instructions change (`PACK_NAME` -> v2, v1 loadable). Changes
and why: (1) "the arm is the one solid black bar attached to the white hub ... short, about 15 mm, its tip ends
well inside the inner semicircle; a soft grey shadow ... is not the arm" (images 3/6: the shadow lies on the
HIGH side; the tip never reaches the ring); (2) the two thin grey rays "continue the two inner zone edges down
to where the arm's tip is": between the rays MID, right of the right ray LOW, left of the left ray HIGH (the
cue a 15 mm arm needs; `make_gauge` draws them at exactly 70 and 110); (3) the task is to "follow the black
arm's direction from the hub outward ... until the line reaches the labelled ring" with "the black boundary
bands (and the thin rays that continue them inward) as the zone edges" (the lead's wording); (4) UNSURE "only
when the direction cannot be judged: the line reaches the ring inside a black band or on the edge of one, the
arm is blurred, hidden or doubled, or more than one bar is attached to the hub. Clear daylight between the
line and the nearest band is a definite zone, not UNSURE" (image 1). A band still gives UNSURE: a band belongs
to no zone and the card makes it CAN'T VERIFY. Re-test reservation: 6,020 input tokens + 2,048 = $0.016229
per call, 6 images at most $0.097374 (`--budget-usd 0.10` still fits).

## Servo map (D-103, lead, after zc-153656)
`config/servo_map.toml`: `gauge = 0.7475 x command + 11.46 deg` (gain 747500 ppm, offset 11458 mdeg), the
least-squares line through the six measured arm angles above (stored as the points; the loader re-fits them
and refuses a file whose numbers differ; worst residual 3.23 deg: the two 90 points sit 3 deg under the line).
Provenance in the file: run `zc-20261104-153656`, this README, 2026-11-04, the measurement method.
`witness/servomap.py`; used by the vision backend only (`VisionWitness(servo_map=...)`): the expected zone and
the boundary band are judged on the mapped angle (`expected_zone`; a band gives `COMMAND_BOUNDARY: commanded
130 deg maps to 108.6 deg, within 5 deg of the 110 edge`, no frame, no call), every witness record logs
`commanded_deg` and `mapped_mdeg`, and the zone-check targets are the commands whose mapped angles are the
zone centres, clamped to the kernel's 30-150 and reached in steps of <= 30 deg:

| zone | centre | command | mapped angle | margin to the nearest band edge |
|---|---|---|---|---|
| LOW | 50 | 52 | 50.3 | 15.3 deg (65) |
| MID | 90 | 105 | 89.9 | 14.9 deg (75 / 105) |
| HIGH | 130 | 150 (clamped) | 123.6 | 8.6 deg (115) |

Order 52, 105, 150 then 150, 105, 52 (round 2 starts with a zero-length move the kernel still decides, then
approaches MID and LOW from above); 6 and 5 moves per request. The zone-check file records the map and a run
with another map refuses it. The SAM backend never reads the map (`test_sam_backend_never_reads_the_map`).
Tests: `tests/witness/test_servomap.py` (6) + the updated zone-check tests; QEMU sim-cell tests run with the
identity map (the simulated arm follows the command) and assert both angles in every record.
**For the live runs:** the agent pack agent-v3 still tells the model to command 50 / 90 / 130; through this map
90 lands at 78.7 (MID, 3.7 deg clear of the band) and **130 lands on the 110 band**, so a "move to HIGH and
confirm" request answers CAN'T VERIFY: COMMAND_BOUNDARY (detail names both angles) instead of FAIL. The
D-29 moment (c) therefore needs a request that commands 150 (e.g. "move the gauge to 150 and confirm it with
the camera") or a pack change, the lead's call.

## Live runs (A4) — to fill
| run | request | moves | witness verdict | agent's next action | outcome |
|---|---|---|---|---|---|
| (not run yet) | | | | | |

## Spend
Builder: $0 (no call). User's zone checks 2026-11-04: $0.047881 settled over 3 runs (ledger), $0.023164 for
`zc-153656`.

## Files (this card's paths)
`bridge/ring3bridge/models/vision.py` (new), `bridge/ring3bridge/witness/vision.py` (new),
`bridge/ring3bridge/zonecheck.py` (new), `bridge/ring3bridge/{calibration,cli,config,eventlog,ledger,run}.py`,
`bridge/ring3bridge/models/muse_spark.py` (`redact_reply` factored out, behaviour unchanged),
`bridge/ring3bridge/witness/{__init__,pipeline}.py`, `config/models.toml` (`[vision]`),
`docs/context-packs/witness-vision-v1.md` (new), `tests/witness/test_vision.py` (new),
`tests/bridge/test_vision_secrets.py` (new), `tests/test_witness_loop.py` (+2), `tests/witness/test_part_b.py`
(1 expectation), `docs/evidence/T-16/`, `docs/STATUS.md`. No dependency added.
