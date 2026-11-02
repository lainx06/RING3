"""Transport layer: the only code that reads ``MODEL_API_KEY`` and the only code that sees request headers.

The key is read from the environment (never from ``.env`` or any file), kept in a private attribute, put into
the ``Authorization`` header and nowhere else. Response bodies are returned with any occurrence of the key
replaced. Errors are re-raised as ``TransportError`` built *outside* the ``except`` block, so no original
exception (which might carry a request or headers) is chained or kept as context.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

KEY_ENV = "MODEL_API_KEY"
REDACTED = b"[REDACTED]"
MAX_RESPONSE_BYTES = 1 << 20


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

    def __repr__(self) -> str:
        return "HttpsTransport(key=<hidden>)"

    def _scrub(self, b: bytes) -> bytes:
        return b.replace(self.__key.encode("utf-8", "replace"), REDACTED)

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
