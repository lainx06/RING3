"""``uv run ring3 <command>``.

Only ``run`` and ``calibrate`` (without ``--dry-run``) make network calls, and only to the endpoints in
``config/models.toml`` through the transport in ``models/``; both need ``--budget-usd`` and both are the
user's to run (D-57: denied to builder sessions in ``.claude/settings.json``). Nothing here reads a key:
``HttpsTransport`` does, from the environment, when ``run`` or ``calibrate`` constructs it.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
import time
from pathlib import Path

from ring3bridge import abi, context_pack, eventlog, replay, selftest, style
from ring3bridge.config import REPO, load_model_config, load_project_cap
from ring3bridge.ledger import (
    DEFAULT_LEDGER,
    BudgetRefused,
    Ledger,
    cost_uusd,
    fmt_usd,
    request_tokens,
    usd_to_uusd,
)

RUNS = REPO / "runs"
DEFAULT_PORT = 4555


def _run_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    return RUNS / arg


def cmd_verify(a: argparse.Namespace) -> int:
    r = eventlog.verify(_run_dir(a.run))
    print(r.message)
    return 0 if r.ok else 1


def cmd_spend(a: argparse.Namespace) -> int:
    led = Ledger(load_project_cap(), Path(a.ledger) if a.ledger else DEFAULT_LEDGER)
    print(led.report())
    return 0


def cmd_frames_selftest(_a: argparse.Namespace) -> int:
    ok, lines = selftest.run()
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_replay(a: argparse.Namespace) -> int:
    r = replay.replay_run(_run_dir(a.run))
    print(r.message)
    kinds = replay.event_kinds(replay.read_records(_run_dir(a.run)))
    print("tape events by kind: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    return 0 if r.ok else 1


def cmd_tape(a: argparse.Namespace) -> int:
    """T-04 step 9: the run's event records as TAPE lines for `build/host/policy_test --replay` (the
    converter `ring3 replay` uses; printed so the host build can be driven by hand)."""
    for line in replay.tape_lines(replay.read_records(_run_dir(a.run))):
        print(line)
    return 0


def cmd_fuzz_link(a: argparse.Namespace) -> int:
    from ring3bridge.fuzz import run_fuzz
    from ring3bridge.link import Link

    link = Link(a.host, a.port)
    link.connect(a.connect_timeout)
    try:
        res = run_fuzz(link, a.seed, a.frames, settle_s=a.settle)
    finally:
        link.close()
    print(res.report())
    return 0 if res.ok() else 1


def _plan_reservation(cfg, pack, request_text: str) -> tuple[int, int]:
    from ring3bridge.models.muse_spark import build_request
    from ring3bridge.run import build_input

    body = build_request(
        cfg.id, pack.text, build_input(request_text, [], None), cfg.max_output_tokens, cfg.reasoning_effort
    )
    tin = request_tokens(body)
    assert cfg.price is not None
    uusd = cost_uusd(tin, cfg.price.input_uusd_per_mtok) + cost_uusd(
        cfg.max_output_tokens, cfg.price.output_uusd_per_mtok
    )
    return tin, uusd


def _sam_setup():
    """(SAM config, gauge geometry, geometry.json SHA-256) for the witness; no key, no network."""
    from ring3bridge.witness.dial import GEOMETRY_JSON, load_geometry
    from ring3bridge.witness.sam import load_sam_config

    return load_sam_config(), load_geometry(), hashlib.sha256(GEOMETRY_JSON.read_bytes()).hexdigest()


def _witness_dir(arg: str | None) -> Path | None:
    if arg is None:
        return None
    d = Path(arg)
    if not d.is_dir():
        raise SystemExit(f"--witness-dir {arg}: not a directory (start the Windows capture helper first)")
    return d


def cmd_run(a: argparse.Namespace) -> int:
    from ring3bridge.run import Runner, start_run

    cfg = load_model_config()
    pack = context_pack.load(a.pack)
    budget = usd_to_uusd(a.budget_usd)
    kernel = Path(a.kernel)
    if not kernel.is_absolute():
        kernel = REPO / kernel
    if cfg.price is None:
        print(
            "run: no price in config/models.toml; the ledger would refuse every reservation", file=sys.stderr
        )
        return 2
    if a.dry_run:
        sample = a.dry_run_request or "list the objects, read cell_spec, and tell me the fan limits"
        tin, uusd = _plan_reservation(cfg, pack, sample)
        print("ring3 run --dry-run (no network, no key read)")
        price = cfg.price
        p_in, p_out = price.input_uusd_per_mtok / 1e6, price.output_uusd_per_mtok / 1e6
        per_req = abi.ACTIONS_PER_REQUEST + 1 + abi.ACTIONS_PER_REQUEST
        lines = [
            f"  model {cfg.id} at {cfg.endpoint}",
            f"  context pack {pack.name}: {len(pack.text)} B, sha256 {pack.sha256[:16]}...",
            f"  price: ${p_in:.2f} / 1M in, ${p_out:.2f} / 1M out ({price.source}, checked {price.checked})",
            f"  first call for request {sample!r}: request body {tin} B -> {tin} input tokens reserved",
            f"    + {cfg.max_output_tokens} max output tokens = {fmt_usd(uusd)} per call",
            "    (estimate, not billing)",
            f"  worst case per request: {abi.ACTIONS_PER_REQUEST} actions + done + one retry each",
            f"    = {per_req} calls; later calls grow with the observations (<= 512 B each)",
            f"  --budget-usd {a.budget_usd} = {fmt_usd(budget)} per run",
            f"  project cap {fmt_usd(load_project_cap())}",
        ]
        if a.witness_dir:
            sam_cfg, _, _ = _sam_setup()
            per = sam_cfg.price_uusd_per_image
            lines.append(
                f"  witness: {sam_cfg.model_id}, one image reserved per witness op "
                f"({fmt_usd(per) if per else 'price unset: refused'}), calibration {a.calibration or 'none'}"
            )
        print("\n".join(lines))
        return 0
    from ring3bridge.link import Link
    from ring3bridge.models.muse_spark import MuseSparkClient
    from ring3bridge.models.transport import HttpsTransport, TransportError

    run_id = a.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    try:
        transport = HttpsTransport()
    except TransportError as e:
        print(f"run: {e} (the model API key comes from the environment, never from a file)", file=sys.stderr)
        return 2
    witness_dir = _witness_dir(a.witness_dir)
    log = eventlog.EventLog(run_dir)
    ledger = Ledger(load_project_cap(), DEFAULT_LEDGER)
    client = MuseSparkClient(cfg, transport, ledger, log, run_id, budget)
    seed = secrets.token_bytes(16)
    start_run(log, run_id, kernel, pack, cfg.id, seed, budget)
    witness = None
    if witness_dir is not None:
        witness = _run_witness(a, log, transport, ledger, run_id, budget, witness_dir)
    link = Link(a.host, a.port)
    print(f"run {run_id}: connecting to COM2 at {a.host}:{a.port} (budget {fmt_usd(budget)}, estimate)")
    link.connect(a.connect_timeout)
    timeline = style.timeline_for(a.style, sys.stdout, _timeline)  # T-08: plain unless a terminal asks
    runner = Runner(link, client, log, pack, seed, timeline=timeline, witness=witness)
    try:
        runner.serve(requests=a.requests, idle_s=a.idle)
    except KeyboardInterrupt:
        style.close(timeline)
        print("interrupted by the operator")
        if runner.request is not None:
            try:
                link.send(abi.LINK_T_STOP, bytes([abi.LINK_STOP_OPERATOR]))
            except Exception:  # noqa: BLE001
                pass
    finally:
        style.close(timeline)
        runner.finish()
        link.close()
    print(f"run {run_id}: {runner.requests_done} request(s); log {run_dir.relative_to(REPO)}/events.jsonl")
    print(ledger.report())
    v = eventlog.verify(run_dir)
    print(v.message)
    return 0 if v.ok else 1


def _run_witness(a, log, transport, ledger, run_id, budget, witness_dir):  # noqa: ANN001, ANN202
    """The ``witness`` op for a run: the calibration from ``--calibration`` (re-fitted, logged), the shared
    capture folder, SAM on the run's transport and ledger. No calibration -> every check CAN'T VERIFY."""
    from ring3bridge.calibration import CalibrationError, load_calibration
    from ring3bridge.witness import calibrate
    from ring3bridge.witness.sam import LedgerImageSpend, SamClient
    from ring3bridge.witness.service import FolderCamera, Witness

    sam_cfg, geom, geom_sha = _sam_setup()
    cal, source, file_sha = calibrate.NONE, "", ""
    if a.calibration:
        try:
            cal, doc, file_sha = load_calibration(_run_dir(a.calibration), geom_sha)
            source = doc["run_id"]
        except (CalibrationError, OSError, ValueError, KeyError) as e:
            cal = calibrate.Calibration(False, f"calibration {a.calibration} not usable: {e}")
    log.append("calibration", source_run=source, file_sha256=file_sha, ok=int(cal.ok), reason=cal.reason,
               slope_ppm=cal.slope_ppm, offset_mdeg=cal.offset_mdeg, residual_mdeg=cal.residual_mdeg,
               n=cal.n, geometry_sha256=geom_sha)  # fmt: skip
    print(f"witness: {cal.message()}")
    sam = SamClient(sam_cfg, transport, LedgerImageSpend(ledger, run_id, sam_cfg, budget))
    return Witness(FolderCamera(witness_dir), sam, cal, geom).check


def cmd_witness_frame(a: argparse.Namespace) -> int:
    """Setup check (free): one fresh frame through the capture helper, markers, fit, camera axis vs pivot.
    No SAM call, no key, no network; the frame and the dial crop go to runs/witness-frame-<time>/."""
    from ring3bridge.witness import CantVerify
    from ring3bridge.witness.dial import encode_jpeg, load_geometry
    from ring3bridge.witness.service import FolderCamera, frame_report

    folder = _witness_dir(a.witness_dir)
    assert folder is not None
    try:
        img, jpeg = (FolderCamera(folder, a.timeout) if a.timeout else FolderCamera(folder))()
    except CantVerify as e:
        print(f"witness-frame: {e} (is the Windows helper running on this folder?)")
        return 1
    rep = frame_report(img, load_geometry())
    out = RUNS / ("witness-frame-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)
    (out / "frame.jpg").write_bytes(jpeg)
    if rep.crop_img is not None:
        (out / "crop.jpg").write_bytes(encode_jpeg(rep.crop_img))
    if rep.pivot_img is not None:
        (out / "pivot.jpg").write_bytes(encode_jpeg(rep.pivot_img))
    print("\n".join(rep.lines))
    shown = out.relative_to(REPO) if out.is_relative_to(REPO) else out.name
    print(f"saved {shown}/frame.jpg" + (" and crop.jpg" if rep.crop_img is not None else ""))
    return 0 if rep.ok else 1


def _cmd_probe(a: argparse.Namespace, sam_cfg, geom, budget: int, per: int) -> int:  # noqa: ANN001
    """``ring3 calibrate --probe``: re-send saved frames to SAM with candidate phrases and crops (paid, at
    most 5 images, one reserved before each call). No kernel, no link, no servo move."""
    from ring3bridge.calibration import PROBE_MAX, probe, probe_plan

    frames = [Path(f) for f in a.probe]
    from ring3bridge.witness.sam import DEFAULT_PROMPT

    phrases = [p.strip() for p in (a.phrases or DEFAULT_PROMPT).split(";") if p.strip()]
    crops = [c.strip() for c in a.crops.split(",") if c.strip()]
    try:
        items = probe_plan(frames, phrases, crops)
        missing = [str(f) for f in frames if not f.is_file()]
        if missing:
            raise ValueError(f"no such frame file: {missing}")
    except ValueError as e:
        print(f"calibrate --probe: {e}", file=sys.stderr)
        return 2
    print(f"ring3 calibrate --probe{' --dry-run (no network, no key read)' if a.dry_run else ''}: "
          f"{len(items)} image(s) (max {PROBE_MAX}) x {fmt_usd(per)} = {fmt_usd(len(items) * per)}, one "
          f"reserved before each SAM call; --budget-usd {a.budget_usd} = {fmt_usd(budget)}")  # fmt: skip
    for f, c, p in items:
        print(f"  {f.name[:16]} crop={c} phrase={p!r}")
    if a.dry_run:
        return 0
    from ring3bridge.models.transport import HttpsTransport, TransportError
    from ring3bridge.witness.sam import LedgerImageSpend, SamClient, SamStop

    run_id = a.run_id or "probe-" + time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    try:
        transport = HttpsTransport()
    except TransportError as e:
        print(f"calibrate: {e} (the API key comes from the environment, never from a file)", file=sys.stderr)
        return 2
    log = eventlog.EventLog(run_dir)
    log.append("run_start", run_id=run_id, model_id=sam_cfg.model_id, budget_uusd=budget, t_ns=time.time_ns())
    ledger = Ledger(load_project_cap(), DEFAULT_LEDGER)
    sam = SamClient(sam_cfg, transport, LedgerImageSpend(ledger, run_id, sam_cfg, budget))
    rc = 0
    try:
        probe(items, geom, sam, log)
    except (SamStop, BudgetRefused) as e:
        print(f"probe stopped: {e}")
        rc = 1
    log.append("run_end", records=log.seq)
    print(f"probe {run_id}: {run_dir.relative_to(REPO)}/events.jsonl (raw SAM replies as sam_reply_blob)")
    print(ledger.report())
    v = eventlog.verify(run_dir)
    print(v.message)
    return rc if v.ok else 1


def cmd_calibrate(a: argparse.Namespace) -> int:
    """T-07 Part B step 2: scripted calibration moves through the kernel, one SAM image per point (paid).
    With ``--checks`` (step 4): scripted moves to zone centres and a real witness check after each, with the
    calibration from ``--calibration``; one console request per round (``--requests``)."""
    from ring3bridge.calibration import (
        CAL_POINTS,
        CHECK_TARGETS,
        CalibrationError,
        CalibrationScript,
        calibration_doc,
        load_calibration,
        plan,
        route,
        write_calibration,
    )
    from ring3bridge.context_pack import ContextPack
    from ring3bridge.run import Runner, start_run
    from ring3bridge.witness import calibrate
    from ring3bridge.witness.calibrate import fit

    sam_cfg, geom, geom_sha = _sam_setup()
    budget = usd_to_uusd(a.budget_usd)
    per = sam_cfg.price_uusd_per_image
    if per is None:
        print(
            "calibrate: no SAM price in config/models.toml; the ledger would refuse every image",
            file=sys.stderr,
        )
        return 2
    if a.probe:
        return _cmd_probe(a, sam_cfg, geom, budget, per)
    planner, what = (route, "targets") if a.checks else (plan, "points")
    points = CHECK_TARGETS if a.checks else CAL_POINTS
    arg = a.targets if a.checks else a.points
    if arg:
        try:
            points = tuple(int(x) for x in arg.split(","))
            for start_deg in (30, 90, 150):
                if len(planner(start_deg, points)) >= abi.ACTIONS_PER_REQUEST:
                    raise ValueError(f"more than {abi.ACTIONS_PER_REQUEST - 1} moves from {start_deg}")
        except ValueError as e:
            print(f"calibrate: bad --{what} {arg!r}: {e}", file=sys.stderr)
            return 2
    requests = a.requests if a.checks else 1
    if a.dry_run:
        moves = planner(a.dry_run_start, points)
        n_img = sum(1 for _, m in moves if m) * requests
        print("ring3 calibrate --dry-run (no network, no key read)" + (" --checks" if a.checks else ""))
        print(f"  {sam_cfg.model_id} at {sam_cfg.endpoint}, {fmt_usd(per)} per image "
              f"({sam_cfg.price_source}, checked {sam_cfg.price_checked}), estimate")  # fmt: skip
        print(f"  {what} {list(points)}; from a servo angle of {a.dry_run_start} (STATE telemetry): "
              f"{len(moves)} servo_set (at most {abi.ACTIONS_PER_REQUEST - 1}: agentsh ends a request after "
              f"its {abi.ACTIONS_PER_REQUEST}th ACTION)")  # fmt: skip
        mark = "checked" if a.checks else "measured"
        print("  moves: " + ", ".join(f"{d}{'*' if m else ''}" for d, m in moves) + f"   (* = {mark})")
        print(f"  reservation: {n_img} images x {fmt_usd(per)} = {fmt_usd(n_img * per)} over {requests} "
              f"request(s), one reserved before each SAM call; --budget-usd {a.budget_usd} = "
              f"{fmt_usd(budget)}")  # fmt: skip
        print(f"  project cap {fmt_usd(load_project_cap())}")
        return 0
    from ring3bridge.link import Link
    from ring3bridge.models.transport import HttpsTransport, TransportError
    from ring3bridge.witness.sam import LedgerImageSpend, SamClient
    from ring3bridge.witness.service import FolderCamera, Witness

    if not a.witness_dir:
        print("calibrate: --witness-dir is required (the Windows capture helper's folder)", file=sys.stderr)
        return 2
    witness_dir = _witness_dir(a.witness_dir)
    assert witness_dir is not None
    cal_in, cal_sha = calibrate.NONE, ""
    if a.checks:
        if not a.calibration:
            print("calibrate --checks: --calibration <run> is required", file=sys.stderr)
            return 2
        try:
            cal_in, _, cal_sha = load_calibration(_run_dir(a.calibration), geom_sha)
        except (CalibrationError, OSError, ValueError, KeyError) as e:
            print(f"calibrate --checks: calibration {a.calibration} not usable: {e}", file=sys.stderr)
            return 2
        print(f"witness: {cal_in.message()}")
    run_id = a.run_id or ("chk-" if a.checks else "cal-") + time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    try:
        transport = HttpsTransport()
    except TransportError as e:
        print(f"calibrate: {e} (the API key comes from the environment, never from a file)", file=sys.stderr)
        return 2
    log = eventlog.EventLog(run_dir)
    ledger = Ledger(load_project_cap(), DEFAULT_LEDGER)
    sam = SamClient(sam_cfg, transport, LedgerImageSpend(ledger, run_id, sam_cfg, budget))
    script = CalibrationScript(Witness(FolderCamera(witness_dir), sam, cal_in, geom), points, checks=a.checks)
    name = "scripted-checks" if a.checks else "scripted-calibration"
    pack = ContextPack(name, "", hashlib.sha256(b"").hexdigest())
    kernel = Path(a.kernel) if Path(a.kernel).is_absolute() else REPO / a.kernel
    seed = secrets.token_bytes(16)
    start_run(log, run_id, kernel, pack, name, seed, budget)
    if a.checks:
        log.append("calibration", source_run=a.calibration, file_sha256=cal_sha, ok=int(cal_in.ok),
                   reason=cal_in.reason, slope_ppm=cal_in.slope_ppm, offset_mdeg=cal_in.offset_mdeg,
                   residual_mdeg=cal_in.residual_mdeg, n=cal_in.n, geometry_sha256=geom_sha)  # fmt: skip
    link = Link(a.host, a.port)
    print(f"calibrate {run_id}: connecting to COM2 at {a.host}:{a.port} (budget {fmt_usd(budget)}, estimate)")
    link.connect(a.connect_timeout)
    hint = "e.g. checks normal / checks held / checks unplugged" if a.checks else "e.g. calibrate the witness"
    print(f"type a request at the RING3 console, {hint} (keep hands away from the sensor)")
    runner = Runner(link, script, log, pack, seed, timeline=_timeline, witness=script.measure,
                    max_actions=script.max_actions())  # fmt: skip
    script.runner = runner
    cal = cal_in
    try:
        runner.serve(requests=requests, idle_s=a.idle)
    except KeyboardInterrupt:
        print("interrupted by the operator")
        if runner.request is not None:
            try:
                link.send(abi.LINK_T_STOP, bytes([abi.LINK_STOP_OPERATOR]))
            except Exception:  # noqa: BLE001
                pass
    finally:
        if not a.checks:
            cal = fit(script.pairs)
            doc = calibration_doc(run_id, script.pairs, cal, geom_sha)
            sha = write_calibration(run_dir, doc)
            log.append("calibration", source_run=run_id, file_sha256=sha, ok=int(cal.ok), reason=cal.reason,
                       slope_ppm=cal.slope_ppm, offset_mdeg=cal.offset_mdeg, residual_mdeg=cal.residual_mdeg,
                       n=cal.n, geometry_sha256=geom_sha, start_deg=script.start_deg, note=script.note,
                       points=[[c, m] for c, m in script.pairs])  # fmt: skip
        runner.finish()
        link.close()
    if script.note:
        print(f"  script ended early: {script.note}")
    if a.checks:
        print("  request | text | commanded | verdict | reason | measured zone | measured deg | crop sha256")
        for idx, text, ch in script.results:
            r = ch.result
            deg = "" if r.calibrated_mdeg is None else f"{r.calibrated_mdeg / 1000:.1f}"
            print(f"  {idx} | {text} | {r.commanded_deg} {r.commanded_zone} | {r.verdict} | {r.reason} | "
                  f"{r.measured_zone or '-'} | {deg} | {r.image_sha256[:16]}")  # fmt: skip
        counts: dict[str, int] = {}
        for _, _, ch in script.results:
            counts[ch.result.verdict] = counts.get(ch.result.verdict, 0) + 1
        print("  totals: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    else:
        for c, m in script.pairs:
            print(f"  commanded {c:3d} deg -> dial {'CAN T VERIFY' if m is None else f'{m / 1000:.2f} deg'}")
        print(cal.message())
        print(
            f"calibration {run_id}: {run_dir.relative_to(REPO)}/calibration.json; use: --calibration {run_id}"
        )
    print(ledger.report())
    v = eventlog.verify(run_dir)
    print(v.message)
    return 0 if v.ok and cal.ok else 1


def _timeline(line: str) -> None:
    print(time.strftime("%H:%M:%S"), line, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="ring3", description="RING3 host bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check a run's hash chain and every blob")
    v.add_argument("run", help="run directory, or a run id under runs/")
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("spend", help="ledger totals (estimate, not billing)")
    s.add_argument("--ledger", help="ledger file (default runs/ledger.jsonl)")
    s.set_defaults(fn=cmd_spend)
    f = sub.add_parser("frames-selftest", help="CRC, shared link vectors, COM2 round trips")
    f.set_defaults(fn=cmd_frames_selftest)
    r = sub.add_parser("replay", help="host replay: feed a run's event tape through the host policy build")
    r.add_argument("run", help="run directory, or a run id under runs/")
    r.set_defaults(fn=cmd_replay)
    t = sub.add_parser("tape", help="print a run's event tape as TAPE lines (input of policy_test --replay)")
    t.add_argument("run")
    t.set_defaults(fn=cmd_tape)
    z = sub.add_parser("fuzz-link", help="send seeded malformed frames to a running kernel's COM2 (INV-02)")
    z.add_argument("--host", default="127.0.0.1")
    z.add_argument("--port", type=int, default=DEFAULT_PORT)
    z.add_argument("--seed", type=int, default=1)
    z.add_argument("--frames", type=int, default=10000)
    z.add_argument("--settle", type=float, default=10.0, help="seconds to wait for the final counts")
    z.add_argument("--connect-timeout", type=float, default=10.0)
    z.set_defaults(fn=cmd_fuzz_link)
    u = sub.add_parser("run", help="serve requests from a running kernel with Muse Spark (paid)")
    u.add_argument("--budget-usd", required=True, help="run cap in USD, e.g. 0.50 (required)")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=DEFAULT_PORT)
    u.add_argument("--kernel", default="build/kernel.elf", help="image whose SHA-256 goes in run_start")
    u.add_argument("--pack", default=context_pack.CURRENT)
    u.add_argument("--run-id")
    u.add_argument("--requests", type=int, help="stop after this many requests")
    u.add_argument("--idle", type=float, help="stop after this many seconds without a kernel frame")
    u.add_argument("--connect-timeout", type=float, default=30.0)
    u.add_argument("--dry-run", action="store_true", help="print the planned reservation; no network, no key")
    u.add_argument("--dry-run-request", help="request text to size the dry-run reservation on")
    u.add_argument("--witness-dir", help="capture folder shared with the Windows helper, e.g. /mnt/c/...")
    u.add_argument("--calibration", help="run id (or directory) of a ring3 calibrate run")
    u.add_argument(
        "--style", choices=style.STYLES, default="plain", help="plain, or console: 256 colours on a TTY"
    )
    u.set_defaults(fn=cmd_run)
    wf = sub.add_parser("witness-frame", help="setup check: one frame, markers, camera axis (free, no SAM)")
    wf.add_argument("--witness-dir", required=True, help="capture folder shared with the Windows helper")
    wf.add_argument(
        "--timeout", type=float, default=None, help="seconds (default: the helper's worst case + margin)"
    )
    wf.set_defaults(fn=cmd_witness_frame)
    c = sub.add_parser(
        "calibrate", help="witness calibration: scripted servo moves through the kernel (paid SAM)"
    )
    c.add_argument("--budget-usd", required=True, help="run cap in USD for the SAM images (required)")
    c.add_argument(
        "--witness-dir", help="capture folder shared with the Windows helper (required unless --dry-run)"
    )
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=DEFAULT_PORT)
    c.add_argument("--kernel", default="build/kernel.elf", help="image whose SHA-256 goes in run_start")
    c.add_argument("--run-id")
    c.add_argument("--idle", type=float, default=300.0, help="leave after this many seconds without a frame")
    c.add_argument("--connect-timeout", type=float, default=30.0)
    c.add_argument(
        "--dry-run", action="store_true", help="print the planned moves and reservation; no network"
    )
    c.add_argument(
        "--dry-run-start", type=int, default=90, help="kernel servo angle to plan the dry run from"
    )
    c.add_argument("--points", help="calibration angles, comma-separated (default 60,70,90,110,120, D-72)")
    c.add_argument(
        "--probe", nargs="+", help="saved camera frames (JPEG) to re-send to SAM: no kernel, <= 5 images"
    )
    c.add_argument("--phrases", help="--probe: noun phrases separated by ';' (default: the witness phrase)")
    c.add_argument(
        "--crops", default="full", help="--probe: full (the pipeline's crop) and/or tight, comma-separated"
    )
    c.add_argument("--checks", action="store_true", help="scripted zone checks with --calibration (step 4)")
    c.add_argument("--calibration", help="--checks: run id (or directory) of a ring3 calibrate run")
    c.add_argument("--targets", help="--checks: zone-centre angles per request (default 50,90,130)")
    c.add_argument("--requests", type=int, default=4, help="--checks: console requests to serve (default 4)")
    c.set_defaults(fn=cmd_calibrate)
    a = ap.parse_args(args)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
