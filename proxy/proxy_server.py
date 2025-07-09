#!/usr/bin/env python3
"""
HTTP Proxy Server
Receives chunks from the proxy client, assembles them into complete requests,
forwards them to target servers, and streams responses back in chunks.
"""

import http.server
import json
import logging
import socketserver
import sys
import threading
import time

import requests

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ProxyServer")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SERVER_LISTEN_PORT = 9090
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for responses

# ─── CONNECTION STORAGE ──────────────────────────────────────────────────────

# Store active connections and their data
connections = {}
connection_lock = threading.Lock()


class Connection:
    def __init__(self, connection_id, target_host, method, path, headers, body_size):
        self.connection_id = connection_id
        self.target_host = target_host
        self.method = method
        self.path = path
        self.headers = headers
        self.body_size = body_size
        self.body_chunks = []
        self.body_complete = False
        self.response_data = None
        self.response_headers = None
        self.response_status = None
        self.response_complete = False
        self.created_at = time.time()


# ─── PROXY SERVER HANDLER ────────────────────────────────────────────────────


class ProxyServerHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        if content_length:
            return self.rfile.read(int(content_length))
        return None

    def _handle_request(self):
        """Route requests based on path"""
        logger.debug(f"Server received {self.command} {self.path}")
        logger.debug(f"Request headers: {dict(self.headers)}")

        if self.path == "/proxy/connection":
            self._handle_connection_create()
        elif self.path == "/proxy/chunk":
            self._handle_chunk_receive()
        elif self.path == "/proxy/response":
            self._handle_response_request()
        else:
            logger.warning(f"Unknown path: {self.path}")
            self.send_error(404, "Not found")

    def _handle_connection_create(self):
        """Handle connection creation request"""
        logger.info("=== Creating new connection ===")
        try:
            body = self._read_body()
            if not body:
                logger.error("No body provided for connection creation")
                self.send_error(400, "No body provided")
                return

            metadata = json.loads(body.decode("utf-8"))
            connection_id = metadata["connection_id"]
            target_host = metadata["target_host"]
            method = metadata["method"]
            path = metadata["path"]
            headers = metadata["headers"]
            body_size = metadata["body_size"]

            logger.info(f"Creating connection {connection_id}: {method} {target_host}{path}")
            logger.debug(f"Connection metadata: {metadata}")

            # Create new connection
            with connection_lock:
                connections[connection_id] = Connection(connection_id, target_host, method, path, headers, body_size)
                logger.info(f"Connection {connection_id} created. Total connections: {len(connections)}")

            # Send success response
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"status": "created", "connection_id": connection_id}
            self.wfile.write(json.dumps(response).encode())
            logger.debug(f"Sent connection creation response: {response}")

        except Exception as exc:
            logger.error(f"Connection creation error: {exc}")
            self.send_error(500, f"Connection creation error: {exc}")
            sys.stderr.write(f"Connection creation error: {exc}\n")

    def _handle_chunk_receive(self):
        """Handle chunk reception"""
        try:
            connection_id = self.headers.get("X-Connection-ID")
            is_final = self.headers.get("X-Chunk-Final", "false") == "true"

            logger.debug(f"Receiving chunk for connection {connection_id}, final={is_final}")

            if not connection_id:
                logger.error("No connection ID provided in chunk request")
                self.send_error(400, "No connection ID provided")
                return

            body = self._read_body()
            if body is None:
                body = b""

            logger.debug(f"Received chunk: {len(body)} bytes")

            # Store chunk
            with connection_lock:
                if connection_id not in connections:
                    logger.error(f"Connection {connection_id} not found")
                    self.send_error(404, "Connection not found")
                    return

                conn = connections[connection_id]
                conn.body_chunks.append(body)
                total_received = sum(len(chunk) for chunk in conn.body_chunks)
                logger.debug(f"Stored chunk {len(conn.body_chunks)} for connection {connection_id}: {total_received}/{conn.body_size} bytes")

                if is_final:
                    conn.body_complete = True
                    logger.info(f"Connection {connection_id} body complete: {total_received} bytes in {len(conn.body_chunks)} chunks")

                    # Assemble complete request and forward to target
                    self._forward_request(conn)

            # Send success response
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"status": "chunk_received", "is_final": is_final}
            self.wfile.write(json.dumps(response).encode())
            logger.debug(f"Sent chunk reception response: {response}")

        except Exception as exc:
            logger.error(f"Chunk reception error: {exc}")
            self.send_error(500, f"Chunk reception error: {exc}")
            sys.stderr.write(f"Chunk reception error: {exc}\n")

    def _forward_request(self, conn):
        """Forward assembled request to target server"""
        logger.info(f"=== Forwarding request for connection {conn.connection_id} ===")
        request_start = time.time()

        try:
            # Assemble complete body
            complete_body = b"".join(conn.body_chunks)
            logger.info(f"Assembled request body: {len(complete_body)} bytes from {len(conn.body_chunks)} chunks")

            # Build target URL
            if not conn.path.startswith("http"):
                target_url = f"http://{conn.target_host}{conn.path}"
            else:
                target_url = conn.path

            logger.info(f"Target URL: {conn.method} {target_url}")

            # Prepare headers (exclude proxy-specific headers)
            headers = {}
            excluded_headers = []
            for k, v in conn.headers.items():
                k_lower = k.lower()
                if k_lower not in ("host", "connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length"):
                    headers[k] = v
                else:
                    excluded_headers.append(k)

            # Add host header
            headers["Host"] = conn.target_host

            logger.debug(f"Forwarding {len(headers)} headers (excluded: {excluded_headers})")
            logger.debug(f"Headers: {headers}")

            # Forward request to target server
            logger.info("Sending request to target server...")
            forward_start = time.time()
            resp = requests.request(
                method=conn.method,
                url=target_url,
                headers=headers,
                data=complete_body,
                stream=True,
                allow_redirects=False,
                timeout=30,
            )
            forward_elapsed = time.time() - forward_start

            logger.info(f"Target server responded: {resp.status_code} (took {forward_elapsed:.3f}s)")

            # Store response
            conn.response_status = resp.status_code
            conn.response_headers = dict(resp.headers)
            logger.debug(f"Response headers from target: {conn.response_headers}")

            # Read response data
            logger.info("Reading response body from target server...")
            response_data = b""
            chunk_count = 0
            for chunk in resp.iter_content(8192):
                if chunk:
                    response_data += chunk
                    chunk_count += 1

            conn.response_data = response_data
            conn.response_complete = True

            total_elapsed = time.time() - request_start
            logger.info(f"=== Request forwarding completed: {len(response_data)} bytes in {chunk_count} chunks (total time: {total_elapsed:.3f}s) ===")

        except Exception as exc:
            total_elapsed = time.time() - request_start
            logger.error(f"=== Forward request failed after {total_elapsed:.3f}s: {exc} ===")

            # Store error response
            conn.response_status = 502
            conn.response_headers = {"Content-Type": "text/plain"}
            conn.response_data = f"Proxy error: {exc}".encode()
            conn.response_complete = True
            sys.stderr.write(f"Forward request error: {exc}\n")

    def _handle_response_request(self):
        """Handle response chunk request"""
        try:
            connection_id = self.headers.get("X-Connection-ID")
            chunk_index = int(self.headers.get("X-Chunk-Index", "0"))

            logger.debug(f"Response request for connection {connection_id}, chunk {chunk_index}")

            if not connection_id:
                logger.error("No connection ID provided in response request")
                self.send_error(400, "No connection ID provided")
                return

            with connection_lock:
                if connection_id not in connections:
                    logger.error(f"Connection {connection_id} not found for response request")
                    self.send_error(404, "Connection not found")
                    return

                conn = connections[connection_id]

                if not conn.response_complete:
                    logger.debug(f"Response not ready for connection {connection_id}")
                    self.send_error(202, "Response not ready")
                    return

                if chunk_index == 0:
                    # First chunk: return metadata only
                    body_size = len(conn.response_data) if conn.response_data else 0
                    metadata = {"status": conn.response_status, "headers": conn.response_headers, "body_size": body_size, "has_body": bool(conn.response_data)}

                    logger.info(f"Sending response metadata for connection {connection_id}: status={conn.response_status}, body_size={body_size}")
                    logger.debug(f"Response metadata: {metadata}")

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()

                    # Send metadata as JSON
                    self.wfile.write(json.dumps(metadata).encode())
                else:
                    # Subsequent chunks: return response body data
                    # Adjust chunk_index since body chunks start from index 1
                    body_chunk_index = chunk_index - 1

                    if conn.response_data:
                        start_pos = body_chunk_index * CHUNK_SIZE
                        end_pos = start_pos + CHUNK_SIZE
                        chunk_data = conn.response_data[start_pos:end_pos]
                        has_more = end_pos < len(conn.response_data)

                        logger.debug(f"Sending response chunk {chunk_index} for connection {connection_id}: {len(chunk_data)} bytes, has_more={has_more}")
                    else:
                        chunk_data = b""
                        has_more = False
                        logger.debug(f"No response data for connection {connection_id}, sending empty chunk")

                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("X-More-Chunks", "true" if has_more else "false")
                    self.end_headers()

                    # Send chunk data
                    self.wfile.write(chunk_data)

                    # Clean up connection if no more chunks
                    if not has_more:
                        logger.info(f"Response complete for connection {connection_id}, scheduling cleanup")
                        # Keep connection for a short time in case client needs to retry
                        threading.Timer(30.0, self._cleanup_connection, args=[connection_id]).start()

        except Exception as exc:
            logger.error(f"Response request error: {exc}")
            self.send_error(500, f"Response request error: {exc}")
            sys.stderr.write(f"Response request error: {exc}\n")

    def _cleanup_connection(self, connection_id):
        """Clean up connection after timeout"""
        with connection_lock:
            if connection_id in connections:
                logger.info(f"Cleaning up connection {connection_id}")
                del connections[connection_id]
                logger.debug(f"Remaining connections: {len(connections)}")

    def log_message(self, format, *args):
        """Override to reduce logging noise"""
        pass


# ─── CLEANUP THREAD ──────────────────────────────────────────────────────────


def cleanup_old_connections():
    """Periodically clean up old connections"""
    logger.info("Starting connection cleanup thread")
    while True:
        time.sleep(60)  # Run every minute
        current_time = time.time()
        with connection_lock:
            to_remove = []
            for conn_id, conn in connections.items():
                if current_time - conn.created_at > 300:  # 5 minutes
                    to_remove.append(conn_id)

            if to_remove:
                logger.info(f"Cleaning up {len(to_remove)} old connections: {to_remove}")
                for conn_id in to_remove:
                    del connections[conn_id]
                logger.debug(f"Remaining connections after cleanup: {len(connections)}")
            else:
                logger.debug(f"No connections to clean up. Active connections: {len(connections)}")


# ─── MAIN ────────────────────────────────────────────────────────────────────


def run():
    logger.info("=== Starting Proxy Server ===")
    logger.info(f"Server will listen on port {SERVER_LISTEN_PORT}")
    logger.info(f"Chunk size: {CHUNK_SIZE} bytes")

    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_connections, daemon=True)
    cleanup_thread.start()

    with socketserver.ThreadingTCPServer(("", SERVER_LISTEN_PORT), ProxyServerHandler) as srv:
        logger.info(f"Proxy server listening on 0.0.0.0:{SERVER_LISTEN_PORT}")
        logger.info("This server should be accessible through the AWS SigV4 proxy")
        logger.info("=== Server ready to accept connections ===")
        print(f"Proxy server listening on 0.0.0.0:{SERVER_LISTEN_PORT}")
        print("This server should be accessible through the AWS SigV4 proxy")
        srv.serve_forever()


if __name__ == "__main__":
    run()
