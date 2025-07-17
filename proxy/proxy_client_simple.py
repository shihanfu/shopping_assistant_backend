#!/usr/bin/env python3

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx
from quart import Quart, Request, Response

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

CLIENT_LISTEN_PORT = 8081  # Different port to avoid conflicts
REQUEST_TIMEOUT = 30.0

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("simple-proxy-client")

# Target host rewrite configuration
# Maps original host:port to new host:port
TARGET_HOST_REWRITES = {
    # "metis.lti.cs.cmu.edu:7770": "10.58.210.60:80",
    # "test.com": "127.0.0.1:1234",
    # Add more rewrites as needed:
    # "api.example.com:443": "192.168.1.100:443",
    # "old-server.com": "new-server.com",
}

# ─── SIMPLE PROXY CLIENT CLASS ─────────────────────────────────────────────


class SimpleHTTPProxy:
    """Simple HTTP proxy client that forwards requests directly"""

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

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

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

    async def handle_proxy_request(self, request: Request) -> Response:
        """Handle HTTP proxy request by forwarding directly to target"""
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
                scheme = parsed.scheme
                logger.debug(f"Parsed URL - original target_host: {original_target_host}, path: {request_path}, scheme: {scheme}")
            else:
                request_path = path
                original_target_host = request.headers.get("Host", "")
                scheme = "http"  # Default to http for direct requests
                logger.debug(f"Direct request - original target_host: {original_target_host}, path: {request_path}")

            # Apply host rewriting
            target_host = self._rewrite_target_host(original_target_host, dict(request.headers))

            # Read request body
            body = await request.get_data()
            body_size = len(body) if body else 0
            logger.info(f"Request body size: {body_size} bytes")

            # Prepare headers for forwarding
            forward_headers = dict(request.headers)

            # Update Host header to target host (preserve original for target server)
            if original_target_host != target_host:
                forward_headers["Host"] = original_target_host
                logger.debug(f"Preserving original Host header: {original_target_host}")

            # Remove proxy-specific headers before forwarding
            headers_to_remove = []
            for key in forward_headers.keys():
                if key.lower() in ("x-target-host-rewrite", "proxy-connection"):
                    headers_to_remove.append(key)

            for key in headers_to_remove:
                forward_headers.pop(key)
                logger.debug(f"Removed proxy-specific header: {key}")

            # Build target URL
            target_url = f"{scheme}://{target_host}{request_path}"
            logger.info(f"Forwarding to: {method} {target_url}")

            # Make direct request to target
            start_time = time.time()
            response = await self.client.request(method=method, url=target_url, headers=forward_headers, content=body)
            elapsed = time.time() - start_time
            logger.info(f"Target responded: {response.status_code} (took {elapsed:.3f}s)")

            # Prepare response headers (remove hop-by-hop headers)
            response_headers = {}
            for k, v in response.headers.items():
                if k.lower() not in ("connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"):
                    response_headers[k] = v

            # Create response
            total_elapsed = time.time() - request_start
            logger.info(f"=== Request completed in {total_elapsed:.3f}s ===")

            return Response(response=response.content, status=response.status_code, headers=response_headers)

        except Exception as exc:
            elapsed = time.time() - request_start
            logger.error(f"=== Request failed after {elapsed:.3f}s: {exc} ===")

            # Return proper HTTP error response
            error_message = f"Proxy error: {exc}"
            return Response(response=error_message, status=502, headers={"Content-Type": "text/plain", "Connection": "close"})


# ─── GLOBAL PROXY CLIENT ───────────────────────────────────────────────────

# Global proxy client instance
proxy_client = SimpleHTTPProxy()

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
    logger.info("Starting simple proxy client...")
    await proxy_client.start()


@app.after_serving
async def shutdown():
    """Cleanup the proxy client on shutdown"""
    logger.info("Shutting down simple proxy client...")
    await proxy_client.stop()


# ─── MAIN ────────────────────────────────────────────────────────────────────


async def main():
    """Main application entry point"""
    print(f"Simple proxy client listening on 0.0.0.0:{CLIENT_LISTEN_PORT}")
    print(f"Configure your application to use http://localhost:{CLIENT_LISTEN_PORT} as HTTP proxy")

    # Show host rewrite configuration
    if TARGET_HOST_REWRITES:
        print(f"Static Host Rewrites: {len(TARGET_HOST_REWRITES)} rules configured")
        for original, rewritten in TARGET_HOST_REWRITES.items():
            print(f"  {original} → {rewritten}")
    else:
        print("Static Host Rewrites: None configured")

    print("Dynamic Host Rewrites: Enabled via 'x-target-host-rewrite' header")
    print("  Header format: 'original_host=new_host' or 'original_host:port=new_host:port'")

    # Start the server
    await app.run_task(host="0.0.0.0", port=CLIENT_LISTEN_PORT, debug=False)


def run():
    """Run the simple proxy client"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimple proxy client stopped")


if __name__ == "__main__":
    run()
