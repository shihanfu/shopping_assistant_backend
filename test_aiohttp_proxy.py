#!/usr/bin/env python3

import asyncio
import time

import aiohttp


async def test_aiohttp_proxy():
    """Test the aiohttp proxy client"""

    proxy_url = "http://localhost:8082"
    test_url = "http://httpbin.org/get"

    print("Testing aiohttp proxy client...")
    print(f"Proxy: {proxy_url}")
    print(f"Target: {test_url}")

    # Create a session with proxy
    connector = aiohttp.TCPConnector()
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        try:
            start_time = time.time()

            # Make request through proxy
            async with session.get(test_url, proxy=proxy_url) as response:
                elapsed = time.time() - start_time

                print(f"\nResponse received in {elapsed:.3f}s")
                print(f"Status: {response.status}")
                print(f"Headers: {dict(response.headers)}")

                body = await response.text()
                print(f"Body length: {len(body)} characters")
                print(f"Body preview: {body[:200]}...")

        except Exception as exc:
            print(f"Request failed: {exc}")


async def test_post_request():
    """Test POST request with body"""

    proxy_url = "http://localhost:8082"
    test_url = "http://httpbin.org/post"

    print("\n" + "=" * 50)
    print("Testing POST request with body...")

    test_data = {"message": "Hello from aiohttp proxy!", "timestamp": time.time()}

    connector = aiohttp.TCPConnector()
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        try:
            start_time = time.time()

            # Make POST request through proxy
            async with session.post(test_url, json=test_data, proxy=proxy_url) as response:
                elapsed = time.time() - start_time

                print(f"\nPOST response received in {elapsed:.3f}s")
                print(f"Status: {response.status}")

                body = await response.text()
                print(f"Body length: {len(body)} characters")
                print(f"Body preview: {body[:300]}...")

        except Exception as exc:
            print(f"POST request failed: {exc}")


def test_with_curl():
    """Test with curl command"""
    import subprocess

    print("\n" + "=" * 50)
    print("Testing with curl...")

    try:
        # Test simple GET
        result = subprocess.run(["curl", "-x", "http://localhost:8082", "http://httpbin.org/get", "-v", "--max-time", "30"], capture_output=True, text=True, timeout=35)

        print("Curl output:")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("Return code:", result.returncode)

    except subprocess.TimeoutExpired:
        print("Curl request timed out")
    except Exception as exc:
        print(f"Curl test failed: {exc}")


async def main():
    """Run all tests"""
    print("Starting aiohttp proxy tests...")

    # Test basic GET
    await test_aiohttp_proxy()

    # Test POST with body
    await test_post_request()

    # Test with curl
    test_with_curl()

    print("\nAll tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
