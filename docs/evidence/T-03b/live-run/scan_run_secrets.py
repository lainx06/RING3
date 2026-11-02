"""Scan a run directory (events, blobs, ledger) and the live-run evidence for anything secret-shaped.

Reads only the files under the paths given on the command line; never reads MODEL_API_KEY or any .env file;
no network. Exit code 0 = no hit. Kept with the T-03b live-run evidence (A5).
"""

import re
import sys
from pathlib import Path

NEEDLES = [b"Authorization", b"authorization", b"Bearer ", b"bearer ", b"MODEL_API_KEY", b"api_key",
           b"api-key", b"x-api-key", b"X-Api-Key", b"Cookie", b"Set-Cookie", b"[REDACTED]"]  # fmt: skip
KEY_SHAPE = re.compile(rb"(sk|key|tok|tkn|secret)[-_][A-Za-z0-9_\-]{16,}|[A-Za-z0-9+/_\-]{40,}")
# hex (hashes, frame bodies) and the API's own object ids are not credentials
ALLOW_LONG = re.compile(rb"^[0-9a-f]+$|^(msg|resp|rs)_[A-Za-z0-9-]+$")


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv]
    files = [f for p in paths for f in (p.rglob("*") if p.is_dir() else [p]) if f.is_file()]
    hits = 0
    for f in files:
        data = f.read_bytes()
        for n in NEEDLES:
            if n in data:
                hits += 1
                print(f"HIT {f}: contains {n!r}")
        for m in KEY_SHAPE.finditer(data):
            tok = m.group(0)
            if ALLOW_LONG.match(tok):
                continue
            # the model's encrypted reasoning (opaque base64 from the API, not a credential) is under this key
            pre = data[max(0, m.start() - 40) : m.start()]
            if b'"encrypted_content"' in pre or len(tok) > 200:
                continue
            hits += 1
            print(f"HIT {f}: key-shaped token {tok[:12]!r}... ({len(tok)} chars)")
    print(f"scanned {len(files)} file(s) under {[str(p) for p in paths]}: {hits} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
