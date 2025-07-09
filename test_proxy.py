#!/usr/bin/env python3
"""
Test script for the HTTP proxy client/server system.
This script demonstrates how to use the proxy system.
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
    proxies = {"http": "http://localhost:8080", "https": "http://localhost:8080"}

    try:
        # Test GET request
        resp = requests.get("http://httpbin.org/get", proxies=proxies)
        print(f"GET request status: {resp.status_code}")
        print(f"Response size: {len(resp.content)} bytes")

        # Test POST request with small data
        data = {"test": "data", "message": "Hello, proxy!"}
        resp = requests.post("http://httpbin.org/post", json=data, proxies=proxies)
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
    proxies = {"http": "http://localhost:8080", "https": "http://localhost:8080"}

    try:
        # Create a large payload (3MB)
        large_data = "x" * (3 * 1024 * 1024)

        # Test POST request with large data
        resp = requests.post("http://httpbin.org/post", data=large_data, proxies=proxies)
        print(f"Large POST request status: {resp.status_code}")
        print(f"Response size: {len(resp.content)} bytes")

        # Verify the data was received correctly
        if resp.status_code == 200:
            response_data = resp.json()
            received_size = len(response_data.get("data", ""))
            print(f"Sent {len(large_data)} bytes, received {received_size} bytes")
            return received_size == len(large_data)

        return False
    except Exception as e:
        print(f"Large request test failed: {e}")
        return False


def start_proxy_server():
    """Start the proxy server in a separate process"""
    print("Starting proxy server...")
    server_process = subprocess.Popen([sys.executable, "proxy_server.py"])
    time.sleep(2)  # Give server time to start
    return server_process


def start_proxy_client():
    """Start the proxy client in a separate process"""
    print("Starting proxy client...")
    client_process = subprocess.Popen([sys.executable, "proxy_client.py"])
    time.sleep(2)  # Give client time to start
    return client_process


def main():
    """Main test function"""
    print("HTTP Proxy Client/Server Test")
    print("=" * 40)

    # Check if required files exist
    required_files = ["proxy_client.py", "proxy_server.py"]
    for file in required_files:
        if not os.path.exists(file):
            print(f"Error: {file} not found")
            return

    # Start proxy server and client
    server_process = None
    client_process = None

    try:
        server_process = start_proxy_server()
        client_process = start_proxy_client()

        # Wait a bit for both to start
        time.sleep(3)

        # Run tests
        print("\nRunning tests...")

        small_success = test_small_request()
        print(f"Small request test: {'PASSED' if small_success else 'FAILED'}")

        large_success = test_large_request()
        print(f"Large request test: {'PASSED' if large_success else 'FAILED'}")

        # Summary
        print("\nTest Summary:")
        print(f"Small requests: {'✓' if small_success else '✗'}")
        print(f"Large requests: {'✓' if large_success else '✗'}")

        if small_success and large_success:
            print("\n🎉 All tests passed! The proxy system is working correctly.")
        else:
            print("\n❌ Some tests failed. Check the output above for details.")

    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nTest failed with error: {e}")
    finally:
        # Clean up processes
        if client_process:
            print("Stopping proxy client...")
            client_process.terminate()
            client_process.wait()

        if server_process:
            print("Stopping proxy server...")
            server_process.terminate()
            server_process.wait()

        print("Cleanup complete")


if __name__ == "__main__":
    main()
