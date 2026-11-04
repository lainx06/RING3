#!/usr/bin/env python3
"""Permissive sim-cell replay (T-17 Do 2, D-98, D-99): the recorded model replies of a bundle, played to the
PERMISSIVE kernel on the SIMULATED work cell, so the screen shows what that build lets through.

    make run-cell-sim-permissive BUNDLE=replay/t04-run4-taint-prompt        # QEMU window (WSLg GTK)
    uv run python tools/counterfactual_sim.py replay/t04-run4-taint-prompt --display none --shots DIR

What runs: `build/kernel-permissive.elf` (the production image built with POLICY=permissive, ARCHITECTURE §7)
with the `screen` boot option; COM3 is `tests/sim_cell.py` on a loopback socket, never a device (the real
board is refused); COM2 is the real bridge run loop, whose model transport hands back the bundle's recorded
reply bodies, request by request, in order. No key, no network, no model call, no spend (price 0).

Every reply recorded after the bundle's first divergence (`ring3 counterfactual`) was written after RING3's
answer, so the timeline marks it "hypothetical (the model saw RING3's answer)": the model's later text may
describe RING3's refusal even though the permissive kernel allowed the call.

Labels: the banner is the QEMU window title, the first timeline line, a line at every request and at the end,
a prefix on every timeline line, and a strip at the top and bottom of every screendump PNG (the kernel's own
pixels are kept unchanged between the strips); the `.txt` dump has it as its first and last line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "bridge"), str(REPO / "tools")]

from ring3bridge import abi, context_pack, replay  # noqa: E402
from ring3bridge import counterfactual as cf  # noqa: E402
from ring3bridge.config import ModelConfig, Price  # noqa: E402
from ring3bridge.eventlog import EventLog  # noqa: E402
from ring3bridge.ledger import Ledger  # noqa: E402
from ring3bridge.link import Link  # noqa: E402
from ring3bridge.models.muse_spark import MuseSparkClient  # noqa: E402
from ring3bridge.models.transport import TransportError, TransportResponse  # noqa: E402
from ring3bridge.run import Runner, policy_sha256, sha256_file  # noqa: E402

BANNER = cf.BANNER
TAG = "[PERMISSIVE BUILD]"
KERNEL = REPO / "build" / "kernel-permissive.elf"
ZERO_PRICE = Price(0, 0, "none: recorded replies are replayed, no model call", "2026-11-04")
#: The sim cell starts idle (fan 0, servo 90, 300 mm, E-stop released). A bundle whose RING3 decisions came
#: from the real cell's E-stop, presence, echo or freshness, or from an operator's console command, cannot be
#: shown faithfully on it, so it is refused instead of producing a screen with different physical conditions.
STATE_CODES = frozenset({"AUTO_HALT", "DENY_ESTOP", "DENY_INTERLOCK", "DENY_STALE"})
MODEL_OPS = frozenset(
    {"obj_list", "obj_read", "obj_write", "cell_status", "cell_fan", "cell_servo", "cell_lcd", "cell_halt"}
)


class Refused(Exception):
    pass


def refuse_device(cell: str | None) -> None:
    """The permissive build runs on the simulated cell only: any device path, or CELL_PORT, is refused."""
    if cell not in (None, "", "sim"):
        raise Refused(f"refused: --cell {cell}: the permissive build runs on the simulated cell only")
    env = os.environ.get("CELL_PORT", "")
    if env:
        raise Refused(f"refused: CELL_PORT={env}: the permissive build runs on the simulated cell only")


def guard_command(cmd: list[str]) -> list[str]:
    """No serial port of the permissive QEMU may be a device: COM3 must be the loopback sim cell."""
    serials = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-serial"]
    if any(x.startswith("/dev/") for x in cmd) or not all(
        s in ("stdio", "null") or (s.startswith("tcp:127.0.0.1:") and "/dev/" not in s) for s in serials
    ):
        raise Refused(f"refused: a serial port that is not the simulated cell: {serials}")
    return cmd


def labelled(cmd: list[str], display: str) -> list[str]:
    """The harness's QEMU command with a display and the banner as the window title, then guarded."""
    cmd = list(cmd)
    cmd[cmd.index("-display") + 1] = display
    return guard_command([*cmd, "-name", "guest=" + BANNER.replace(",", ",,")])  # QEMU: ",," = ","


# ---- the bundle -----------------------------------------------------------------------------------------
class Bundle:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.records = replay.read_records(self.path)
        self.start = next(r for r in self.records if r.get("type") == "run_start")
        res = cf.counterfactual(self.path)
        if not res.ok:
            raise Refused(res.message)
        self.cf = res
        bad = sorted({a["decision"] for a in replay.recorded_audits(self.records)} & STATE_CODES)
        if bad or any(r.get("type") == "event" and r.get("kind") == 4 for r in self.records):  # 4 = console
            why = ", ".join(bad) if bad else "an operator console command"
            raise Refused(
                f"refused: {self.path.name} depends on the real cell's state or the operator ({why}); the "
                "simulated cell cannot reproduce it, use `ring3 counterfactual` for this bundle"
            )
        # the divergence point in the log's own order: replies recorded after it are hypothetical
        events = [r for r in self.records if r.get("type") == "event" and "a" in r]
        div = res.first_divergence
        self.div_seq = events[div.ev]["seq"] if div is not None else None
        self.requests: list[str] = []
        self.replies: list[list[tuple[bytes, bool]]] = []
        for r in self.records:
            if r["type"] == "user_request":
                self.requests.append(self.blob(r["text_blob"]).decode("utf-8", "replace"))
                self.replies.append([])
            elif r["type"] == "model_reply" and self.replies:
                hyp = self.div_seq is not None and r["seq"] > self.div_seq
                self.replies[-1].append((self.blob(r["blob"]), hyp))

    def blob(self, h: str) -> bytes:
        return (self.path / "blobs" / h).read_bytes()


class RecordedModel:
    """Transport: the bundle's recorded reply bodies for the current request, in order; never a network."""

    def __init__(self, bundle: Bundle, say, pace_s: float = 0.0):
        self.b = bundle
        self.say = say
        self.pace_s = pace_s  # seconds before each reply: time to see (and screendump) every step
        self.req = -1
        self.queue: list[tuple[bytes, bool]] = []
        self.unused: list[int] = []
        self.hypothetical = 0

    def begin(self, req: int) -> None:
        if self.req >= 0:
            self.unused.append(len(self.queue))
        self.req = req
        self.queue = list(self.b.replies[req])
        self.n = len(self.queue)

    def end(self) -> None:
        self.unused.append(len(self.queue))

    def post(self, url: str, body: bytes, timeout_s: int) -> TransportResponse:  # noqa: ARG002
        if not self.queue:
            self.say(f"request {self.req + 1}: no recorded reply left -> the bridge stops the request")
            raise TransportError("network")
        reply, hyp = self.queue.pop(0)
        k = self.n - len(self.queue)
        self.hypothetical += hyp
        self.say(f"request {self.req + 1}: recorded model reply {k} of {self.n}: "
                 f"{cf.HYPOTHETICAL if hyp else 'exact, recorded before the first divergence'}")  # fmt: skip
        time.sleep(self.pace_s)
        return TransportResponse(200, reply)


# ---- screen ---------------------------------------------------------------------------------------------
def stamp_png(png: Path) -> None:
    """A banner strip above and below the kernel's screendump (the screendump's pixels are kept)."""
    import cv2
    import numpy as np

    img = cv2.imread(str(png))
    h, w = img.shape[:2]
    strip = np.zeros((34, w, 3), np.uint8)
    strip[:] = (0, 0, 170)  # BGR dark red
    scale = 0.40
    (tw, th), _ = cv2.getTextSize(BANNER, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    while tw > w - 12 and scale > 0.25:
        scale -= 0.02
        (tw, th), _ = cv2.getTextSize(BANNER, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.putText(strip, BANNER, ((w - tw) // 2, (34 + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), 1, cv2.LINE_AA)  # fmt: skip
    cv2.imwrite(str(png), np.vstack([strip, img, strip]))


def shoot(mon_path: str, out: Path, name: str, tmp: Path) -> Path:
    import screendump

    png = out / f"{name}.png"
    png.unlink(missing_ok=True)
    screendump.monitor(mon_path, f'screendump "{png}" -f png')
    screendump.wait_file(png)
    vga = tmp / "vga.bin"
    vga.unlink(missing_ok=True)
    screendump.monitor(mon_path, f'pmemsave 0xb8000 4000 "{vga}"')
    screendump.wait_file(vga, 4000)
    time.sleep(0.1)
    raw = vga.read_bytes()
    rows = [bytes(raw[(r * 80 + c) * 2] for c in range(80)).decode("cp437") for r in range(25)]
    (out / f"{name}.txt").write_text("\n".join([BANNER, *rows, BANNER]) + "\n", encoding="utf-8")
    stamp_png(png)
    return png


def op_text(a: dict) -> str:
    """An audit record's operation as the counterfactual table writes it."""
    sub, t = a["subject"], a["target"]
    if sub in ("obj_read", "obj_write"):
        return f"{sub}({abi.OBJECT_NAMES[t] if 0 <= t < len(abi.OBJECT_NAMES) else t})"
    return f"{sub}({t})" if sub in ("cell_fan", "cell_servo") else sub


def log_records(run_dir: Path) -> list[dict]:
    p = run_dir / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for x in p.read_text("ascii").splitlines():
        try:
            out.append(json.loads(x))
        except ValueError:  # a line being written
            break
    return out


def audits(run_dir: Path) -> list[dict]:
    return [r for r in log_records(run_dir) if r["type"] == "audit"]


def wait_log(run_dir: Path, pred, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for r in audits(run_dir):
            if pred(r):
                return r
        time.sleep(0.1)
    raise AssertionError(f"no matching audit record within {timeout} s")


# ---- the run --------------------------------------------------------------------------------------------
def run(bundle_path: Path, display: str, shots: Path, run_dir: Path, say=print, pace_s: float = 2.0) -> dict:
    import tests.qemu as q
    from tests.sim_cell import SimCell

    b = Bundle(bundle_path)
    if not KERNEL.exists():
        raise Refused(f"{KERNEL.relative_to(REPO)} missing: make POLICY=permissive")

    def line(msg: str) -> None:
        say(f"{time.strftime('%H:%M:%S')} {TAG} {msg}")

    line(BANNER)
    line(f"bundle {b.path.name}: {len(b.requests)} request(s), recorded model replies played back; "
         "simulated cell; no key, no network, not a live run")  # fmt: skip
    if b.div_seq is None:
        line("ring3 counterfactual: no divergence, every recorded reply is exact")
    else:
        line(f"ring3 counterfactual: {b.cf.message}")
    run_dir.mkdir(parents=True, exist_ok=True)
    shots.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    mon = run_dir / "mon.sock"

    orig = q.qemu_command

    def qemu_command(*a, **k):  # the harness's command, with a display and the banner as window title
        return labelled(orig(*a, **k), display)

    q.qemu_command = qemu_command
    try:
        s = q.Session(kernel=KERNEL, append="screen", timeout=120.0, com2=True, com3=True,
                      monitor=f"unix:{mon},server=on,wait=off")  # fmt: skip
    finally:
        q.qemu_command = orig
    model = RecordedModel(b, line, pace_s)
    log = EventLog(run_dir)
    pack = context_pack.load(b.start["context_pack"])
    cfg = ModelConfig(b.start["model_id"], "recorded://" + b.path.name, 4096, 60, ZERO_PRICE)
    seed = bytes(16)
    log.append("run_start", run_id=run_dir.name, kernel_sha256=sha256_file(KERNEL),
               kernel_path=str(KERNEL.relative_to(REPO)), policy_sha256=policy_sha256("permissive"),
               context_pack=pack.name, context_pack_sha256=pack.sha256, model_id=cfg.id, seed=seed.hex(),
               abi_version=abi.LINK_ABI_VERSION, budget_uusd=1, t_ns=time.time_ns(), build=BANNER,
               replayed_bundle=b.path.name)  # fmt: skip
    ledger = Ledger(1, run_dir / "ledger.jsonl")
    client = MuseSparkClient(cfg, model, ledger, log, run_dir.name, 1)
    link = Link("127.0.0.1", s.com2_port)
    runner = Runner(link, client, log, pack, seed, timeline=line)
    shots_taken: list[str] = []
    outcomes: list[str] = []
    err: list[BaseException] = []
    sim = None
    try:
        link.connect(10.0)  # before the board boots, so this run's own tape is complete

        def serve() -> None:
            try:
                runner.serve(requests=len(b.requests), idle_s=180.0)
            except BaseException as e:  # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        s.expect(r"^cell: COM3 present.*$", 30)
        sim = SimCell(s.com3_port).start()
        wait_log(run_dir, lambda r: r.get("code_name") == "BOARD_BOOT", 30)
        s.prompt(30)
        time.sleep(1.0)
        shots_taken.append(shoot(str(mon), shots, "00-idle", tmp).name)
        for k, text in enumerate(b.requests):
            line(f"{BANNER} | request {k + 1} of {len(b.requests)}")
            model.begin(k)
            seen = len(audits(run_dir))
            s.send(text)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                new = audits(run_dir)[seen:]
                seen += len(new)
                for a in new:
                    if a.get("subject") not in MODEL_OPS:
                        continue
                    time.sleep(0.5)
                    name = f"{k + 1:02d}-{a['audit_seq']}-{a['subject']}-{a['code_name']}".lower()
                    shots_taken.append(shoot(str(mon), shots, name, tmp).name)
                    line(f"screen after decision #{a['audit_seq']} {op_text(a)}: {a['code_name']} "
                         f"{a['reason_name']}")  # fmt: skip
                ends = [r for r in log_records(run_dir) if r["type"] == "request_end"]
                if len(ends) > k and not new:
                    outcomes.append(ends[k]["outcome"])
                    break
                time.sleep(0.1)
            else:
                outcomes.append("?")
            s.prompt(30)
            time.sleep(0.6)
            shots_taken.append(shoot(str(mon), shots, f"{k + 1:02d}-end", tmp).name)
        model.end()
        t.join(30)
    finally:
        runner.stop()
        try:
            runner.finish()
        finally:
            link.close()
            if sim is not None:
                sim.stop()
            r = s.close()
    (run_dir / "com1.txt").write_text(r.output, encoding="utf-8")
    fans = sim.fan_commands() if sim is not None else []
    line(f"outcomes {outcomes}; fan commands the sim cell received {fans}; recorded replies unused per "
         f"request {model.unused}; {model.hypothetical} hypothetical reply(ies) played")  # fmt: skip
    line(BANNER)
    if err:
        raise err[0]
    return {"outcomes": outcomes, "shots": shots_taken, "fans": fans, "unused": model.unused,
            "hypothetical": model.hypothetical, "com1": r.output}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", help="replay bundle directory, e.g. replay/t04-run4-taint-prompt")
    ap.add_argument("--cell", default="sim", help="only 'sim' is accepted: the real board is refused")
    ap.add_argument("--display", default="gtk", choices=("gtk", "none"))
    ap.add_argument("--shots", help="folder for the PNG + text screendumps (default: the run folder)")
    ap.add_argument("--pace", type=float, default=2.0, help="seconds before each recorded reply (default 2)")
    a = ap.parse_args(argv)
    try:
        refuse_device(a.cell)
        run_dir = REPO / "runs" / f"cf-sim-{Path(a.bundle).name}-{time.strftime('%Y%m%d-%H%M%S')}"
        shots = Path(a.shots) if a.shots else run_dir / "shots"
        res = run(Path(a.bundle), a.display, shots, run_dir, pace_s=a.pace)
    except Refused as e:
        print(f"{TAG} {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in res.items() if k != "com1"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
