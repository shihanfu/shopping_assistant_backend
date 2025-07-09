#!/usr/bin/env python3
"""
aws_ws_repl.py
==============

Interactive REPL that connects to an *AWS API Gateway WebSocket* (or any Sig-V4
WebSocket endpoint).

Features
--------
1. Automatically signs the WebSocket **hand-shake** with Sig V4 headers
   (region + service configurable).
2. Prints every frame sent/received with timestamps (debug tracing).
3. Reads *stdin* → sends to WS, and echoes WS → stdout (line-buffered).

Dependencies
------------
pip install websockets botocore aioconsole
"""

import asyncio
import json
import os
import signal
import sys
from datetime import datetime
from urllib.parse import urlparse

import aioconsole  # async input()
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session
from websockets import connect

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG – edit to match your endpoint
# ──────────────────────────────────────────────────────────────────────────────
WS_URL = os.environ.get("WS_URL", "wss://abcde12345.execute-api.us-east-1.amazonaws.com/prod")
REGION = os.environ.get("AWS_REGION", "us-east-1")
SERVICE = os.environ.get("AWS_SERVICE", "execute-api")  # typical for API GW

# ──────────────────────────────────────────────────────────────────────────────


def sign_websocket_headers(url: str, region: str, service: str) -> list[tuple[str, str]]:
    """
    Returns a list of (header, value) tuples with Sig-V4 authentication
    for the initial `GET` upgrade request done by the WebSocket client.
    The `websockets` library will add `Host`, `Upgrade`, `Connection`,
    `Sec-WebSocket-Key`, etc., so we must sign with *all* headers that
    will be on the wire **except** those added later by the library.
    """
    parsed = urlparse(url)
    host = parsed.netloc

    # (1) resolve AWS credentials (env vars, config file, EC2/ECS role, etc.)
    session = Session()
    creds = session.get_credentials()  # RefreshableCredentials
    frozen_creds = creds.get_frozen_credentials()

    # (2) craft an AWSRequest as if we were doing the handshake
    request = AWSRequest(method="GET", url=f"https://{host}{parsed.path or '/'}", data=b"")
    request.headers.add_header("Host", host)
    print(request.url)
    print(request.headers)

    # (3) sign it
    SigV4Auth(frozen_creds, service, region).add_auth(request)

    # Extract the signed headers back as a list for websockets.connect(extra_headers)
    return list(request.headers.items())


async def stdin_to_ws(ws):
    """Read from console and forward to the WebSocket."""
    async for line in aioconsole.ainput(prompt=">>> "):
        if not line:
            continue
        await ws.send(line)
        ts = datetime.utcnow().isoformat(timespec="seconds")
        print(f"[{ts}]  →  sent  {repr(line)}")


async def ws_to_stdout(ws):
    """Read from the WebSocket and print to console."""
    async for msg in ws:
        ts = datetime.utcnow().isoformat(timespec="seconds")
        print(f"[{ts}]  ←  recv  {repr(msg)}")


async def main():
    # Prepare Sig-V4 headers
    headers = sign_websocket_headers(WS_URL, REGION, SERVICE)
    print("Connecting with headers:")
    print(json.dumps(dict(headers), indent=2))

    # Establish WebSocket
    async with connect(WS_URL, additional_headers=headers) as ws:
        print("Connected!")

        # Launch bidirectional coroutines
        tasks = [
            asyncio.create_task(stdin_to_ws(ws)),
            asyncio.create_task(ws_to_stdout(ws)),
        ]

        # Run until either coroutine closes (Ctrl-D or server closes)
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()


if __name__ == "__main__":
    # Graceful ^C
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(0))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
