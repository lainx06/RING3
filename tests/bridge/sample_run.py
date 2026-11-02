"""A small, realistic sample run log for tests and evidence (no network, fake data, synthetic price)."""

from pathlib import Path

from ring3bridge import abi
from ring3bridge.eventlog import EventLog


def write_sample_run(run_dir: Path) -> EventLog:
    log = EventLog(run_dir)
    seed = bytes(range(16))
    log.append("run_start", run_id=run_dir.name, kernel_sha256="1" * 64, policy_sha256="2" * 64,
               context_pack_sha256="3" * 64, model_id="muse-spark-1.3", seed=seed.hex(),
               abi_version=abi.LINK_ABI_VERSION, budget_uusd=500_000)  # fmt: skip
    req = log.blob(b"self-test the cell and report anything abnormal")
    log.append("user_request", text_blob=req, bytes=48)
    log.append("spend_reserved", reservation=1, uusd=4124, in_tokens=1400, max_out_tokens=512)
    log.append("model_request", request_blob=log.blob(b'{"model":"muse-spark-1.3","input":"..."}'), bytes=40)
    reply = b'{"action":"call","op":"fan_set","args":{"duty":60}}'
    log.append("model_reply", blob=log.blob(reply), bytes=len(reply), status=200)
    log.append("spend_settled", reservation=1, uusd=3200)
    log.append("action_sent", index=0, op="fan_set", p1=60, nonce=12345678, text_id=0,
               frames_blob=log.blob(b"\x03\x90\x00\x00\x10\x00"))  # fmt: skip
    log.append("event", kind=3, sub=0, flags=1, tick=1234, nr=6, a=[60, 0, 0, 0])
    log.append("audit", audit_seq=7, tick=1234, kind=3, nr=6, code=10, reason=0, target=60, result=0,
               tainted=0, permissive=0, code_name="CONFIRM_PENDING", reason_name="none")  # fmt: skip
    log.append("observation", nonce=12345678, op="fan_set", result=0, truncated=0,
               output_blob=log.blob(b"fan 60\n"))  # fmt: skip
    log.append(
        "reply_rejected",
        reason="float",
        blob=log.blob(b'{"action":"call","op":"fan_set","args":{"duty":60.0}}'),
    )
    log.append("request_end", outcome="DONE", actions=1)
    log.append("run_end", records=13)
    return log
