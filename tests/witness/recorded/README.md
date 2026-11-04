# Recorded SAM 3.1 replies (T-07 Part B)

Exact reply bodies from the user's paid probes on 2026-11-03 (`runs/probe-20261103-202557` and
`runs/probe-20261103-202522`), all for the same camera frame (SHA-256 prefix `7855cbe2624a`), full dial crop
920 x 570 px. Copied byte for byte from the runs' `sam_reply_blob`s; checked to contain no key, no
`Authorization`/`Bearer` text and no long token strings.

| file | phrase | text |
|---|---|---|
| `sam-blue-servo-motor.json` | blue servo motor | `<0f>` + 1 record |
| `sam-black-square.json` | black square | `<0f>` + 4 records |
| `sam-empty-black-plastic-arm.json` | black plastic arm | empty: concept not present |
| `frame-7855cbe2624a-side-mount-90.jpg` | (camera frame) | the full 1920 x 1080 frame those replies came from: servo on its side at 90 deg (D-78, void for calibration; used to check the hub readout says "not found") |
| `frame-upright-213006.jpg` | (camera frame) | `witness-frame-20261103-213006` after the D-78 remount (servo on its back): the hub readout's real positive case (lead's hand measurement: dx about -2.4 mm, dy 0.0 mm) |
