"""LSP message framing over byte streams.

The Language Server Protocol uses HTTP-style headers to delimit JSON
messages. Each message looks like::

    Content-Length: 123\r\n
    \r\n
    {"jsonrpc": "2.0", "id": 1, ...}

Only ``Content-Length`` is mandatory; every other header is ignored by
spec-compliant clients and servers. This module contains no LSP semantics
— it only knows how to read and write framed JSON.

The functions in this module are stream-agnostic: they take an
``asyncio.StreamReader`` / ``StreamWriter`` pair and do not assume the
stream comes from a subprocess, a socket, or an in-memory pipe.
"""

from __future__ import annotations

import json
from typing import Any

# Maximum permitted Content-Length. Defends against a misbehaving peer
# that asks us to allocate gigabytes. 64 MiB is comfortably above the
# largest legitimate LSP response we expect (WorkspaceEdit on a big
# workspace, for instance).
_MAX_CONTENT_LENGTH = 64 * 1024 * 1024

# Maximum number of header bytes we will scan for the blank line that
# terminates the header block. Prevents an attacker (or a bug) from
# sending an unterminated header stream that would otherwise consume
# unlimited memory.
_MAX_HEADER_BYTES = 64 * 1024


class LSPProtocolError(Exception):
    """Raised when a peer violates the LSP framing rules."""


async def read_message(reader: Any) -> dict[str, Any] | None:
    """Read one LSP message from *reader*.

    Returns ``None`` on clean EOF (the peer closed the stream after the
    last complete message). Raises :class:`LSPProtocolError` on malformed
    input.
    """
    headers: dict[str, str] = {}
    header_buf = bytearray()
    while True:
        chunk = await reader.readline()
        if not chunk:
            # EOF: only valid if we haven't read any header bytes yet.
            if header_buf:
                raise LSPProtocolError("unexpected EOF in header block")
            return None
        if len(header_buf) + len(chunk) > _MAX_HEADER_BYTES:
            raise LSPProtocolError("header block exceeds maximum size")
        header_buf.extend(chunk)
        # A blank line (just "\r\n" or "\n") terminates the header block.
        if chunk in (b"\r\n", b"\n"):
            break
        # Strip the trailing line ending before splitting.
        line = chunk.rstrip(b"\r\n").decode("ascii", errors="replace")
        if ":" not in line:
            raise LSPProtocolError(f"malformed header line: {line!r}")
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()

    raw_length = headers.get("content-length")
    if raw_length is None:
        raise LSPProtocolError("missing Content-Length header")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise LSPProtocolError(f"invalid Content-Length: {raw_length!r}") from exc
    if length < 0 or length > _MAX_CONTENT_LENGTH:
        raise LSPProtocolError(f"Content-Length out of range: {length}")

    body = await reader.readexactly(length)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LSPProtocolError(f"invalid JSON body: {exc}") from exc


async def write_message(writer: Any, message: dict[str, Any]) -> None:
    """Serialize *message* and write it framed to *writer*."""
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    writer.write(header)
    writer.write(body)
    await writer.drain()
