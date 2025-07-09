#!/usr/bin/env python3

import asyncio
import logging
import time
import uuid
from io import BytesIO
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientSession, web
from aiohttp.web_request import Request
from aiohttp.web_response import Response

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

CLIENT_LISTEN_PORT = 8082
API_GATEWAY_URL = "http://localhost:9090"  # Proxy server endpoint
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# ─── LOGGING ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("proxy-client-aiohttp")

# ─── AWS AUTHENTICATION ────────────────────────────────────────────────────

try:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import get_credentials
    from botocore.session import get_session

    session = get_session()
    credentials = get_credentials(session)
    aws_auth = SigV4Auth(credentials, "execute-api", "us-east-1") if credentials else None
    logger.info("AWS authentication configured" if aws_auth else "AWS authentication not available")
except ImportError:
    aws_auth = None
    logger.warning("botocore not available, AWS authentication disabled")

# ─── PROXY CLIENT CLASS ────────────────────────────────────────────────────


class AIOHTTPProxyClient:
    """Async HTTP proxy client using aiohttp"""

    def __init__(self):
        self.session = None

    async def __aenter__(self):
        # Create aiohttp client session
        timeout = aiohttp.ClientTimeout(total=300)  # 5 minute timeout
        self.session = ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _create_connection(self, target_host: str, method: str, path: str, headers: dict, body_size: int) -> str:
        """Create a new connection on the proxy server"""
        connection_id = str(uuid.uuid4())
        logger.info(f"Creating connection {connection_id} for {method} {target_host}{path}")

        metadata = {"connection_id": connection_id, "target_host": target_host, "method": method, "path": path, "headers": headers, "body_size": body_size}
        logger.debug(f"Connection metadata: {metadata}")

        # Send connection creation request to proxy server
        url = f"{API_GATEWAY_URL}/proxy/connection"
        headers_req = {"Content-Type": "application/json"}

        logger.debug(f"Sending connection request to {url}")
        start_time = time.time()

        async with self.session.post(url, json=metadata, headers=headers_req) as resp:
            elapsed = time.time() - start_time
            logger.debug(f"Connection creation response: {resp.status} (took {elapsed:.3f}s)")

            if resp.status == 200:
                logger.info(f"Successfully created connection {connection_id}")
                return connection_id
            else:
                error_text = await resp.text()
                logger.error(f"Failed to create connection: {resp.status} - {error_text}")
                raise Exception(f"Failed to create connection: {resp.status}")

    async def _send_chunk(self, connection_id: str, chunk_data: bytes, is_final: bool = False) -> dict:
        """Send a chunk of data to the proxy server"""
        logger.debug(f"Sending chunk for connection {connection_id}: {len(chunk_data)} bytes, final={is_final}")

        headers = {"Content-Type": "application/octet-stream", "X-Connection-ID": connection_id, "X-Chunk-Final": "true" if is_final else "false"}

        url = f"{API_GATEWAY_URL}/proxy/chunk"
        start_time = time.time()

        async with self.session.post(url, data=chunk_data, headers=headers) as resp:
            elapsed = time.time() - start_time
            logger.debug(f"Chunk send response: {resp.status} (took {elapsed:.3f}s)")

            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Failed to send chunk: {resp.status} - {error_text}")
                raise Exception(f"Failed to send chunk: {resp.status}")

            result = await resp.json()
            logger.debug(f"Chunk send result: {result}")
            return result

    async def _get_response_metadata(self, connection_id: str) -> tuple:
        """Get response metadata from the proxy server"""
        logger.debug(f"Getting response metadata for connection {connection_id}")
        headers = {"X-Connection-ID": connection_id, "X-Chunk-Index": "0"}

        url = f"{API_GATEWAY_URL}/proxy/response"
        start_time = time.time()

        async with self.session.get(url, headers=headers) as resp:
            elapsed = time.time() - start_time
            logger.debug(f"Metadata response: {resp.status} (took {elapsed:.3f}s)")

            if resp.status == 200:
                metadata = await resp.json()
                logger.info(f"Got response metadata: status={metadata['status']}, has_body={metadata['has_body']}")
                logger.debug(f"Response headers: {metadata['headers']}")
                return metadata["status"], metadata["headers"], metadata["has_body"]
            else:
                error_text = await resp.text()
                logger.error(f"Failed to get response metadata: {resp.status} - {error_text}")
                raise Exception(f"Failed to get response metadata: {resp.status}")

    async def _get_response_chunk(self, connection_id: str, chunk_index: int) -> tuple:
        """Get a chunk of response body data from the proxy server"""
        logger.debug(f"Getting response chunk {chunk_index} for connection {connection_id}")
        headers = {"X-Connection-ID": connection_id, "X-Chunk-Index": str(chunk_index)}

        url = f"{API_GATEWAY_URL}/proxy/response"
        start_time = time.time()

        async with self.session.get(url, headers=headers) as resp:
            elapsed = time.time() - start_time

            if resp.status == 200:
                has_more = resp.headers.get("X-More-Chunks", "false") == "true"
                content = await resp.read()
                logger.debug(f"Got response chunk {chunk_index}: {len(content)} bytes, has_more={has_more} (took {elapsed:.3f}s)")
                return content, has_more
            else:
                error_text = await resp.text()
                logger.error(f"Failed to get response chunk {chunk_index}: {resp.status} - {error_text}")
                raise Exception(f"Failed to get response chunk: {resp.status}")

    async def handle_proxy_request(self, request: Request) -> Response:
        """Handle HTTP proxy request by splitting into chunks if needed"""
        request_start = time.time()
        method = request.method
        path = str(request.url)
        logger.info(f"=== New {method} request: {path} ===")
        logger.debug(f"Request headers: {dict(request.headers)}")

        try:
            # Parse proxy request
            if path.startswith(("http://", "https://")):
                parsed = urlparse(path)
                request_path = parsed.path or "/"
                if parsed.query:
                    request_path += "?" + parsed.query
                target_host = parsed.netloc
                logger.debug(f"Parsed URL - target_host: {target_host}, path: {request_path}")
            else:
                request_path = path
                target_host = request.headers.get("Host", "")
                logger.debug(f"Direct request - target_host: {target_host}, path: {request_path}")

            # Read request body
            body = await request.read()
            body_size = len(body) if body else 0
            logger.info(f"Request body size: {body_size} bytes")

            # Handle request with unified chunking approach
            response = await self._handle_request_unified(target_host, request_path, method, dict(request.headers), body)

            elapsed = time.time() - request_start
            logger.info(f"=== Request completed in {elapsed:.3f}s ===")
            return response

        except Exception as exc:
            elapsed = time.time() - request_start
            logger.error(f"=== Request failed after {elapsed:.3f}s: {exc} ===")

            # Return proper HTTP error response
            error_message = f"Proxy client error: {exc}"
            return web.Response(text=error_message, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})

    async def _handle_request_unified(self, target_host: str, path: str, method: str, headers: dict, body: bytes) -> Response:
        """Handle all requests with unified chunking approach"""
        logger.info(f"Processing unified request for {target_host}{path}")

        try:
            # Create connection
            connection_id = await self._create_connection(target_host, method, path, headers, len(body) if body else 0)
        except Exception as exc:
            logger.error(f"Failed to create connection: {exc}")
            raise Exception(f"Connection creation failed: {exc}")

        try:
            # Send request body in chunks (always use chunking for consistency)
            chunk_count = 0
            if body:
                logger.info(f"Sending request body in chunks ({len(body)} bytes total)")
                # Split body into chunks
                body_io = BytesIO(body)
                total_sent = 0

                while True:
                    chunk_data = body_io.read(CHUNK_SIZE)
                    if not chunk_data:
                        break

                    chunk_count += 1
                    total_sent += len(chunk_data)
                    is_final = total_sent >= len(body)
                    logger.debug(f"Sending chunk {chunk_count}: {len(chunk_data)} bytes (total sent: {total_sent}/{len(body)})")
                    await self._send_chunk(connection_id, chunk_data, is_final=is_final)
            else:
                logger.info("No request body, sending empty final chunk")
                chunk_count = 1
                await self._send_chunk(connection_id, b"", is_final=True)

            logger.info(f"Sent {chunk_count} chunks total")
        except Exception as exc:
            logger.error(f"Failed to send request chunks: {exc}")
            raise Exception(f"Request transmission failed: {exc}")

        try:
            # Get response metadata first
            status_code, response_headers, has_body = await self._get_response_metadata(connection_id)
            logger.info(f"Got response metadata: status={status_code}, has_body={has_body}")
        except Exception as exc:
            logger.error(f"Failed to get response metadata: {exc}")
            # Return a 502 response since we couldn't get the real response
            error_msg = f"Failed to get response from proxy server: {exc}"
            return web.Response(text=error_msg, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})

        try:
            # Prepare response headers (filter out proxy-specific headers)
            filtered_headers = {}
            for k, v in response_headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "x-more-chunks", "x-chunk-index"):
                    filtered_headers[k] = v

            # Get and collect response body if it exists
            response_body = b""
            if has_body:
                logger.info("Getting response body chunks")
                chunk_index = 1  # Body chunks start from index 1
                response_bytes_received = 0

                while True:
                    response_data, has_more = await self._get_response_chunk(connection_id, chunk_index)
                    response_body += response_data
                    response_bytes_received += len(response_data)
                    logger.debug(f"Received response chunk {chunk_index}: {len(response_data)} bytes (total: {response_bytes_received})")

                    if not has_more:
                        break
                    chunk_index += 1

                logger.info(f"Completed response body: {response_bytes_received} bytes in {chunk_index} chunks")
            else:
                logger.info("No response body to receive")

            # Create and return response
            logger.info(f"Returning response: {status_code} with {len(response_body)} bytes")
            return web.Response(body=response_body, status=status_code, headers=filtered_headers)

        except Exception as exc:
            logger.error(f"Failed to get response: {exc}")
            # Return error response
            error_msg = f"Response transmission failed: {exc}"
            return web.Response(text=error_msg, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})


# ─── SERVER SETUP ──────────────────────────────────────────────────────────


async def create_app() -> web.Application:
    """Create the aiohttp application"""
    app = web.Application()

    # Create proxy client instance
    proxy_client = AIOHTTPProxyClient()
    app["proxy_client"] = proxy_client

    # Add routes - catch all HTTP methods and paths
    app.router.add_route("*", "/{path:.*}", proxy_handler)

    return app


async def proxy_handler(request: Request) -> Response:
    """Route handler that delegates to proxy client"""
    proxy_client = request.app["proxy_client"]
    async with proxy_client:
        return await proxy_client.handle_proxy_request(request)


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    """Main application entry point"""
    app = await create_app()

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", CLIENT_LISTEN_PORT)
    await site.start()

    print(f"Proxy client listening on 0.0.0.0:{CLIENT_LISTEN_PORT}")
    print(f"Configure your application to use http://localhost:{CLIENT_LISTEN_PORT} as HTTP proxy")

    # Keep the server running
    try:
        await asyncio.Future()  # Run forever
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await runner.cleanup()


def run():
    """Run the proxy client"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProxy client stopped")


if __name__ == "__main__":
    run()
