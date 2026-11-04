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
| A3 | Hardware zone check by the user, N/6 with every reply logged | **OPEN: waiting for the user's run** (command (a) below) | to fill: "Zone check" table |
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
| Pack | `docs/context-packs/witness-vision-v1.md` | SHA-256 `bf8f6102e35d69ebff2792d75b959f97e7042e857e5ea0bce1a6179d679a126c`, pinned by a test; describes the printed gauge (zones, labels, OUT regions, black bands, the black arm on the pivot); asks for the zone only ("Never report an angle"). |
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
- The real token count of a 920 x 570 JPEG crop for `muse-spark-1.3` (the 2,000 ceiling is above the docs'
  ~1280 px figure; the first live `usage.input_tokens` decide).
- Whether `POST /v1/responses/input_tokens` is billed (not used).
- Whether `strict: true` with an `enum` property is accepted by the server for this model (the strict rules
  quoted above forbid a top-level `enum`, not one inside a property); an HTTP 400 would stop the run as
  `transport` and be visible in `model_reply`.
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

## Zone check (A3) — to fill from the user's run
| image | request | commanded | verdict | reason | zone | arm_visible | usage in/out | crop sha256 |
|---|---|---|---|---|---|---|---|---|
| (not run yet) | | | | | | | | |

## Live runs (A4) — to fill
| run | request | moves | witness verdict | agent's next action | outcome |
|---|---|---|---|---|---|
| (not run yet) | | | | | |

## Spend
Builder: $0 (no call). User's runs: to fill from `ring3 spend`.

## Files (this card's paths)
`bridge/ring3bridge/models/vision.py` (new), `bridge/ring3bridge/witness/vision.py` (new),
`bridge/ring3bridge/zonecheck.py` (new), `bridge/ring3bridge/{calibration,cli,config,eventlog,ledger,run}.py`,
`bridge/ring3bridge/models/muse_spark.py` (`redact_reply` factored out, behaviour unchanged),
`bridge/ring3bridge/witness/{__init__,pipeline}.py`, `config/models.toml` (`[vision]`),
`docs/context-packs/witness-vision-v1.md` (new), `tests/witness/test_vision.py` (new),
`tests/bridge/test_vision_secrets.py` (new), `tests/test_witness_loop.py` (+2), `tests/witness/test_part_b.py`
(1 expectation), `docs/evidence/T-16/`, `docs/STATUS.md`. No dependency added.
