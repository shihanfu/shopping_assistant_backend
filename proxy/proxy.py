#!/usr/bin/env python3
"""
aws_sigv4_proxy.py
A minimal HTTP proxy that:
  • accepts proxy-style requests from curl / browsers
  • signs them with Sig-V4
  • forwards them to an API Gateway HTTP/REST endpoint
Only supports plain-HTTP (no CONNECT / HTTPS tunnelling).
"""

import http.server
import socketserver
import sys
from urllib.parse import urlparse

import requests
from botocore.session import Session
from requests_aws4auth import AWS4Auth

# ─── CONFIG ──────────────────────────────────────────────────────────────────

API_GATEWAY_URL = "https://3he3rx88gl.execute-api.us-east-1.amazonaws.com"
AWS_REGION = "us-east-1"
SERVICE = "execute-api"
LISTEN_PORT = 8888

# ─── DYNAMIC AWS CREDENTIALS ─────────────────────────────────────────────────

session = Session()
credentials = session.get_credentials()
aws_auth = AWS4Auth(
    refreshable_credentials=credentials,
    region=AWS_REGION,
    service=SERVICE,
)

GATEWAY_HOST = urlparse(API_GATEWAY_URL).netloc  # 3he3rx88gl.execute-api.us-east-1.amazonaws.com

# ─── PROXY HANDLER ───────────────────────────────────────────────────────────


class SigV4ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Map all HTTP verbs to one function
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
        import io

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
                import io

                body = io.BytesIO()
                remaining = content_len
                max_chunk = 1024 * 1024  # 1MB chunks
                while remaining > 0:
                    chunk_size = min(remaining, max_chunk)
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
        # 1. Parse absolute-form proxy request line
        if self.path.startswith(("http://", "https://")):
            parsed = urlparse(self.path)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            orig_host = parsed.netloc
        else:
            # Shouldn’t happen with curl -x, but keep it safe
            path = self.path
            orig_host = self.headers.get("Host", "")

        # 2. Read body with chunking support
        body = self._read_body()

        # 3. Build headers to *sign*
        signed_headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade")}
        #   Put the *Gateway* host in Host (must match URL we sign)
        signed_headers["Host"] = GATEWAY_HOST
        #   Send the real target host in a separate header for remote proxy
        signed_headers["X-Original-Host"] = orig_host

        # 4. Forward to Gateway (origin-form URL)
        gw_url = API_GATEWAY_URL + path
        try:
            resp = requests.request(
                method=self.command,
                url=gw_url,
                headers=signed_headers,
                data=body,
                auth=aws_auth,
                stream=True,
                allow_redirects=False,
                timeout=30,
            )

            # 5. Relay response to client
            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() in ("transfer-encoding",):
                    continue  # avoid chunked↔content-length clash
                self.send_header(k, v)
            self.end_headers()

            for chunk in resp.iter_content(8192):
                if chunk:
                    self.wfile.write(chunk)

        except Exception as exc:
            self.send_error(502, f"Proxy error: {exc}")
            sys.stderr.write(f"Proxy error: {exc}\n")


# ─── MAIN ────────────────────────────────────────────────────────────────────


def run():
    with socketserver.ThreadingTCPServer(("", LISTEN_PORT), SigV4ProxyHandler) as srv:
        print(f"Sig-V4 proxy listening on 0.0.0.0:{LISTEN_PORT}")
        srv.serve_forever()


if __name__ == "__main__":
    run()
