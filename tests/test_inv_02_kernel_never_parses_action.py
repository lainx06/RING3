"""INV-02 (static): no kernel source touches an ACTION / TEXT / FINAL / STOP body field. The body-field names
of the ABI header and the op numbers may appear only in ring-3 code (user/) and the bridge."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "kernel"
BODY_FIELD_TOKENS = (
    "LINK_ACT_OFF_", "LINK_ACT_RSV_LEN", "LINK_OP_", "LINK_TEXT_OFF_", "LINK_FINAL_OFF_", "LINK_FINAL_DONE",
    "LINK_FINAL_REFUSE", "LINK_STOP_OFF_", "LINK_STOP_INVALID", "LINK_STOP_BUDGET", "LINK_STOP_TIMEOUT",
    "LINK_STOP_MODEL_AUTH", "LINK_STOP_TRANSPORT", "LINK_STOP_OPERATOR", "LINK_DUTY_MAX", "LINK_DEG_MAX",
    "LINK_LCD_", "LINK_OBJ_TEXT_MAX", "json", "JSON",
)  # fmt: skip


def test_inv_02_kernel_never_parses_action():
    hits = []
    for p in sorted(KERNEL.rglob("*.[ch]")):
        text = p.read_text()
        for tok in BODY_FIELD_TOKENS:
            for m in re.finditer(re.escape(tok), text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{p.relative_to(REPO)}:{line}: {tok}")
    assert not hits, "kernel code references an ACTION/TEXT/FINAL/STOP body field:\n" + "\n".join(hits)
    # and the link layer forwards bodies with memcpy only: no indexed read of a queued body
    src = (KERNEL / "core" / "agentlink.c").read_text()
    assert not re.search(r"q->body\[|->body\[\w+\]\s*[=!<>]", src), "agentlink.c indexes a frame body"
    assert "memcpy(q->body, dec + LINK_OFF_BODY, len)" in src
