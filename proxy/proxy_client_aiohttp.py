#!/usr/bin/env python3

import asyncio
import json
import logging
import time
import uuid
from io import BytesIO
from urllib.parse import urlparse

import httpx
from quart import Quart, Request, Response

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

CLIENT_LISTEN_PORT = 8080
# API_GATEWAY_URL = "http://localhost:9090"  # Proxy server endpoint
API_GATEWAY_URL = "https://3he3rx88gl.execute-api.us-east-1.amazonaws.com"
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("proxy-client-httpx")


# Target host rewrite configuration
# Maps original host:port to new host:port
TARGET_HOST_REWRITES = {
    "test.com:80": "10.58.210.239:80",
    "test.com": "10.58.210.239:80",  # Also handle without explicit port
    # "test.com": "127.0.0.1:1234",
    # Add more rewrites as needed:
    # "api.example.com:443": "192.168.1.100:443",
    # "old-server.com": "new-server.com",
}

# ─── LOGGING ────────────────────────────────────────────────────────────────

# ─── AWS AUTHENTICATION ────────────────────────────────────────────────────


class AWSAuth:
    """AWS SigV4 authentication for httpx requests"""

    def __init__(self):
        self.auth = None
        self.credentials = None
        try:
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest
            from botocore.credentials import get_credentials
            from botocore.session import get_session

            session = get_session()
            self.credentials = get_credentials(session)
            if self.credentials:
                self.auth = SigV4Auth(self.credentials, "execute-api", "us-east-1")
                logger.info("AWS authentication configured successfully")
            else:
                logger.warning("AWS credentials not found")
        except ImportError:
            logger.warning("botocore not available, AWS authentication disabled")

    def sign_request(self, method: str, url: str, headers: dict, body: bytes = None) -> dict:
        """Sign an HTTP request with AWS SigV4"""
        if not self.auth or not self.credentials:
            logger.debug("No AWS auth configured, returning unsigned headers")
            return headers

        try:
            from botocore.awsrequest import AWSRequest

            # Create AWS request object
            aws_request = AWSRequest(method=method, url=url, headers=headers, data=body)

            # Log request details for debugging
            logger.debug(f"Signing request: {method} {url}")
            logger.debug(f"Request body length: {len(body) if body else 0}")
            logger.debug(f"Original headers: {headers}")

            # Sign the request
            self.auth.add_auth(aws_request)

            # Log signed headers for debugging
            signed_headers = dict(aws_request.headers)
            logger.debug(f"Signed headers: {signed_headers}")

            # Return the signed headers
            return signed_headers

        except Exception as exc:
            logger.error(f"Failed to sign request: {exc}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            return headers


# Global AWS auth instance
aws_auth = AWSAuth()

# ─── PROXY CLIENT CLASS ────────────────────────────────────────────────────


class HTTPXProxyClient:
    """Async HTTP proxy client using httpx"""

    def __init__(self):
        self.client = None

    async def start(self):
        """Start the HTTP client"""
        if self.client is None:
            timeout = httpx.Timeout(300.0)  # 5 minute timeout
            self.client = httpx.AsyncClient(timeout=timeout)
            logger.info("HTTP client started")

    async def stop(self):
        """Stop the HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("HTTP client stopped")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    def _rewrite_target_host(self, original_host: str) -> str:
        """Rewrite target host based on configuration"""
        # Check for exact match first
        if original_host in TARGET_HOST_REWRITES:
            rewritten = TARGET_HOST_REWRITES[original_host]
            logger.info(f"Host rewrite: {original_host} → {rewritten}")
            return rewritten

        # If no port specified, try adding default ports
        if ":" not in original_host:
            # Try with default HTTP port
            http_key = f"{original_host}:80"
            if http_key in TARGET_HOST_REWRITES:
                rewritten = TARGET_HOST_REWRITES[http_key]
                logger.info(f"Host rewrite: {original_host} → {rewritten} (added default port 80)")
                return rewritten

            # Try with default HTTPS port
            https_key = f"{original_host}:443"
            if https_key in TARGET_HOST_REWRITES:
                rewritten = TARGET_HOST_REWRITES[https_key]
                logger.info(f"Host rewrite: {original_host} → {rewritten} (added default port 443)")
                return rewritten

        # No rewrite found, return original
        logger.debug(f"No host rewrite configured for: {original_host}")
        return original_host

    async def _make_signed_request(self, method: str, url: str, headers: dict = None, data: bytes = None, json_data: dict = None, max_retries: int = 3) -> httpx.Response:
        """Make an HTTP request with AWS SigV4 authentication and automatic retry with progressive sleep"""
        if headers is None:
            headers = {}

        # Prepare request body - ensure consistency between signing and sending
        body = data
        if json_data:
            body = json.dumps(json_data, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        # Define retry conditions
        def should_retry(response: httpx.Response = None, exception: Exception = None) -> bool:
            if exception:
                # Don't retry on client closure or state issues
                error_msg = str(exception).lower()
                if "client has been closed" in error_msg or "client is closed" in error_msg:
                    return False
                # Retry on network errors, timeouts, connection errors
                return isinstance(exception, (httpx.RequestError, httpx.TimeoutException, httpx.ConnectError))
            if response:
                # Retry on 5xx server errors, 429 rate limiting, 502/503/504 gateway errors
                return response.status_code in (429, 500, 502, 503, 504)
            return False

        last_exception = None
        last_response = None

        for attempt in range(max_retries + 1):
            try:
                # Sign the request with the exact body that will be sent (resign on each retry for fresh timestamp)
                signed_headers = aws_auth.sign_request(method, url, headers.copy(), body)

                if attempt > 0:
                    logger.info(f"Retry attempt {attempt}/{max_retries} for {method} {url}")

                # Make the request with the same body used for signing
                response = await self.client.request(method, url, headers=signed_headers, content=body)

                # Check if we should retry based on response
                if should_retry(response=response):
                    last_response = response
                    if attempt < max_retries:
                        sleep_time = (2**attempt) * 1.0  # Exponential backoff: 1s, 2s, 4s, 8s...
                        logger.warning(f"Request failed with status {response.status_code}, retrying in {sleep_time}s (attempt {attempt + 1}/{max_retries + 1})")
                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        logger.error(f"Request failed after {max_retries} retries, final status: {response.status_code}")
                        return response

                # Success case
                if attempt > 0:
                    logger.info(f"Request succeeded on retry attempt {attempt}")
                return response

            except Exception as exc:
                last_exception = exc
                if should_retry(exception=exc):
                    if attempt < max_retries:
                        sleep_time = (2**attempt) * 1.0  # Exponential backoff: 1s, 2s, 4s, 8s...
                        logger.warning(f"Request failed with exception {type(exc).__name__}: {exc}, retrying in {sleep_time}s (attempt {attempt + 1}/{max_retries + 1})")
                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        logger.error(f"Request failed after {max_retries} retries with exception: {exc}")
                        raise exc
                else:
                    # Don't retry on non-retryable exceptions
                    logger.error(f"Request failed with non-retryable exception: {exc}")
                    raise exc

        # This should never be reached, but just in case
        if last_exception:
            raise last_exception
        if last_response:
            return last_response
        raise Exception("Unexpected error in retry logic")

    async def _create_connection(self, target_host: str, method: str, path: str, headers: dict, body_size: int) -> str:
        """Create a new connection on the proxy server"""
        connection_id = str(uuid.uuid4())
        logger.info(f"Creating connection {connection_id} for {method} {target_host}{path}")

        metadata = {"connection_id": connection_id, "target_host": target_host, "method": method, "path": path, "headers": headers, "body_size": body_size}
        logger.debug(f"Connection metadata: {metadata}")

        # Send connection creation request to proxy server
        url = f"{API_GATEWAY_URL}/proxy/connection"
        logger.debug(f"Sending connection request to {url}")
        start_time = time.time()

        resp = await self._make_signed_request("POST", url, json_data=metadata)
        elapsed = time.time() - start_time
        logger.debug(f"Connection creation response: {resp.status_code} (took {elapsed:.3f}s)")

        if resp.status_code == 200:
            logger.info(f"Successfully created connection {connection_id}")
            return connection_id
        else:
            logger.error(f"Failed to create connection: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to create connection: {resp.status_code}")

    async def _send_chunk(self, connection_id: str, chunk_data: bytes, is_final: bool = False) -> dict:
        """Send a chunk of data to the proxy server"""
        logger.debug(f"Sending chunk for connection {connection_id}: {len(chunk_data)} bytes, final={is_final}")

        headers = {"Content-Type": "application/octet-stream", "X-Connection-ID": connection_id, "X-Chunk-Final": "true" if is_final else "false"}

        url = f"{API_GATEWAY_URL}/proxy/chunk"
        start_time = time.time()

        resp = await self._make_signed_request("POST", url, headers=headers, data=chunk_data)
        elapsed = time.time() - start_time
        logger.debug(f"Chunk send response: {resp.status_code} (took {elapsed:.3f}s)")

        if resp.status_code != 200:
            logger.error(f"Failed to send chunk: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to send chunk: {resp.status_code}")

        result = resp.json()
        logger.debug(f"Chunk send result: {result}")
        return result

    async def _get_response_metadata(self, connection_id: str) -> tuple:
        """Get response metadata from the proxy server"""
        logger.debug(f"Getting response metadata for connection {connection_id}")
        headers = {"X-Connection-ID": connection_id, "X-Chunk-Index": "0"}

        url = f"{API_GATEWAY_URL}/proxy/response"
        start_time = time.time()

        resp = await self._make_signed_request("GET", url, headers=headers)
        elapsed = time.time() - start_time
        logger.debug(f"Metadata response: {resp.status_code} (took {elapsed:.3f}s)")

        if resp.status_code == 200:
            metadata = resp.json()
            logger.info(f"Got response metadata: status={metadata['status']}, has_body={metadata['has_body']}")
            logger.debug(f"Response headers: {metadata['headers']}")
            return metadata["status"], metadata["headers"], metadata["has_body"]
        else:
            logger.error(f"Failed to get response metadata: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to get response metadata: {resp.status_code}")

    async def _get_response_chunk(self, connection_id: str, chunk_index: int) -> tuple:
        """Get a chunk of response body data from the proxy server"""
        logger.debug(f"Getting response chunk {chunk_index} for connection {connection_id}")
        headers = {"X-Connection-ID": connection_id, "X-Chunk-Index": str(chunk_index)}

        url = f"{API_GATEWAY_URL}/proxy/response"
        start_time = time.time()

        resp = await self._make_signed_request("GET", url, headers=headers)
        elapsed = time.time() - start_time

        if resp.status_code == 200:
            has_more = resp.headers.get("X-More-Chunks", "false") == "true"
            content = resp.content
            logger.debug(f"Got response chunk {chunk_index}: {len(content)} bytes, has_more={has_more} (took {elapsed:.3f}s)")
            return content, has_more
        else:
            logger.error(f"Failed to get response chunk {chunk_index}: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to get response chunk: {resp.status_code}")

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
                original_target_host = parsed.netloc
                logger.debug(f"Parsed URL - original target_host: {original_target_host}, path: {request_path}")
            else:
                request_path = path
                original_target_host = request.headers.get("Host", "")
                logger.debug(f"Direct request - original target_host: {original_target_host}, path: {request_path}")

            # Apply host rewriting
            target_host = self._rewrite_target_host(original_target_host)

            # Read request body
            body = await request.get_data()
            body_size = len(body) if body else 0
            logger.info(f"Request body size: {body_size} bytes")

            # Handle request with unified chunking approach
            response = await self._handle_request_unified(target_host, request_path, method, dict(request.headers), body, original_target_host)

            elapsed = time.time() - request_start
            logger.info(f"=== Request completed in {elapsed:.3f}s ===")
            return response

        except Exception as exc:
            elapsed = time.time() - request_start
            logger.error(f"=== Request failed after {elapsed:.3f}s: {exc} ===")

            # Return proper HTTP error response
            error_message = f"Proxy client error: {exc}"
            return Response(response=error_message, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})

    async def _handle_request_unified(self, target_host: str, path: str, method: str, headers: dict, body: bytes, original_host: str = None) -> Response:
        """Handle all requests with unified chunking approach"""
        logger.info(f"Processing unified request for {target_host}{path}")
        if original_host and original_host != target_host:
            logger.info(f"Original host: {original_host} → Rewritten to: {target_host}")

        try:
            # Update headers to preserve original Host header for the target server
            updated_headers = headers.copy()
            if original_host:
                updated_headers["Host"] = original_host
                logger.debug(f"Preserving original Host header: {original_host}")

            # Create connection
            connection_id = await self._create_connection(target_host, method, path, updated_headers, len(body) if body else 0)
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
            return Response(response=error_msg, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})

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
            return Response(response=response_body, status=status_code, headers=filtered_headers)

        except Exception as exc:
            logger.error(f"Failed to get response: {exc}")
            # Return error response
            error_msg = f"Response transmission failed: {exc}"
            return Response(response=error_msg, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})


# ─── GLOBAL PROXY CLIENT ───────────────────────────────────────────────────

# Global proxy client instance
proxy_client = HTTPXProxyClient()

# ─── APP SETUP ─────────────────────────────────────────────────────────────

# Create Quart application
app = Quart(__name__)

# ─── ROUTE HANDLERS ────────────────────────────────────────────────────────


@app.before_request
async def handle_all_requests():
    """Handle all requests through the proxy client"""
    from quart import request

    return await proxy_client.handle_proxy_request(request)


# ─── STARTUP/SHUTDOWN HOOKS ──────────────────────────────────────────────────


@app.before_serving
async def startup():
    """Initialize the proxy client on startup"""
    logger.info("Starting proxy client...")
    await proxy_client.start()


@app.after_serving
async def shutdown():
    """Cleanup the proxy client on shutdown"""
    logger.info("Shutting down proxy client...")
    await proxy_client.stop()


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    """Main application entry point"""
    print(f"Proxy client listening on 0.0.0.0:{CLIENT_LISTEN_PORT}")
    print(f"Configure your application to use http://localhost:{CLIENT_LISTEN_PORT} as HTTP proxy")
    print(f"AWS Authentication: {'Enabled' if aws_auth.auth else 'Disabled'}")

    # Show host rewrite configuration
    if TARGET_HOST_REWRITES:
        print(f"Host Rewrites Configured: {len(TARGET_HOST_REWRITES)} rules")
        for original, rewritten in TARGET_HOST_REWRITES.items():
            print(f"  {original} → {rewritten}")
    else:
        print("Host Rewrites: None configured")

    # Start the server
    await app.run_task(host="0.0.0.0", port=CLIENT_LISTEN_PORT, debug=False)


def run():
    """Run the proxy client"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProxy client stopped")


if __name__ == "__main__":
    run()
