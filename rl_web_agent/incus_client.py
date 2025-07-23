"""
Incus Container Management Client

This client communicates with the Incus HTTP server to manage containers
for WebArena environments from the agent environment.
"""

import logging
from typing import Optional

import httpx


class IncusClient:
    """HTTP client for communicating with Incus server"""

    def __init__(self, server_url: str = "http://localhost:8000", timeout: int = 300):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    async def launch_container(self, base_name: str, instance_name: str) -> str:
        """
        Launch a new container by copying from base and starting it.

        Args:
            base_name: Name of the base container to copy from
            instance_name: Name for the new container instance

        Returns:
            str: IP address of the launched container

        Raises:
            RuntimeError: If container launch fails
        """
        url = f"{self.server_url}/containers/launch"
        payload = {"base_name": base_name, "instance_name": instance_name}

        self.logger.info(f"Launching container {instance_name} from base {base_name}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    ip_address = data["ip_address"]
                    self.logger.info(f"Container {instance_name} launched with IP {ip_address}")
                    return ip_address
                else:
                    error_text = response.text
                    self.logger.error(f"Failed to launch container: {error_text}")
                    raise RuntimeError(f"Failed to launch container {instance_name}: {error_text}")

            except httpx.TimeoutException as e:
                self.logger.error(f"Timeout launching container {instance_name}")
                raise RuntimeError(f"Timeout launching container {instance_name}") from e
            except httpx.RequestError as e:
                self.logger.error(f"Network error launching container: {e}")
                raise RuntimeError(f"Network error launching container {instance_name}: {e}") from e

    async def delete_container(self, container_name: str) -> None:
        """
        Stop and remove a container.

        Args:
            container_name: Name of the container to delete

        Raises:
            RuntimeError: If container deletion fails
        """
        url = f"{self.server_url}/containers/{container_name}"

        self.logger.info(f"Deleting container {container_name}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.delete(url)
                if response.status_code == 200:
                    self.logger.info(f"Container {container_name} deleted successfully")
                else:
                    error_text = response.text
                    self.logger.error(f"Failed to delete container: {error_text}")
                    raise RuntimeError(f"Failed to delete container {container_name}: {error_text}")

            except httpx.TimeoutException as e:
                self.logger.error(f"Timeout deleting container {container_name}")
                raise RuntimeError(f"Timeout deleting container {container_name}") from e
            except httpx.RequestError as e:
                self.logger.error(f"Network error deleting container: {e}")
                raise RuntimeError(f"Network error deleting container {container_name}: {e}") from e

    async def get_container_status(self, container_name: str) -> Optional[dict]:
        """
        Get status of a container.

        Args:
            container_name: Name of the container

        Returns:
            dict: Container status information or None if not found
        """
        url = f"{self.server_url}/containers/{container_name}/status"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    error_text = response.text
                    self.logger.error(f"Failed to get container status: {error_text}")
                    return None

            except (httpx.TimeoutException, httpx.RequestError) as e:
                self.logger.error(f"Error getting container status: {e}")
                return None

    async def health_check(self) -> bool:
        """
        Check if the Incus server is healthy and available.

        Returns:
            bool: True if server is healthy, False otherwise
        """
        url = f"{self.server_url}/health"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.get(url)
                return response.status_code == 200
            except (httpx.TimeoutException, httpx.RequestError):
                return False
