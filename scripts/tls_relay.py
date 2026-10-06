"""A TLS-terminating TCP relay for trying a production-mode container from a browser.

The image runs in production mode (``WORKBUDDY_ENV=production``): an https public origin, ``Secure``
cookies, HSTS. A browser on this machine can only reach it over https if something terminates TLS
in front, as a host's proxy does. This relays raw bytes from ``https://localhost:<listen>`` (a
throw-away self-signed certificate, so the browser must be told to ignore certificate errors) to
``127.0.0.1:<target>``. It parses no HTTP and adds no headers: not a model of any host, only a way
to open the page the way a user will. Never use it for anything public.

Usage::

    python scripts/tls_relay.py LISTEN_PORT TARGET_PORT
"""

from __future__ import annotations

import asyncio
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path


def self_signed(folder: Path) -> tuple[Path, Path]:
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, ssl.SSLError):
        pass
    finally:
        writer.close()


async def serve(listen: int, target: int, cert: Path, key: Path) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", target)
        except OSError:
            writer.close()
            return
        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))

    server = await asyncio.start_server(handle, "127.0.0.1", listen, ssl=context)
    async with server:
        await server.serve_forever()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    folder = Path(tempfile.mkdtemp(prefix="tls-relay-"))
    cert, key = self_signed(folder)
    asyncio.run(serve(int(sys.argv[1]), int(sys.argv[2]), cert, key))


if __name__ == "__main__":
    main()
