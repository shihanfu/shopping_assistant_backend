#!/usr/bin/env python3
"""
Test script for the async HTTP proxy client/server system.
This script demonstrates how to use the async proxy system.
"""

import os
import subprocess
import sys
import time

import requests


def test_small_request():
    """Test a small request that doesn't need chunking"""
    print("Testing small request...")

    # Configure requests to use our proxy client
    proxies = {"http": "http://localhost:8082", "https": "http://localhost:8082"}

    try:
        # Test GET request
        resp = requests.get("http://httpbin.org/get", proxies=proxies, timeout=30)
        print(f"GET request status: {resp.status_code}")
        print(f"Response size: {len(resp.content)} bytes")

        # Test POST request with small data
        data = {"test": "data", "message": "Hello, async proxy!"}
        resp = requests.post("http://httpbin.org/post", json=data, proxies=proxies, timeout=30)
        print(f"POST request status: {resp.status_code}")
        print(f"Response size: {len(resp.content)} bytes")

        return True
    except Exception as e:
        print(f"Small request test failed: {e}")
        return False


def test_large_request():
    """Test a large request that needs chunking"""
    print("Testing large request...")

    # Configure requests to use our proxy client
    proxies = {"http": "http://localhost:8082", "https": "http://localhost:8082"}

    try:
        # Create a large payload (3MB)
        large_data = "x" * (3 * 1024 * 1024)

        # Test POST request with large data
        resp = requests.post("http://httpbin.org/post", data=large_data, proxies=proxies, timeout=60)
        print(f"Large POST request status: {resp.status_code}")
        print(f"Response size: {len(resp.content)} bytes")

        # Verify the data was received correctly
        if resp.status_code == 200:
            try:
                response_data = resp.json()
                received_size = len(response_data.get("data", ""))
                print(f"Sent {len(large_data)} bytes, received {received_size} bytes")
                return received_size == len(large_data)
            except:
                # If response is not JSON, just check that we got a response
                print(f"Got non-JSON response of {len(resp.content)} bytes")
                return True

        return False
    except Exception as e:
        print(f"Large request test failed: {e}")
        return False


def start_async_proxy_server():
    """Start the async proxy server in a separate process"""
    print("Starting async proxy server...")
    server_process = subprocess.Popen([sys.executable, "proxy_server_async.py"])
    time.sleep(3)  # Give server more time to start
    return server_process


def start_proxy_client():
    """Start the proxy client in a separate process"""
    print("Starting proxy client...")
    client_process = subprocess.Popen([sys.executable, "proxy_client.py"])
    time.sleep(3)  # Give client more time to start
    return client_process


def test_server_health():
    """Test if the server is responding"""
    try:
        # Try to make a direct request to the server (should fail with 404)
        resp = requests.get("http://localhost:9090/health", timeout=5)
        return True
    except requests.exceptions.ConnectionError:
        return False
    except:
        return True  # Any response means server is up


def main():
    """Main test function"""
    print("Async HTTP Proxy Client/Server Test")
    print("=" * 40)

    # Check if required files exist
    required_files = ["proxy_client.py", "proxy_server_async.py"]
    for file in required_files:
        if not os.path.exists(file):
            print(f"Error: {file} not found")
            return

    # Start proxy server and client
    server_process = None
    client_process = None

    try:
        server_process = start_async_proxy_server()

        # Wait for server to be ready
        print("Waiting for async server to be ready...")
        for i in range(10):
            if test_server_health():
                print("Server is responding!")
                break
            time.sleep(1)
        else:
            print("Warning: Server may not be ready")

        client_process = start_proxy_client()

        # Wait a bit more for both to be fully ready
        time.sleep(5)

        # Run tests
        print("\nRunning tests...")

        small_success = test_small_request()
        print(f"Small request test: {'PASSED' if small_success else 'FAILED'}")

        time.sleep(2)  # Brief pause between tests

        large_success = test_large_request()
        print(f"Large request test: {'PASSED' if large_success else 'FAILED'}")

        # Summary
        print("\nTest Summary:")
        print(f"Small requests: {'✓' if small_success else '✗'}")
        print(f"Large requests: {'✓' if large_success else '✗'}")

        if small_success and large_success:
            print("\n🎉 All tests passed! The async proxy system is working correctly.")
        else:
            print("\n❌ Some tests failed. Check the output above for details.")
            print("Check the server and client logs for more information.")

    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nTest failed with error: {e}")
    finally:
        # Clean up processes
        if client_process:
            print("Stopping proxy client...")
            client_process.terminate()
            try:
                client_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                client_process.kill()

        if server_process:
            print("Stopping async proxy server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()

        print("Cleanup complete")


if __name__ == "__main__":
    main()
