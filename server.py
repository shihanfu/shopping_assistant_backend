#!/usr/bin/env python3
"""
remote_proxy_server.py
A remote HTTP proxy server that:
  • receives requests from the SigV4 proxy
  • extracts the original host from X-Original-Host header
  • forwards requests to the actual target server
  • supports chunked transfers for large requests/responses (>2MB)
"""

import http.server
import io
import socketserver
import sys

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─── CONFIG ──────────────────────────────────────────────────────────────────

LISTEN_PORT = 8080
CHUNK_SIZE = 8192  # 8KB chunks
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB max chunk size for large transfers

# ─── RETRY CONFIGURATION ─────────────────────────────────────────────────────

retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
)

adapter = HTTPAdapter(max_retries=retry_strategy)
session = requests.Session()
session.mount("http://", adapter)
session.mount("https://", adapter)

# ─── REMOTE PROXY HANDLER ────────────────────────────────────────────────────


class RemoteProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_HEAD(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def _read_chunked_body(self):
        """Read chunked transfer-encoded body"""
        body = io.BytesIO()
        while True:
            # Read chunk size line
            chunk_size_line = self.rfile.readline().decode("ascii").strip()
            if not chunk_size_line:
                break

            # Parse chunk size (hex)
            try:
                chunk_size = int(chunk_size_line.split(";")[0], 16)
            except ValueError:
                break

            if chunk_size == 0:
                # End of chunks
                break

            # Read chunk data
            chunk_data = self.rfile.read(chunk_size)
            body.write(chunk_data)

            # Read CRLF after chunk
            self.rfile.readline()

        return body.getvalue()

    def _read_body(self):
        """Read request body with support for different transfer encodings"""
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        content_length = self.headers.get("Content-Length")

        if "chunked" in transfer_encoding:
            return self._read_chunked_body()
        elif content_length:
            content_len = int(content_length)
            if content_len > 2 * 1024 * 1024:  # > 2MB
                # For large bodies, read in chunks
                body = io.BytesIO()
                remaining = content_len
                while remaining > 0:
                    chunk_size = min(remaining, MAX_CHUNK_SIZE)
                    chunk = self.rfile.read(chunk_size)
                    if not chunk:
                        break
                    body.write(chunk)
                    remaining -= len(chunk)
                return body.getvalue()
            else:
                return self.rfile.read(content_len)
        else:
            return None

    def _proxy(self):
        try:
            # 1. Extract original host from X-Original-Host header
            original_host = self.headers.get("X-Original-Host")
            if not original_host:
                self.send_error(400, "Missing X-Original-Host header")
                return

            # 2. Build target URL
            if self.path.startswith(("http://", "https://")):
                # Absolute URL (shouldn't happen with our setup)
                target_url = self.path
            else:
                # Relative path - construct URL with original host
                scheme = "https" if original_host.endswith(":443") else "http"
                if ":" in original_host and not original_host.endswith(":80") and not original_host.endswith(":443"):
                    target_url = f"{scheme}://{original_host}{self.path}"
                else:
                    # Remove port if it's standard
                    host = original_host.replace(":80", "").replace(":443", "")
                    target_url = f"{scheme}://{host}{self.path}"

            # 3. Read request body with chunking support
            body = self._read_body()

            # 4. Prepare headers for forwarding
            headers = {}
            for k, v in self.headers.items():
                k_lower = k.lower()
                # Filter out proxy-specific headers
                if k_lower not in ("host", "x-original-host", "connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"):
                    headers[k] = v

            # 5. Forward request to target server
            try:
                resp = session.request(
                    method=self.command,
                    url=target_url,
                    headers=headers,
                    data=body,
                    stream=True,
                    allow_redirects=False,
                    timeout=60,  # Increased timeout for large transfers
                )

                # 6. Send response headers
                self.send_response(resp.status_code)

                # Forward response headers
                for k, v in resp.headers.items():
                    k_lower = k.lower()
                    if k_lower not in ("transfer-encoding", "connection", "keep-alive"):
                        self.send_header(k, v)

                self.end_headers()

                # 7. Stream response body with chunking support
                content_length = resp.headers.get("Content-Length")
                transfer_encoding = resp.headers.get("Transfer-Encoding", "").lower()

                if content_length:
                    content_len = int(content_length)
                    if content_len > 2 * 1024 * 1024:  # > 2MB
                        # For large responses, use chunked transfer encoding
                        self.wfile.write(b"Transfer-Encoding: chunked\r\n\r\n")
                        remaining = content_len
                        while remaining > 0:
                            chunk_size = min(remaining, CHUNK_SIZE)
                            chunk = resp.raw.read(chunk_size)
                            if not chunk:
                                break
                            # Send chunk in chunked format
                            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                            self.wfile.write(chunk)
                            self.wfile.write(b"\r\n")
                            remaining -= len(chunk)
                        # End of chunks
                        self.wfile.write(b"0\r\n\r\n")
                    else:
                        # Small response, stream normally
                        for chunk in resp.iter_content(CHUNK_SIZE):
                            if chunk:
                                self.wfile.write(chunk)
                elif "chunked" in transfer_encoding:
                    # Forward chunked response as-is
                    for chunk in resp.iter_content(CHUNK_SIZE):
                        if chunk:
                            self.wfile.write(chunk)
                else:
                    # Unknown encoding, stream normally
                    for chunk in resp.iter_content(CHUNK_SIZE):
                        if chunk:
                            self.wfile.write(chunk)

            except requests.exceptions.RequestException as e:
                self.send_error(502, f"Target server error: {e}")
                sys.stderr.write(f"Target server error: {e}\n")

        except Exception as e:
            self.send_error(500, f"Proxy error: {e}")
            sys.stderr.write(f"Proxy error: {e}\n")

    def log_message(self, format, *args):
        """Custom logging to include client IP"""
        client_ip = self.client_address[0]
        sys.stderr.write(f"[{client_ip}] {format % args}\n")


# ─── MAIN ────────────────────────────────────────────────────────────────────


def run():
    with socketserver.ThreadingTCPServer(("", LISTEN_PORT), RemoteProxyHandler) as srv:
        print(f"Remote proxy server listening on 0.0.0.0:{LISTEN_PORT}")
        print("This server expects requests with X-Original-Host header")
        srv.serve_forever()


if __name__ == "__main__":
    run()
