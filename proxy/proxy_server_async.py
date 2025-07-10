#!/usr/bin/env python3
"""
HTTP Proxy Server (Async)
Receives chunks from the proxy client, assembles them into complete requests,
forwards them to target servers, and streams responses back in chunks.
Uses Quart (async Flask) for better performance.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

# import aiohttp
import httpx
from quart import Quart, Response, jsonify, request

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ProxyServerAsync")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SERVER_LISTEN_PORT = 9090
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for responses

# ─── CONNECTION STORAGE ──────────────────────────────────────────────────────


@dataclass
class Connection:
    connection_id: str
    target_host: str
    method: str
    path: str
    headers: dict[str, str]
    body_size: int
    body_chunks: list[bytes] = field(default_factory=list)
    body_complete: bool = False
    response_data: bytes | None = None
    response_headers: dict[str, str] | None = None
    response_status: int | None = None
    response_complete: bool = False
    created_at: float = field(default_factory=time.time)


# Store active connections and their data
connections: dict[str, Connection] = {}
connections_lock = asyncio.Lock()

# ─── FLASK APP ───────────────────────────────────────────────────────────────

app = Quart(__name__)


@app.before_serving
async def startup():
    """Initialize the application"""
    logger.info("=== Starting Async Proxy Server ===")
    logger.info(f"Server will listen on port {SERVER_LISTEN_PORT}")
    logger.info(f"Chunk size: {CHUNK_SIZE} bytes")

    # Start cleanup task
    asyncio.create_task(cleanup_old_connections())

    logger.info("=== Server ready to accept connections ===")


@app.route("/proxy/connection", methods=["POST"])
async def handle_connection_create():
    """Handle connection creation request"""
    logger.info("=== Creating new connection ===")

    try:
        body = await request.get_data()
        if not body:
            logger.error("No body provided for connection creation")
            return jsonify({"error": "No body provided"}), 400

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
        async with connections_lock:
            connections[connection_id] = Connection(connection_id=connection_id, target_host=target_host, method=method, path=path, headers=headers, body_size=body_size)
            logger.info(f"Connection {connection_id} created. Total connections: {len(connections)}")

        response = {"status": "created", "connection_id": connection_id}
        logger.debug(f"Sent connection creation response: {response}")
        return jsonify(response)

    except Exception as exc:
        logger.error(f"Connection creation error: {exc}")
        return jsonify({"error": f"Connection creation error: {exc}"}), 500


@app.route("/proxy/chunk", methods=["POST"])
async def handle_chunk_receive():
    """Handle chunk reception"""
    try:
        connection_id = request.headers.get("X-Connection-ID")
        is_final = request.headers.get("X-Chunk-Final", "false") == "true"

        logger.debug(f"Receiving chunk for connection {connection_id}, final={is_final}")

        if not connection_id:
            logger.error("No connection ID provided in chunk request")
            return jsonify({"error": "No connection ID provided"}), 400

        body = await request.get_data()
        if body is None:
            body = b""

        logger.debug(f"Received chunk: {len(body)} bytes")

        # Store chunk
        async with connections_lock:
            if connection_id not in connections:
                logger.error(f"Connection {connection_id} not found")
                return jsonify({"error": "Connection not found"}), 404

            conn = connections[connection_id]
            conn.body_chunks.append(body)
            total_received = sum(len(chunk) for chunk in conn.body_chunks)
            logger.debug(f"Stored chunk {len(conn.body_chunks)} for connection {connection_id}: {total_received}/{conn.body_size} bytes")

            if is_final:
                conn.body_complete = True
                logger.info(f"Connection {connection_id} body complete: {total_received} bytes in {len(conn.body_chunks)} chunks")

                # Assemble complete request and forward to target
                await forward_request(conn)

        response = {"status": "chunk_received", "is_final": is_final}
        logger.debug(f"Sent chunk reception response: {response}")
        return jsonify(response)

    except Exception as exc:
        logger.error(f"Chunk reception error: {exc}")
        return jsonify({"error": f"Chunk reception error: {exc}"}), 500


@app.route("/proxy/response", methods=["GET"])
async def handle_response_request():
    """Handle response chunk request"""
    try:
        connection_id = request.headers.get("X-Connection-ID")
        chunk_index = int(request.headers.get("X-Chunk-Index", "0"))

        logger.debug(f"Response request for connection {connection_id}, chunk {chunk_index}")

        if not connection_id:
            logger.error("No connection ID provided in response request")
            return jsonify({"error": "No connection ID provided"}), 400

        async with connections_lock:
            if connection_id not in connections:
                logger.error(f"Connection {connection_id} not found for response request")
                return jsonify({"error": "Connection not found"}), 404

            conn = connections[connection_id]

            if not conn.response_complete:
                logger.debug(f"Response not ready for connection {connection_id}")
                return jsonify({"error": "Response not ready"}), 202

            if chunk_index == 0:
                # First chunk: return metadata only
                body_size = len(conn.response_data) if conn.response_data else 0
                metadata = {"status": conn.response_status, "headers": conn.response_headers, "body_size": body_size, "has_body": bool(conn.response_data)}

                logger.info(f"Sending response metadata for connection {connection_id}: status={conn.response_status}, body_size={body_size}")
                logger.debug(f"Response metadata: {metadata}")

                return jsonify(metadata)
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

                headers = {"Content-Type": "application/octet-stream", "X-More-Chunks": "true" if has_more else "false"}

                # Clean up connection if no more chunks
                if not has_more:
                    logger.info(f"Response complete for connection {connection_id}, scheduling cleanup")
                    # Schedule cleanup after a delay
                    asyncio.create_task(cleanup_connection_delayed(connection_id, 30.0))

                return Response(chunk_data, headers=headers)

    except Exception as exc:
        logger.error(f"Response request error: {exc}")
        return jsonify({"error": f"Response request error: {exc}"}), 500


# ─── ASYNC HELPER FUNCTIONS ──────────────────────────────────────────────────


async def forward_request(conn: Connection):
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
            if k_lower not in ("connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length"):
                headers[k] = v
            else:
                excluded_headers.append(k)

        logger.debug(f"Forwarding {len(headers)} headers (excluded: {excluded_headers})")
        logger.debug(f"Headers: {headers}")
        if "accept-encoding" not in headers:
            headers["accept-encoding"] = "identity"

        # Forward request to target server using httpx (raw bytes passthrough)
        logger.info("Sending request to target server...")
        forward_start = time.time()

        timeout = httpx.Timeout(30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                method=conn.method,
                url=target_url,
                headers=headers,
                content=complete_body,
                follow_redirects=False,
            ) as resp:
                forward_elapsed = time.time() - forward_start
                logger.info(f"Target server responded: {resp.status_code} (took {forward_elapsed:.3f}s)")

                # Store response status and headers
                conn.response_status = resp.status_code
                conn.response_headers = dict(resp.headers)
                logger.debug(f"Response headers from target: {conn.response_headers}")

                # Read response data exactly as received (no auto-decompression)
                logger.info("Reading response body from target server...")
                response_data = b"".join([chunk async for chunk in resp.aiter_raw()])

                conn.response_data = response_data
                conn.response_complete = True

                total_elapsed = time.time() - request_start
                logger.info(f"=== Request forwarding completed: {len(response_data)} bytes (total time: {total_elapsed:.3f}s) ===")

    except Exception as exc:
        total_elapsed = time.time() - request_start
        logger.error(f"=== Forward request failed after {total_elapsed:.3f}s: {exc} ===")

        # Store error response
        conn.response_status = 502
        conn.response_headers = {"Content-Type": "text/plain"}
        conn.response_data = f"Proxy error: {exc}".encode()
        conn.response_complete = True


async def cleanup_connection_delayed(connection_id: str, delay: float):
    """Clean up connection after delay"""
    await asyncio.sleep(delay)
    async with connections_lock:
        if connection_id in connections:
            logger.info(f"Cleaning up connection {connection_id}")
            del connections[connection_id]
            logger.debug(f"Remaining connections: {len(connections)}")


async def cleanup_old_connections():
    """Periodically clean up old connections"""
    logger.info("Starting connection cleanup task")
    while True:
        await asyncio.sleep(60)  # Run every minute
        current_time = time.time()

        async with connections_lock:
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


# ─── ERROR HANDLERS ──────────────────────────────────────────────────────────


@app.errorhandler(404)
async def not_found(error):
    logger.warning(f"404 error: {request.path}")
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
async def internal_error(error):
    logger.error(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ─── MAIN ────────────────────────────────────────────────────────────────────


def run():
    """Run the async proxy server"""
    print(f"Starting async proxy server on port {SERVER_LISTEN_PORT}")
    print("This server should be accessible through the AWS SigV4 proxy")

    app.run(host="0.0.0.0", port=SERVER_LISTEN_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    run()
