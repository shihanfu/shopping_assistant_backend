"""
Incus Container Management Functions

Stateless async functions for communicating with Incus HTTP server to manage containers
for WebArena environments from the agent environment.
"""

import logging

import httpx


async def launch_container(server_url: str, base_name: str, container_name: str, timeout: int = 300) -> str:
    """
    Launch a new container by copying from base and starting it.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        base_name: Name of the base container to copy from
        container_name: Name for the new container instance
        timeout: Request timeout in seconds

    Returns:
        str: IP address of the launched container

    Raises:
        RuntimeError: If container launch fails
    """
    logger = logging.getLogger(__name__)
    server_url = server_url.rstrip("/")
    url = f"{server_url}/containers/launch"
    payload = {"base_name": base_name, "container_name": container_name}

    logger.info(f"Launching container {container_name} from base {base_name}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                data = response.json()
                ip_address = data["ip_address"]
                logger.info(f"Container {container_name} launched with IP {ip_address}")
                return ip_address
            else:
                error_text = response.text
                logger.error(f"Failed to launch container: {error_text}")
                raise RuntimeError(f"Failed to launch container {container_name}: {error_text}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout launching container {container_name}")
            raise RuntimeError(f"Timeout launching container {container_name}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error launching container: {e}")
            raise RuntimeError(f"Network error launching container {container_name}: {e}") from e


async def delete_container(server_url: str, container_name: str, timeout: int = 300) -> None:
    """
    Stop and remove a container.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        container_name: Name of the container to delete
        timeout: Request timeout in seconds

    Raises:
        RuntimeError: If container deletion fails
    """
    logger = logging.getLogger(__name__)
    server_url = server_url.rstrip("/")
    url = f"{server_url}/containers/{container_name}"

    logger.info(f"Deleting container {container_name}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.delete(url)
            if response.status_code == 200:
                logger.info(f"Container {container_name} deleted successfully")
            else:
                error_text = response.text
                logger.error(f"Failed to delete container: {error_text}")
                raise RuntimeError(f"Failed to delete container {container_name}: {error_text}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout deleting container {container_name}")
            raise RuntimeError(f"Timeout deleting container {container_name}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error deleting container: {e}")
            raise RuntimeError(f"Network error deleting container {container_name}: {e}") from e


async def get_container_status(server_url: str, container_name: str, timeout: int = 300) -> dict | None:
    """
    Get status of a container.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        container_name: Name of the container
        timeout: Request timeout in seconds

    Returns:
        dict: Container status information or None if not found
    """
    logger = logging.getLogger(__name__)
    server_url = server_url.rstrip("/")
    url = f"{server_url}/containers/{container_name}/status"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                error_text = response.text
                logger.error(f"Failed to get container status: {error_text}")
                return None

        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.error(f"Error getting container status: {e}")
            return None


async def health_check(server_url: str, timeout: int = 10) -> bool:
    """
    Check if the Incus server is healthy and available.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        timeout: Request timeout in seconds

    Returns:
        bool: True if server is healthy, False otherwise
    """
    server_url = server_url.rstrip("/")
    url = f"{server_url}/health"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url)
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.RequestError):
            return False
