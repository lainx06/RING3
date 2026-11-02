"""Camera witness (T-07, D-22, D-29, D-56, D-58, INV-14): reports evidence about a physical servo move.

Pipeline for one check (``pipeline.check``): commanded zone -> session calibration -> 4 ArUco markers in
*this* frame (``dial``) -> warp-crop to the dial -> SAM 3.1 boxes (``sam``, one image reserved first) ->
exactly one plausible arm, angle from the pivot through the box centre (``angle``) -> calibrated angle -> zone
and verdict (``verdict``).

Results are PASS / FAIL / CAN'T VERIFY. Every stage that is unsure raises ``CantVerify``; the pipeline returns
at the first one, so a CAN'T VERIFY is never upgraded. Nothing here writes to the kernel link, imports the
link writer or reads the model key: the witness never changes a kernel decision (INV-14, checked statically by
``tests/witness/test_inv_14_witness.py``). Authentication and transport failures are not CAN'T VERIFY: they
propagate and stop the run (INV-09).
"""

from __future__ import annotations

PASS = "PASS"
FAIL = "FAIL"
CANT_VERIFY = "CAN'T VERIFY"

# CAN'T VERIFY reasons
MARKERS = "MARKERS"  # fewer than 4 markers in the frame, duplicates, or a poor marker fit
SAM_FORMAT = "SAM_FORMAT"  # SAM reply or record line the parser doesn't accept
NOT_FOUND = "NOT_FOUND"  # SAM returned no record (concept not present)
AMBIGUOUS = "AMBIGUOUS"  # 2 or more records pass the arm-shape filter
SHAPE = "SHAPE"  # records, but none shaped like an arm on this pivot
LOW_CONFIDENCE = "LOW_CONFIDENCE"  # the one arm record has no or too low a confidence
BOUNDARY = "BOUNDARY"  # measured angle inside a boundary band
CALIBRATION = "CALIBRATION"  # no calibration, or the witness is disabled for the session
STALE_FRAME = "STALE_FRAME"  # missing, old, mismatched or undecodable capture
COMMAND_BOUNDARY = "COMMAND_BOUNDARY"  # the commanded angle itself is inside a boundary band
COMMAND_RANGE = "COMMAND_RANGE"  # the commanded angle is outside 30-150 deg (the kernel would not allow it)

# FAIL / PASS reasons
WRONG_ZONE = "WRONG_ZONE"
OUT_OF_SCALE = "OUT_OF_SCALE"  # below 25 deg or above 155 deg: FAIL, never PASS
ZONE_MATCH = "ZONE_MATCH"

CANT_VERIFY_REASONS = frozenset(
    {MARKERS, SAM_FORMAT, NOT_FOUND, AMBIGUOUS, SHAPE, LOW_CONFIDENCE, BOUNDARY, CALIBRATION, STALE_FRAME,
     COMMAND_BOUNDARY, COMMAND_RANGE}
)  # fmt: skip


class CantVerify(Exception):
    """Raised by any witness stage that cannot say PASS or FAIL. Never caught to continue a check."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in CANT_VERIFY_REASONS:
            raise ValueError(f"unknown CAN'T VERIFY reason {reason!r}")
        super().__init__(f"{CANT_VERIFY}: {reason}" + (f" ({detail})" if detail else ""))
        self.reason = reason
        self.detail = detail
