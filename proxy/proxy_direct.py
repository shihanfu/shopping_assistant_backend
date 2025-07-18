#!/usr/bin/env python3

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx
from quart import Quart, Request, Response

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

CLIENT_LISTEN_PORT = 8082
REQUEST_TIMEOUT = 30.0

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("direct-proxy")

# Target host rewrite configuration
# Maps original host:port to new host:port
TARGET_HOST_REWRITES = {
    # "metis.lti.cs.cmu.edu:7770": "10.58.210.60:80",
    # "test.com": "127.0.0.1:1234",
    # Add more rewrites as needed:
    # "api.example.com:443": "192.168.1.100:443",
    # "old-server.com": "new-server.com",
}

# ─── DIRECT PROXY CLASS ────────────────────────────────────────────────────


class DirectProxy:
    """Direct HTTP proxy - no chunking, minimal modification"""

    def __init__(self):
        self.client = None

    async def start(self):
        """Start the HTTP client"""
        if self.client is None:
            timeout = httpx.Timeout(REQUEST_TIMEOUT)
            self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
            logger.info("HTTP client started")

    async def stop(self):
        """Stop the HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("HTTP client stopped")

    def _rewrite_target_host(self, original_host: str, headers: dict = None) -> str:
        """Rewrite target host based on configuration and request headers"""
        # Check for dynamic rewrite in request header first
        if headers:
            # Case-insensitive header lookup
            rewrite_header = ""
            for key, value in headers.items():
                if key.lower() == "x-target-host-rewrite":
                    rewrite_header = value.strip()
                    break

            if rewrite_header:
                # Parse header format: "original_host=new_host" or "original_host:port=new_host:port"
                if "=" in rewrite_header:
                    header_original, header_rewritten = rewrite_header.split("=", 1)
                    header_original = header_original.strip()
                    header_rewritten = header_rewritten.strip()

                    # Check if this request matches the header rewrite rule
                    if header_original == original_host:
                        logger.info(f"Dynamic host rewrite from header: {original_host} → {header_rewritten}")
                        return header_rewritten

                    # Also check with default ports if no port specified
                    if ":" not in original_host:
                        if header_original in (f"{original_host}:80", f"{original_host}:443"):
                            logger.info(f"Dynamic host rewrite from header: {original_host} → {header_rewritten} (matched with default port)")
                            return header_rewritten
                else:
                    logger.warning(f"Invalid x-target-host-rewrite header format: {rewrite_header} (expected 'original=new')")

        # Check for exact match in static configuration
        if original_host in TARGET_HOST_REWRITES:
            rewritten = TARGET_HOST_REWRITES[original_host]
            logger.info(f"Static host rewrite: {original_host} → {rewritten}")
            return rewritten

        # If no port specified, try adding default ports
        if ":" not in original_host:
            # Try with default HTTP port
            http_key = f"{original_host}:80"
            if http_key in TARGET_HOST_REWRITES:
                rewritten = TARGET_HOST_REWRITES[http_key]
                logger.info(f"Static host rewrite: {original_host} → {rewritten} (added default port 80)")
                return rewritten

            # Try with default HTTPS port
            https_key = f"{original_host}:443"
            if https_key in TARGET_HOST_REWRITES:
                rewritten = TARGET_HOST_REWRITES[https_key]
                logger.info(f"Static host rewrite: {original_host} → {rewritten} (added default port 443)")
                return rewritten

        # No rewrite found, return original
        logger.debug(f"No host rewrite configured for: {original_host}")
        return original_host

    async def handle_request(self, request: Request) -> Response:
        """Handle HTTP request with direct forwarding"""
        request_start = time.time()
        method = request.method
        path = str(request.url)
        logger.info(f"=== {method} {path} ===")

        try:
            # Parse proxy request
            if path.startswith(("http://", "https://")):
                parsed = urlparse(path)
                request_path = parsed.path or "/"
                if parsed.query:
                    request_path += "?" + parsed.query
                original_target_host = parsed.netloc
                scheme = parsed.scheme
            else:
                request_path = path
                original_target_host = request.headers.get("Host", "")
                scheme = "http"

            # Apply host rewriting
            target_host = self._rewrite_target_host(original_target_host, dict(request.headers))

            # Read request body
            body = await request.get_data()
            logger.debug(f"Request body: {len(body) if body else 0} bytes")

            # Prepare headers (exclude proxy-specific and hop-by-hop headers)
            forward_headers = {}
            excluded_headers = []
            for key, value in request.headers.items():
                key_lower = key.lower()
                if key_lower not in ("connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length", "x-target-host-rewrite", "remote-addr"):
                    forward_headers[key] = value
                else:
                    excluded_headers.append(key)

            logger.debug(f"Forwarding {len(forward_headers)} headers (excluded: {excluded_headers})")
            logger.debug(f"Headers: {forward_headers}")

            # Update Host header if host was rewritten
            if original_target_host != target_host:
                forward_headers["Host"] = original_target_host
                logger.debug(f"Host rewrite: {target_host} (preserving original Host: {original_target_host})")

            # Add accept-encoding: identity if not present (matching proxy server behavior)
            # Check case-insensitively for accept-encoding header
            has_accept_encoding = any(key.lower() == "accept-encoding" for key in forward_headers.keys())
            if not has_accept_encoding:
                forward_headers["accept-encoding"] = "identity"

            # Build target URL
            target_url = f"{scheme}://{target_host}{request_path}"
            logger.info(f"→ {target_url}")

            # Forward request using streaming with raw response handling (matching proxy server)
            timeout = httpx.Timeout(REQUEST_TIMEOUT)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    method=method,
                    url=target_url,
                    headers=forward_headers,
                    content=body,
                    follow_redirects=False,
                ) as resp:
                    forward_elapsed = time.time() - request_start
                    logger.info(f"← {resp.status_code} ({forward_elapsed:.3f}s)")
                    logger.debug(f"Response headers from target: {resp.headers.multi_items()}")

                    # Read response data exactly as received (no auto-decompression)
                    logger.debug("Reading response body from target server...")
                    response_data = b"".join([chunk async for chunk in resp.aiter_raw()])

                    total_elapsed = time.time() - request_start
                    logger.info(f"=== Request completed: {len(response_data)} bytes (total time: {total_elapsed:.3f}s) ===")

                    # Return response as-is (no header filtering)
                    return Response(response=response_data, status=resp.status_code, headers=resp.headers.multi_items())

        except Exception as exc:
            elapsed = time.time() - request_start
            logger.error(f"✗ Failed after {elapsed:.3f}s: {exc}")
            return Response(response=f"Proxy error: {exc}", status=502, headers={"Content-Type": "text/plain"})


# ─── APP SETUP ─────────────────────────────────────────────────────────────

proxy = DirectProxy()
app = Quart(__name__)


@app.before_request
async def handle_all_requests():
    """Handle all requests through direct proxy"""
    from quart import request

    return await proxy.handle_request(request)


@app.before_serving
async def startup():
    """Initialize proxy"""
    logger.info("Starting direct proxy...")
    await proxy.start()


@app.after_serving
async def shutdown():
    """Cleanup proxy"""
    logger.info("Shutting down direct proxy...")
    await proxy.stop()


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    """Main entry point"""
    print(f"Direct proxy listening on 0.0.0.0:{CLIENT_LISTEN_PORT}")
    print(f"Usage: http_proxy=http://localhost:{CLIENT_LISTEN_PORT} curl ...")

    if TARGET_HOST_REWRITES:
        print(f"Host rewrites: {len(TARGET_HOST_REWRITES)} configured")
        for orig, new in TARGET_HOST_REWRITES.items():
            print(f"  {orig} → {new}")
    else:
        print("Host rewrites: None (use x-target-host-rewrite header)")

    await app.run_task(host="0.0.0.0", port=CLIENT_LISTEN_PORT, debug=False)


def run():
    """Run the direct proxy"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDirect proxy stopped")


if __name__ == "__main__":
    run()
