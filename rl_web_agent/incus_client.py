"""
Incus Container Management Functions

Stateless async functions for communicating with Incus HTTP server to manage containers
for WebArena environments from the agent environment.
"""

import logging

import httpx


def _get_httpx_client_kwargs(proxy_server: str | None = None, timeout: int = 300) -> dict:
    """
    Create httpx client kwargs with optional proxy configuration.

    Args:
        proxy_server: Proxy server URL (e.g., "http://localhost:8080")
        timeout: Request timeout in seconds

    Returns:
        dict: Client configuration kwargs
    """
    kwargs = {"timeout": timeout}
    if proxy_server:
        kwargs["proxy"] = proxy_server
    return kwargs


async def launch_container(server_url: str, base_name: str, container_name: str, timeout: int = 300, proxy_server: str | None = None) -> str:
    """
    Launch a new container by copying from base and starting it.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        base_name: Name of the base container to copy from
        container_name: Name for the new container instance
        timeout: Request timeout in seconds
        proxy_server: Optional proxy server URL for HTTP requests

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
    if proxy_server:
        logger.debug(f"Using proxy server: {proxy_server}")

    client_kwargs = _get_httpx_client_kwargs(proxy_server, timeout)
    async with httpx.AsyncClient(**client_kwargs) as client:
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


async def delete_container(server_url: str, container_name: str, timeout: int = 300, proxy_server: str | None = None) -> None:
    """
    Stop and remove a container.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        container_name: Name of the container to delete
        timeout: Request timeout in seconds
        proxy_server: Optional proxy server URL for HTTP requests

    Raises:
        RuntimeError: If container deletion fails
    """
    logger = logging.getLogger(__name__)
    server_url = server_url.rstrip("/")
    url = f"{server_url}/containers/{container_name}"

    logger.info(f"Deleting container {container_name}")
    if proxy_server:
        logger.debug(f"Using proxy server: {proxy_server}")

    client_kwargs = _get_httpx_client_kwargs(proxy_server, timeout)
    async with httpx.AsyncClient(**client_kwargs) as client:
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


async def get_container_status(server_url: str, container_name: str, timeout: int = 300, proxy_server: str | None = None) -> dict | None:
    """
    Get status of a container.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        container_name: Name of the container
        timeout: Request timeout in seconds
        proxy_server: Optional proxy server URL for HTTP requests

    Returns:
        dict: Container status information or None if not found
    """
    logger = logging.getLogger(__name__)
    server_url = server_url.rstrip("/")
    url = f"{server_url}/containers/{container_name}/status"

    if proxy_server:
        logger.debug(f"Using proxy server: {proxy_server}")

    client_kwargs = _get_httpx_client_kwargs(proxy_server, timeout)
    async with httpx.AsyncClient(**client_kwargs) as client:
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


async def health_check(server_url: str, timeout: int = 10, proxy_server: str | None = None) -> bool:
    """
    Check if the Incus server is healthy and available.

    Args:
        server_url: Incus server URL (e.g., "http://localhost:8001")
        timeout: Request timeout in seconds
        proxy_server: Optional proxy server URL for HTTP requests

    Returns:
        bool: True if server is healthy, False otherwise
    """
    server_url = server_url.rstrip("/")
    url = f"{server_url}/health"

    client_kwargs = _get_httpx_client_kwargs(proxy_server, timeout)
    async with httpx.AsyncClient(**client_kwargs) as client:
        try:
            response = await client.get(url)
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.RequestError):
            return False
