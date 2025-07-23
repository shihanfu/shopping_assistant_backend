#!/usr/bin/env python3
"""
Incus Container Management HTTP Server

This server runs on the Incus host and exposes container operations via HTTP API.
It provides endpoints to launch, manage, and cleanup containers for WebArena environments.
"""

import asyncio
import json
import logging

from quart import Quart, jsonify, request

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Quart(__name__)


async def run_incus_command(cmd: list[str]) -> tuple[int, str, str]:
    """Run incus command and return exit code, stdout, stderr"""
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()

        return process.returncode, stdout.decode(), stderr.decode()
    except Exception as e:
        logger.error(f"Failed to run command {cmd}: {e}")
        return 1, "", str(e)


async def get_container_ip(container_name: str) -> str | None:
    """Get IP address of a running container"""
    cmd = ["incus", "list", container_name, "--format", "json"]
    exit_code, stdout, stderr = await run_incus_command(cmd)

    if exit_code != 0:
        logger.error(f"Failed to get container info: {stderr}")
        return None

    try:
        containers = json.loads(stdout)
        if not containers:
            return None

        container = containers[0]
        state = container.get("state", {})
        network = state.get("network", {})

        # Look for eth0 interface first, then any interface with an inet address
        for interface_name, interface_info in network.items():
            if interface_name == "lo":  # Skip loopback
                continue

            addresses = interface_info.get("addresses", [])
            for addr in addresses:
                if addr.get("family") == "inet" and addr.get("scope") == "global":
                    return addr["address"]

        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse container info: {e}")
        return None


async def get_container_status(container_name: str) -> str | None:
    """Get status of a container"""
    cmd = ["incus", "list", container_name, "--format", "json"]
    exit_code, stdout, stderr = await run_incus_command(cmd)

    if exit_code != 0:
        return None

    try:
        containers = json.loads(stdout)
        if not containers:
            return None

        return containers[0]["status"].lower()
    except (json.JSONDecodeError, KeyError):
        return None


@app.route("/containers/launch", methods=["POST"])
async def launch_container():
    """Launch a new container by copying from base and starting it"""
    data = await request.get_json()
    base_name = data["base_name"]
    container_name = data["container_name"]

    logger.info(f"Launching container {container_name} from base {base_name}")

    # Step 1: Stop base container if it's running (required for copying)
    base_status = await get_container_status(base_name)
    if base_status == "running":
        logger.info(f"Stopping base container {base_name} before copying")
        stop_cmd = ["incus", "stop", base_name]
        exit_code, stdout, stderr = await run_incus_command(stop_cmd)
        if exit_code != 0:
            logger.error(f"Failed to stop base container: {stderr}")
            return jsonify({"error": f"Failed to stop base container {base_name}: {stderr}"}), 500

    # Step 2: Copy base container
    copy_cmd = ["incus", "copy", base_name, container_name]
    exit_code, stdout, stderr = await run_incus_command(copy_cmd)

    if exit_code != 0:
        logger.error(f"Failed to copy container: {stderr}")
        return jsonify({"error": f"Failed to copy container from {base_name}: {stderr}"}), 500

    logger.info(f"Successfully copied {base_name} to {container_name}")

    # Step 3: Start the container
    start_cmd = ["incus", "start", container_name]
    exit_code, stdout, stderr = await run_incus_command(start_cmd)

    if exit_code != 0:
        logger.error(f"Failed to start container: {stderr}")
        # Cleanup: remove the copied container
        await run_incus_command(["incus", "rm", container_name])
        return jsonify({"error": f"Failed to start container {container_name}: {stderr}"}), 500

    logger.info(f"Successfully started container {container_name}")

    # Step 4: Wait for container to get IP address
    max_retries = 30  # 30 seconds timeout
    ip_address = None

    for retry in range(max_retries):
        ip_address = await get_container_ip(container_name)
        if ip_address:
            break
        await asyncio.sleep(1)

    if not ip_address:
        logger.error(f"Container {container_name} started but no IP address found")
        # Don't fail here, just log warning - container might still be usable
        ip_address = "unknown"

    logger.info(f"Container {container_name} launched successfully with IP {ip_address}")

    return jsonify({"container_name": container_name, "ip_address": ip_address, "status": "running"})


@app.route("/containers/<container_name>", methods=["DELETE"])
async def delete_container(container_name: str):
    """Stop and remove a container"""
    logger.info(f"Deleting container {container_name}")

    # Step 1: Stop the container (ignore errors if already stopped)
    stop_cmd = ["incus", "stop", container_name]
    exit_code, stdout, stderr = await run_incus_command(stop_cmd)

    if exit_code != 0:
        logger.warning(f"Failed to stop container (might already be stopped): {stderr}")
    else:
        logger.info(f"Successfully stopped container {container_name}")

    # Step 2: Remove the container
    rm_cmd = ["incus", "rm", container_name]
    exit_code, stdout, stderr = await run_incus_command(rm_cmd)

    if exit_code != 0:
        logger.error(f"Failed to remove container: {stderr}")
        return jsonify({"error": f"Failed to remove container {container_name}: {stderr}"}), 500

    logger.info(f"Successfully removed container {container_name}")
    return jsonify({"message": f"Container {container_name} deleted successfully"})


@app.route("/containers/<container_name>/status", methods=["GET"])
async def get_status(container_name: str):
    """Get status of a container"""
    status = await get_container_status(container_name)

    if status is None:
        return jsonify({"error": f"Container {container_name} not found"}), 404

    ip_address = None
    if status == "running":
        ip_address = await get_container_ip(container_name)

    return jsonify({"container_name": container_name, "status": status, "ip_address": ip_address})


@app.route("/health", methods=["GET"])
async def health_check():
    """Health check endpoint"""
    # Test if incus command is available
    exit_code, stdout, stderr = await run_incus_command(["incus", "version"])

    if exit_code != 0:
        return jsonify({"error": f"Incus not available: {stderr}"}), 503

    return jsonify({"status": "healthy", "incus_version": stdout.strip()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=False)
