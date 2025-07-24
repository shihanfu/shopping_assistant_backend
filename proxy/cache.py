#!/usr/bin/env python3

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("proxy-cache")


class CacheEntry:
    """Represents a cached response entry"""

    def __init__(self, status_code: int, headers: list, body: bytes, expires_at: Optional[datetime] = None, etag: Optional[str] = None):
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.expires_at = expires_at
        self.etag = etag
        self.created_at = datetime.now()

    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "status_code": self.status_code,
            "headers": self.headers,
            "body": self.body.hex(),  # Convert bytes to hex string
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "etag": self.etag,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        """Create from dictionary (JSON deserialization)"""
        expires_at = datetime.fromisoformat(data["expires_at"]) if data["expires_at"] else None
        entry = cls(status_code=data["status_code"], headers=data["headers"], body=bytes.fromhex(data["body"]), expires_at=expires_at, etag=data.get("etag"))
        entry.created_at = datetime.fromisoformat(data["created_at"])
        return entry


class FileLockManager:
    """Simple file-based locking mechanism using asyncio locks"""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_lock = asyncio.Lock()

    async def get_lock(self, cache_key: str) -> asyncio.Lock:
        """Get or create a lock for a specific cache key"""
        async with self._lock_lock:
            if cache_key not in self._locks:
                self._locks[cache_key] = asyncio.Lock()
            return self._locks[cache_key]


class ProxyCache:
    """File-based cache with locking for proxy responses"""

    def __init__(self, cache_dir: Optional[str] = None, max_age_seconds: int = 300):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "proxy_cache"
        self.max_age_seconds = max_age_seconds
        self.lock_manager = FileLockManager()

    async def init(self):
        """Initialize cache directory"""
        try:
            # Use synchronous makedirs since it's fast and async not needed
            os.makedirs(self.cache_dir, exist_ok=True)
            logger.info(f"Cache initialized at {self.cache_dir}")
        except Exception as e:
            logger.error(f"Failed to initialize cache directory: {e}")
            raise

    def _generate_cache_key(self, host: str, method: str, path: str, headers: list, body: bytes) -> str:
        """Generate cache key using Host header and request body hash"""
        # Use the original Host header (not the rewritten target host)
        key_components = [
            host,
            method,
            path,
            # Include relevant headers that affect response (excluding per-request and auth headers)
            json.dumps(sorted([(k.lower(), v) for k, v in headers if k.lower() not in ("user-agent", "x-forwarded-for", "x-real-ip", "x-request-id", "date", "authorization", "cookie", "x-api-key", "x-auth-token", "x-target-host-rewrite", "remote-addr")])),
            hashlib.sha256(body if body else b"").hexdigest(),
        ]

        # Create hash of all components
        key_string = "|".join(str(comp) for comp in key_components)
        cache_key = hashlib.sha256(key_string.encode()).hexdigest()

        logger.debug(f"Generated cache key {cache_key} for {method} {host}{path}")
        return cache_key

    def _get_cache_file_path(self, cache_key: str) -> Path:
        """Get file path for cache key"""
        return self.cache_dir / f"{cache_key}.json"

    def _parse_cache_control(self, headers: list) -> dict:
        """Parse Cache-Control header and return directives"""
        cache_control = {}

        for name, value in headers:
            if name.lower() == "cache-control":
                # Parse cache control directives
                directives = [d.strip() for d in value.split(",")]
                for directive in directives:
                    if "=" in directive:
                        key, val = directive.split("=", 1)
                        cache_control[key.strip().lower()] = val.strip()
                    else:
                        cache_control[directive.strip().lower()] = True
                break

        return cache_control

    def _calculate_expiry(self, headers: list) -> Optional[datetime]:
        """Calculate expiry time based on response headers"""
        cache_control = self._parse_cache_control(headers)

        # Check if response should not be cached
        if cache_control.get("no-cache") or cache_control.get("no-store"):
            return None

        # Check for explicit max-age
        if "max-age" in cache_control:
            try:
                max_age = int(cache_control["max-age"])
                if max_age <= 0:
                    return None
                return datetime.now() + timedelta(seconds=max_age)
            except (ValueError, TypeError):
                pass

        # Check for Expires header
        for name, value in headers:
            if name.lower() == "expires":
                try:
                    # Parse HTTP date format
                    from email.utils import parsedate_to_datetime

                    expires_dt = parsedate_to_datetime(value)
                    if expires_dt > datetime.now(expires_dt.tzinfo):
                        return expires_dt.replace(tzinfo=None)  # Convert to naive datetime
                except Exception:
                    pass
                break

        # Default cache duration if no explicit cache headers
        return datetime.now() + timedelta(seconds=self.max_age_seconds)

    def _should_cache_response(self, status_code: int, headers: list) -> bool:
        """Determine if response should be cached"""
        # Only cache successful responses
        if status_code not in (200, 301, 302, 304, 404, 410):
            return False

        cache_control = self._parse_cache_control(headers)

        # Don't cache if explicitly forbidden
        if cache_control.get("no-cache") or cache_control.get("no-store"):
            return False

        # Don't cache if private (proxy should not cache private responses)
        if cache_control.get("private"):
            return False

        return True

    async def get(self, host: str, method: str, path: str, headers: list, body: bytes) -> Optional[CacheEntry]:
        """Get cached response if available and valid"""
        cache_key = self._generate_cache_key(host, method, path, headers, body)
        cache_file = self._get_cache_file_path(cache_key)

        # Acquire lock for this cache key
        lock = await self.lock_manager.get_lock(cache_key)
        async with lock:
            try:
                if not cache_file.exists():
                    logger.debug(f"Cache miss: {cache_key}")
                    return None

                # Read cache file synchronously (fast for small files)
                def read_cache():
                    with open(cache_file) as f:
                        return json.loads(f.read())

                data = await asyncio.get_event_loop().run_in_executor(None, read_cache)
                entry = CacheEntry.from_dict(data)

                # Check if expired
                if entry.is_expired():
                    logger.debug(f"Cache expired: {cache_key}")
                    # Clean up expired entry
                    try:
                        cache_file.unlink()
                    except Exception:
                        pass
                    return None

                logger.info(f"Cache hit: {cache_key}")
                return entry

            except Exception as e:
                logger.error(f"Error reading cache {cache_key}: {e}")
                # Clean up corrupted cache file
                try:
                    cache_file.unlink()
                except Exception:
                    pass
                return None

    async def put(self, host: str, method: str, path: str, headers: list, body: bytes, status_code: int, response_headers: list, response_body: bytes) -> bool:
        """Cache response if appropriate"""
        # Check if response should be cached
        if not self._should_cache_response(status_code, response_headers):
            logger.debug(f"Response not cacheable: {method} {host}{path} -> {status_code}")
            return False

        cache_key = self._generate_cache_key(host, method, path, headers, body)
        cache_file = self._get_cache_file_path(cache_key)

        # Calculate expiry
        expires_at = self._calculate_expiry(response_headers)
        if expires_at is None:
            logger.debug(f"Response has no-cache directive: {cache_key}")
            return False

        # Extract ETag if present
        etag = None
        for name, value in response_headers:
            if name.lower() == "etag":
                etag = value
                break

        # Create cache entry
        entry = CacheEntry(status_code, response_headers, response_body, expires_at, etag)

        # Acquire lock and write to cache
        lock = await self.lock_manager.get_lock(cache_key)
        async with lock:
            try:

                def write_cache():
                    with open(cache_file, "w") as f:
                        json.dump(entry.to_dict(), f, indent=2)

                await asyncio.get_event_loop().run_in_executor(None, write_cache)
                logger.info(f"Cached response: {cache_key} (expires: {expires_at})")
                return True

            except Exception as e:
                logger.error(f"Error writing cache {cache_key}: {e}")
                return False

    async def clear_expired(self):
        """Clear all expired cache entries"""
        try:
            if not self.cache_dir.exists():
                return

            def clear_expired_sync():
                files = os.listdir(self.cache_dir)
                cleared_count = 0

                for filename in files:
                    if not filename.endswith(".json"):
                        continue

                    cache_file = self.cache_dir / filename
                    try:
                        with open(cache_file) as f:
                            data = json.loads(f.read())

                        entry = CacheEntry.from_dict(data)
                        if entry.is_expired():
                            cache_file.unlink()
                            cleared_count += 1

                    except Exception:
                        # Remove corrupted cache files
                        try:
                            cache_file.unlink()
                            cleared_count += 1
                        except Exception:
                            pass

                return cleared_count

            cleared_count = await asyncio.get_event_loop().run_in_executor(None, clear_expired_sync)
            if cleared_count > 0:
                logger.info(f"Cleared {cleared_count} expired cache entries")

        except Exception as e:
            logger.error(f"Error clearing expired cache: {e}")

    async def clear_all(self):
        """Clear all cache entries"""
        try:
            if not self.cache_dir.exists():
                return

            def clear_all_sync():
                files = os.listdir(self.cache_dir)
                cleared_count = 0

                for filename in files:
                    if filename.endswith(".json"):
                        cache_file = self.cache_dir / filename
                        try:
                            cache_file.unlink()
                            cleared_count += 1
                        except Exception:
                            pass

                return cleared_count

            cleared_count = await asyncio.get_event_loop().run_in_executor(None, clear_all_sync)
            logger.info(f"Cleared {cleared_count} cache entries")

        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
