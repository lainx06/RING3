# T-03a evidence: bridge core (link ABI, framing, strict JSON, spend ledger, event log, adapter, context pack)

Builder: Opus 5.5 / high · 2026-11-01 22:38–23:10 ET, second terminal (T-01B ran in the other one; disjoint
paths, scoped test runs). Host-only Python 3.12 + the host C compiler for the ABI check. **No network call to Meta,
no paid call, no `.env` read**: every bridge test runs with sockets blocked (`tests/bridge/conftest.py`), and the
adapter is tested through a fake transport only.

## Acceptance
| A | Check | Result | Evidence |
|---|---|---|---|
| A1 | `abi/link_abi.h` and `abi.py` agree (test), committed as M1 before the rest | PASS. M1 = `e6300b4`, the first T-03a commit. The test collects all 135 `#define LINK_*` names, compiles a probe that prints each value with the host compiler, and fails on any missing, extra or different constant in either direction. The header also compiles freestanding for i686 with `-Wpedantic -Werror` and includes only `<stdint.h>`. The object table, policy name tables and errno values are checked against `kernel/policy/` compiled into a temp dir (read-only). A mutation check (one value changed, then one extra define) failed the test both times. | `abi-check.txt`, `tests/bridge/test_abi.py` |
| A2 | `uv run pytest tests/bridge` and `ruff` pass; the shared link vectors match byte for byte | PASS: 260 tests, about 4 s; ruff check + format clean on `bridge/` and `tests/bridge/` (and repo-wide). All 25 lines of `firmware/test/link_vectors.txt` (CRC, COBS, frames, both directions, every expected error) match; `ring3 frames-selftest` 57/57. | `pytest.txt`, `abi-check.txt` |
| A3 | INV-01, INV-08 (≥ 60 cases), INV-09 (three tests), INV-10 (tamper) exist and pass | PASS. `test_inv_01_no_code_path` (AST; scanner checked against 22 forbidden snippets). `test_inv_08_parser_cases`: **113 cases** (25 accepted, 88 rejected). `test_inv_09_reservation_before_request`, `test_inv_09_stop_on_401` (401, 403, 500, 429, network, timeout), `test_inv_09_no_secrets_anywhere`. `test_inv_10_chain_tamper_every_log_byte` / `_every_blob_byte`. | `pytest.txt`, `strictjson-cases.md`, `verify-tamper.txt` |
| A4 | `ring3 verify` detects a one-byte edit anywhere in a sample run; `ring3 spend` works on a sample ledger | PASS. Every byte of the 3,673-byte sample log (3 replacement values each) and every byte of its 6 blobs (204 B) → `BROKEN …`; restored → `CHAIN OK`. `ring3 spend` prints settled / unsettled / total, labelled "estimate, not billing". | `verify-tamper.txt`, `ledger.txt` |
| A5 | `config/models.toml` price fields sourced (URL + date) or unset and `[UNVERIFIED]`; the ledger refuses to reserve without a price | PASS: **unset, `[UNVERIFIED]`**. The loader also refuses a price that lacks an `https` source URL and a `YYYY-MM-DD` check date. Shipped config → `REFUSED reason=no_price`, nothing written. | `ledger.txt`, `test_shipped_config_has_no_price_and_fails_closed` |
| A6 | Commits: real `Builder:` trailer, only this card's paths | PASS: 10 code commits `e6300b4` … `b73e922` plus this evidence/STATUS commit; each carries `Builder: Opus 5.5/high` as a trailer; the union of touched paths is inside `abi/ bridge/ config/ docs/context-packs/ docs/LINK_ABI.md tests/bridge/ pyproject.toml uv.lock docs/evidence/T-03a/` (+ `docs/STATUS.md`, my row only, in the final commit). `tools/check-paths.sh` OK. | `git log` |

## What was built (one module per step)
| Step | Files | Notes |
|---|---|---|
| 1 ABI | `abi/link_abi.h`, `bridge/ring3bridge/abi.py`, `docs/LINK_ABI.md` | Types `0x10`–`0x15` K→B, `0x90`–`0x93` B→K (disjoint from COM3). Exact body bounds per type; largest body 524 (`OBSERVATION`) → decoded 531 → canonical COBS 534 → 535 on the wire (replaces the 1024 B *(prov.)* ceiling with an exact bound below it). 16-byte ACTION, op numbers 1–9, object IDs 0–6, STOP reasons 1–6, zero-unused rule, TEXT-before-ACTION rule, deterministic nonce rule. |
| 2 Framing | `framing.py` | Canonical COBS only (decode, re-encode, compare); checks in vector order: size → length → CRC → type → body bounds → field rules. Incremental decoder, raw input capped at 534 B between delimiters, resync on `0x00`, never raises. |
| 3 Strict JSON | `strictjson.py` | `json.loads` with hooks that reject duplicates, floats/exponents, NaN/Infinity, `-0`, > 6-digit ints; exact `type() is` checks (no bool-as-int); printable ASCII after unescaping; byte caps 16 / 64 / 200; names from the object table; whole reply must be ASCII + JSON whitespace; ≤ 4096 B. |
| 4 Serializer | `actions.py` | TEXT then ACTION; nonce = LE u32 of SHA-256(`ring3-nonce-v1` ‖ seed[16] ‖ index LE)[0:4], text_id = index % 255 + 1. |
| 5 Ledger | `config.py`, `ledger.py`, `config/*.toml` | Integer µUSD, prices as µUSD per 1M tokens, costs rounded up; reservation = request bytes + max output tokens; unsettled reservations count against both caps; append-only canonical JSONL with fsync; unreadable ledger → refuse. |
| 6 Event log | `eventlog.py` | Canonical lines, `seq`/`prev`/`h` chain, §6 record types + `event`, integers only, content-addressed blobs; `verify` also rejects non-canonical bytes, unknown types, missing/extra/altered blobs, a missing `run_start`; a missing `run_end` is reported as "run incomplete". |
| 7 Adapter | `models/transport.py`, `models/muse_spark.py` | reserve → `spend_reserved` → header-free `model_request` → post → `model_reply` → settle → `spend_settled`; the first 401/403/non-200/transport error stops the client permanently (failed requests settle at the full reservation). Key: env only, private attribute, `Authorization` header only, scrubbed from response bodies, errors raised with no chained context, **redirects refused** (urllib would forward the `Authorization` header to a redirect target). |
| 8 Context pack | `docs/context-packs/agent-v1.md`, `context_pack.py` | Every example reply in the pack validates; limits checked against `policy.h`; no banned words. |
| 9 INV-01 | `tests/bridge/test_inv_01_no_code_path.py` | Bans the primitives outright (eval/exec/compile/`__import__`/subprocess/pickle/marshal/importlib/ctypes/os.system…, dynamic getattr/globals); one justified allowlist entry (`abi.c_constants()` reads its own module globals). |
| 10 CLI | `cli.py`, `selftest.py`, `pyproject.toml` | `ring3 verify|spend|frames-selftest`; `run` → T-03b, `replay` / `fuzz-link` → T-05a (exit 2). |

## `[UNVERIFIED]` (Meta API and pricing)
- **Price per token: unset.** Not found on an official Meta source in this session (no network calls to Meta, per
  the user's instruction). One web search returned only secondary aggregators
  ([eesel.ai](https://www.eesel.ai/blog/muse-spark-1-3-pricing), [LiteLLM](https://docs.litellm.ai/blog/muse_spark_1_3)):
  $1.25 / 1M input, $4.25 / 1M output for `muse-spark-1.3`. Recorded in a comment as a hint, **not used**. Before
  the first paid call the user copies the price from Meta's official pricing page with its URL and date.
- **Endpoint and model ID:** `POST https://api.meta.ai/v1/responses`, `muse-spark-1.3`, taken from ARCHITECTURE §6
  (authenticated catalog, 2026-10-31), not re-checked.
- **Request/response field names:** `model`, `instructions`, `input`, `max_output_tokens`;
  `output[].content[].text` (`type == "output_text"`), `usage.input_tokens` / `usage.output_tokens`. These follow
  the common Responses-API shape; **not checked against Meta's docs**. They are marked `[UNVERIFIED]` in
  `models/muse_spark.py`. If they're wrong, the parser yields empty text → strict JSON rejects → two invalid
  replies → `STOP`; usage unknown → settles at the full reservation. It fails closed either way.
- Rate limits: unknown.

## Surprises and decisions worth a look
- **The other terminal was T-01B**, not T-02F (T-02F had already finished). I kept to the same rules: own paths,
  scoped runs, STATUS once.
- **The kernel's COBS decoder accepts some non-canonical encodings** (a trailing `0x01` after a `0xFF` block decodes to
  the same bytes). The bridge rejects them; `docs/LINK_ABI.md` §7 flags this for T-03b. The CRC still has to
  match, so it is not a corruption path.
- **urllib forwards `Authorization` on redirects**, including cross-host ones. The transport refuses all
  redirects, and a 3xx stops the run.
- **Root `tests/conftest.py` builds every kernel image in an autouse session fixture.** `tests/bridge/conftest.py`
  overrides it by name (no-op) so bridge tests don't touch `build/` while another terminal does.
- `pyproject.toml`: `pythonpath = ["bridge"]`, `known-first-party`, one per-file E501 ignore (the case table), and
  `[build-system] uv_build` for the `ring3` entry point. **New build-system requirement** (`uv_build`, uv's own
  backend, resolved from the local cache); no runtime dependency added. `uv.lock` changed one line (the project
  is now `editable`).
- My strict-JSON commit message (`209c414`) says "115 parser cases"; the real count is 113 (+2 added after).
  I left the commit as is and the numbers here are the real ones.
- The ledger needs no lock: there's one bridge process per run. A crash between reserve and settle leaves the
  reservation counting against the caps until settled by hand (fail closed).

## For T-03b
- Build `kernel/core/agentlink.c` and `agentsh` on `abi/link_abi.h`. Size buffers from `LINK_MAX_ENCODED` /
  `LINK_MAX_DECODED`, not `COBS_MAX_PAYLOAD`. Check the per-type body bounds in the kernel; field rules (zero
  bytes, op ranges, TEXT id) belong to `agentsh`.
- AUDIT is 24 B and EVENT 28 B (= `struct pol_event`); the kernel's text `TAPE`/`AUDIT` lines move to these frames.
  Log the decoded audit record as `audit` with `audit_seq` (the chain owns `seq`).
- `run_start` should hold `kernel_sha256`, `policy_sha256`, `context_pack_sha256` (`context_pack.load()`),
  `model_id`, `seed` (16 bytes hex, `actions.new_seed()`), `abi_version`, `budget_uusd`.
- The observation block format in the context pack (`OBSERVATION (untrusted data) op=… result=… [error=…]`) is a
  proposal. If T-03b formats it differently, change the pack as `agent-v2` (the hash in `run_start` changes with it).
- `witness` (op 9) has a number and a JSON shape but no handler. T-07 decides who answers it.
- First paid call: needs the official price in `config/models.toml` and the user's go-ahead.
