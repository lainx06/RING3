"""Transport layer: the only code that reads ``MODEL_API_KEY`` and the only code that sees request headers.

The key is read from the environment (never from ``.env`` or any file), kept in a private attribute, put into
the ``Authorization`` header and nowhere else. Response bodies are returned with any occurrence of the key
replaced, through JSON escapes too (``Redactor``, T-09 F2): the literal key in any letter case, and the key
written as ``\\u00XX`` escapes (upper- or lower-case hex, all or some characters) inside any JSON string of
the body, at any nesting depth up to ``NEST_DEPTH`` (the API envelope, the action JSON the model embeds in
its text, one more). The same ``Redactor`` is applied by the client to every decoded string it returns, so
nothing leaves ``models/`` with the key in it. Errors are re-raised as ``TransportError`` built *outside*
the ``except`` block, so no original exception (which might carry a request or headers) is chained or kept
as context.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

KEY_ENV = "MODEL_API_KEY"
REDACTED = b"[REDACTED]"
REDACTED_TEXT = "[REDACTED]"
MAX_RESPONSE_BYTES = 1 << 20
NEST_DEPTH = 3  # envelope string -> action JSON string inside it -> one more
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')  # one JSON string token; its escapes are left to json.loads


class Redactor:
    """Holds the key privately and replaces it wherever it could reappear after decoding (T-09 F2).

    ``text()``: the literal key (any letter case) in a decoded string, then every JSON string token the
    string contains is decoded and redacted the same way, ``depth`` levels down; a token whose decoded value
    changed is re-encoded (ASCII escapes), every other byte is left as it was. ``body()``: the same on the
    raw response bytes (invalid UTF-8 survives untouched through ``surrogateescape``)."""

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("empty key")
        self.__pat = re.compile(re.escape(key), re.IGNORECASE)
        self.__pat_bytes = re.compile(re.escape(key.encode("utf-8", "replace")), re.IGNORECASE)

    def __repr__(self) -> str:
        return "Redactor(key=<hidden>)"

    def text(self, s: str, depth: int = NEST_DEPTH) -> str:
        s = self.__pat.sub(REDACTED_TEXT, s)
        if depth <= 0 or "\\" not in s:
            return s

        def fix(m: re.Match[str]) -> str:
            tok = m.group(0)
            try:
                val = json.loads(tok)
            except ValueError:
                return tok
            if type(val) is not str:
                return tok
            new = self.text(val, depth - 1)
            return tok if new == val else json.dumps(new, ensure_ascii=True)

        return _JSON_STRING.sub(fix, s)

    def body(self, b: bytes) -> bytes:
        b = self.__pat_bytes.sub(REDACTED, b)
        if b"\\" not in b:
            return b
        s = b.decode("utf-8", "surrogateescape")
        new = self.text(s)
        return b if new == s else new.encode("utf-8", "surrogateescape")


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes  # scrubbed of the key


class TransportError(Exception):
    """Header-free, key-free transport failure. ``kind``: timeout | network | no_key | too_large."""

    def __init__(self, kind: str, status: int = 0) -> None:
        super().__init__(f"transport {kind}" + (f" (HTTP {status})" if status else ""))
        self.kind = kind
        self.status = status


class Transport(Protocol):
    """``redactor`` (optional attribute): a ``Redactor`` the client applies to decoded reply strings."""

    def post(self, url: str, body: bytes, timeout_s: int) -> TransportResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib forwards the Authorization header on redirects, even to another host: refuse every redirect
    (the 3xx then surfaces as a non-200 status, which stops the run)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urlopen(req: urllib.request.Request, timeout: int):  # noqa: ANN202 - tests replace this function
    return _OPENER.open(req, timeout=timeout)


class HttpsTransport:
    """Real HTTPS transport (used only by a paid run, T-03b, after the user's go-ahead)."""

    def __init__(self) -> None:
        key = os.environ.get(KEY_ENV, "")
        if not key:
            raise TransportError("no_key")
        self.__key = key
        self.redactor = Redactor(key)

    def __repr__(self) -> str:
        return "HttpsTransport(key=<hidden>)"

    def _scrub(self, b: bytes) -> bytes:
        return self.redactor.body(b)

    def post(self, url: str, body: bytes, timeout_s: int) -> TransportResponse:
        if not url.startswith("https://"):
            raise TransportError("network")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + self.__key)
        failure: tuple[str, int] | None = None
        result: TransportResponse | None = None
        try:
            with _urlopen(req, timeout_s) as resp:
                data = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    failure = ("too_large", resp.status)
                else:
                    result = TransportResponse(resp.status, self._scrub(data))
        except urllib.error.HTTPError as e:  # a non-2xx status: keep the status, scrub the body
            try:
                data = e.read(MAX_RESPONSE_BYTES)
            except Exception:  # noqa: BLE001
                data = b""
            result = TransportResponse(e.code, self._scrub(data))
        except TimeoutError:  # socket.timeout is an alias
            failure = ("timeout", 0)
        except urllib.error.URLError as e:
            failure = ("timeout" if isinstance(e.reason, TimeoutError) else "network", 0)
        except Exception:  # noqa: BLE001 - anything else is a transport error; details dropped on purpose
            failure = ("network", 0)
        finally:
            del req
        if failure is not None:
            raise TransportError(*failure)
        assert result is not None
        return result
