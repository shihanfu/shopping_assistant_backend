#!/usr/bin/env python3
"""
HTTP Proxy Client
Accepts HTTP proxy requests and splits large requests into chunks for transmission
through the AWS SigV4 proxy to the proxy server.
"""

import http.server
import logging
import socketserver
import sys
import time
import uuid
from io import BytesIO
from urllib.parse import urlparse

import requests
from botocore.session import Session
from requests_aws4auth import AWS4Auth

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ProxyClient")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

# AWS SigV4 Proxy configuration (same as proxy.py)
# API_GATEWAY_URL = "https://3he3rx88gl.execute-api.us-east-1.amazonaws.com"
API_GATEWAY_URL = "http://localhost:9090"
AWS_REGION = "us-east-1"
SERVICE = "execute-api"

# Proxy client configuration
CLIENT_LISTEN_PORT = 8082
CHUNK_SIZE = 1024 * 1024  # 1MB chunks (under 2MB limit)

# ─── AWS AUTHENTICATION ──────────────────────────────────────────────────────

session = Session()
credentials = session.get_credentials()
aws_auth = AWS4Auth(
    refreshable_credentials=credentials,
    region=AWS_REGION,
    service=SERVICE,
)

# ─── PROXY CLIENT HANDLER ────────────────────────────────────────────────────


class ProxyClientHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        self.connection_id = None
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def do_DELETE(self):
        self._handle_request()

    def do_HEAD(self):
        self._handle_request()

    def do_PATCH(self):
        self._handle_request()

    def do_OPTIONS(self):
        self._handle_request()

    def _read_body(self):
        """Read request body"""
        content_length = self.headers.get("Content-Length")
        logger.debug(f"Reading body, Content-Length: {content_length}")
        if content_length:
            body = self.rfile.read(int(content_length))
            logger.debug(f"Read {len(body)} bytes from request body")
            return body
        logger.debug("No Content-Length header, returning None")
        return None

    def _create_connection(self, target_host, method, path, headers, body_size):
        """Create a new connection on the proxy server"""
        connection_id = str(uuid.uuid4())
        logger.info(f"Creating connection {connection_id} for {method} {target_host}{path}")

        metadata = {"connection_id": connection_id, "target_host": target_host, "method": method, "path": path, "headers": dict(headers), "body_size": body_size}
        logger.debug(f"Connection metadata: {metadata}")

        # Send connection creation request to proxy server
        logger.debug(f"Sending connection request to {API_GATEWAY_URL}/proxy/connection")
        start_time = time.time()
        resp = requests.post(f"{API_GATEWAY_URL}/proxy/connection", json=metadata, auth=aws_auth, headers={"Content-Type": "application/json"})
        elapsed = time.time() - start_time

        logger.debug(f"Connection creation response: {resp.status_code} (took {elapsed:.3f}s)")
        if resp.status_code == 200:
            logger.info(f"Successfully created connection {connection_id}")
            return connection_id
        else:
            logger.error(f"Failed to create connection: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to create connection: {resp.status_code}")

    def _send_chunk(self, connection_id, chunk_data, is_final=False):
        """Send a chunk of data to the proxy server"""
        logger.debug(f"Sending chunk for connection {connection_id}: {len(chunk_data)} bytes, final={is_final}")

        headers = {"Content-Type": "application/octet-stream", "X-Connection-ID": connection_id, "X-Chunk-Final": "true" if is_final else "false"}

        start_time = time.time()
        resp = requests.post(f"{API_GATEWAY_URL}/proxy/chunk", data=chunk_data, auth=aws_auth, headers=headers)
        elapsed = time.time() - start_time

        logger.debug(f"Chunk send response: {resp.status_code} (took {elapsed:.3f}s)")
        if resp.status_code != 200:
            logger.error(f"Failed to send chunk: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to send chunk: {resp.status_code}")

        result = resp.json()
        logger.debug(f"Chunk send result: {result}")
        return result

    def _get_response_metadata(self, connection_id):
        """Get response metadata from the proxy server"""
        logger.debug(f"Getting response metadata for connection {connection_id}")
        headers = {"X-Connection-ID": connection_id, "X-Chunk-Index": "0"}

        start_time = time.time()
        resp = requests.get(f"{API_GATEWAY_URL}/proxy/response", auth=aws_auth, headers=headers)
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

    def _get_response_chunk(self, connection_id, chunk_index):
        """Get a chunk of response body data from the proxy server"""
        logger.debug(f"Getting response chunk {chunk_index} for connection {connection_id}")
        headers = {"X-Connection-ID": connection_id, "X-Chunk-Index": str(chunk_index)}

        start_time = time.time()
        resp = requests.get(f"{API_GATEWAY_URL}/proxy/response", auth=aws_auth, headers=headers)
        elapsed = time.time() - start_time

        if resp.status_code == 200:
            has_more = resp.headers.get("X-More-Chunks", "false") == "true"
            logger.debug(f"Got response chunk {chunk_index}: {len(resp.content)} bytes, has_more={has_more} (took {elapsed:.3f}s)")
            return resp.content, has_more
        else:
            logger.error(f"Failed to get response chunk {chunk_index}: {resp.status_code} - {resp.text}")
            raise Exception(f"Failed to get response chunk: {resp.status_code}")

    def _handle_request(self):
        """Handle HTTP proxy request by splitting into chunks if needed"""
        request_start = time.time()
        logger.info(f"=== New {self.command} request: {self.path} ===")
        logger.debug(f"Request headers: {dict(self.headers)}")

        try:
            # Parse proxy request
            if self.path.startswith(("http://", "https://")):
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                target_host = parsed.netloc
                logger.debug(f"Parsed URL - target_host: {target_host}, path: {path}")
            else:
                path = self.path
                target_host = self.headers.get("Host", "")
                logger.debug(f"Direct request - target_host: {target_host}, path: {path}")

            # Read request body
            body = self._read_body()
            body_size = len(body) if body else 0
            logger.info(f"Request body size: {body_size} bytes")

            # Handle all requests the same way (always use chunking)
            self._handle_request_unified(target_host, path, body)

            elapsed = time.time() - request_start
            logger.info(f"=== Request completed in {elapsed:.3f}s ===")

        except Exception as exc:
            elapsed = time.time() - request_start
            logger.error(f"=== Request failed after {elapsed:.3f}s: {exc} ===")

            # Send a proper HTTP error response
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Connection", "close")
                self.end_headers()
                error_message = f"Proxy client error: {exc}"
                self.wfile.write(error_message.encode("utf-8"))
                logger.debug(f"Sent error response: {error_message}")
            except Exception as send_exc:
                logger.error(f"Failed to send error response: {send_exc}")

            sys.stderr.write(f"Proxy client error: {exc}\n")

    def _handle_request_unified(self, target_host, path, body):
        """Handle all requests with unified chunking approach"""
        logger.info(f"Processing unified request for {target_host}{path}")

        try:
            # Create connection
            connection_id = self._create_connection(target_host, self.command, path, self.headers, len(body) if body else 0)
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
                    self._send_chunk(connection_id, chunk_data, is_final=is_final)
            else:
                logger.info("No request body, sending empty final chunk")
                chunk_count = 1
                self._send_chunk(connection_id, b"", is_final=True)

            logger.info(f"Sent {chunk_count} chunks total")
        except Exception as exc:
            logger.error(f"Failed to send request chunks: {exc}")
            raise Exception(f"Request transmission failed: {exc}")

        try:
            # Get response metadata first
            status_code, response_headers, has_body = self._get_response_metadata(connection_id)
            logger.info(f"Got response metadata: status={status_code}, has_body={has_body}")
        except Exception as exc:
            logger.error(f"Failed to get response metadata: {exc}")
            # Send a 502 response since we couldn't get the real response
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Connection", "close")
            self.end_headers()
            error_msg = f"Failed to get response from proxy server: {exc}"
            self.wfile.write(error_msg.encode("utf-8"))
            return

        try:
            # Send response headers (relay original headers from target server)
            logger.info(f"Sending response: {status_code}")
            logger.debug(f"Relaying {len(response_headers)} response headers")
            self.send_response(status_code)
            for k, v in response_headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "x-more-chunks", "x-chunk-index"):
                    self.send_header(k, v)
            self.end_headers()

            # Get and send response body if it exists
            if has_body:
                logger.info("Getting response body chunks")
                chunk_index = 1  # Body chunks start from index 1
                response_bytes_sent = 0

                while True:
                    response_data, has_more = self._get_response_chunk(connection_id, chunk_index)
                    self.wfile.write(response_data)
                    response_bytes_sent += len(response_data)
                    logger.debug(f"Sent response chunk {chunk_index}: {len(response_data)} bytes (total: {response_bytes_sent})")

                    if not has_more:
                        break
                    chunk_index += 1

                logger.info(f"Completed response body: {response_bytes_sent} bytes in {chunk_index} chunks")
            else:
                logger.info("No response body to send")
        except Exception as exc:
            logger.error(f"Failed to send response: {exc}")
            # At this point we've already sent headers, so we can't send a proper error response
            # Just log the error and close the connection
            raise Exception(f"Response transmission failed: {exc}")


# ─── MAIN ────────────────────────────────────────────────────────────────────


def run():
    with socketserver.ThreadingTCPServer(("", CLIENT_LISTEN_PORT), ProxyClientHandler) as srv:
        print(f"Proxy client listening on 0.0.0.0:{CLIENT_LISTEN_PORT}")
        print(f"Configure your application to use http://localhost:{CLIENT_LISTEN_PORT} as HTTP proxy")
        srv.serve_forever()


if __name__ == "__main__":
    run()
