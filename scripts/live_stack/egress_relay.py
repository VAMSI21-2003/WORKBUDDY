"""Forward a TCP port to the sandbox's egress proxy, so a container on a bridge network can use it.

Only for the cloud sandbox the live runs are made in, where the container's way to the LLM is an
HTTP proxy on the machine's own loopback (``HTTPS_PROXY``), which a bridge-network container can't
reach. Not part of the deployment: on a real VM the container talks to ``api.groq.com`` directly.

    python scripts/live_stack/egress_relay.py LISTEN_PORT        # forwards to the HTTPS_PROXY's port
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlparse


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def main(listen: int, target_host: str, target_port: int) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(target_host, target_port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    server = await asyncio.start_server(handle, "0.0.0.0", listen)  # noqa: S104
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    proxy = urlparse(os.environ["HTTPS_PROXY"])
    asyncio.run(main(int(sys.argv[1]), proxy.hostname or "127.0.0.1", proxy.port or 3128))
