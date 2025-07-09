#!/bin/bash

# HTTP Proxy Client/Server Startup Script

echo "Starting HTTP Proxy Client/Server System"
echo "========================================"

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in PATH"
    exit 1
fi

# Check which client to use
CLIENT_TYPE=${1:-"sync"}  # Default to sync client

# Check if required files exist
if [ ! -f "proxy_server.py" ]; then
    echo "Error: proxy_server.py not found"
    exit 1
fi

if [ "$CLIENT_TYPE" = "aiohttp" ]; then
    CLIENT_FILE="proxy_client_aiohttp.py"
    CLIENT_PORT="8082"
else
    CLIENT_FILE="proxy_client.py"
    CLIENT_PORT="8080"
fi

if [ ! -f "$CLIENT_FILE" ]; then
    echo "Error: $CLIENT_FILE not found"
    exit 1
fi

# Install dependencies if needed
echo "Checking dependencies..."
pip3 install -r requirements.txt > /dev/null 2>&1

# Function to cleanup processes on exit
cleanup() {
    echo "Shutting down proxy system..."
    if [ ! -z "$CLIENT_PID" ]; then
        kill $CLIENT_PID 2>/dev/null
    fi
    if [ ! -z "$SERVER_PID" ]; then
        kill $SERVER_PID 2>/dev/null
    fi
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Start proxy server
echo "Starting proxy server on port 9090..."
python3 proxy_server.py &
SERVER_PID=$!

# Wait a moment for server to start
sleep 2

# Start proxy client
echo "Starting $CLIENT_TYPE proxy client on port $CLIENT_PORT..."
python3 $CLIENT_FILE &
CLIENT_PID=$!

# Wait a moment for client to start
sleep 2

echo ""
echo "Proxy system is running!"
echo "======================="
echo "Proxy Client ($CLIENT_TYPE): http://localhost:$CLIENT_PORT"
echo "Proxy Server: http://localhost:9090"
echo ""
echo "Configure your application to use:"
echo "  HTTP_PROXY=http://localhost:$CLIENT_PORT"
echo "  HTTPS_PROXY=http://localhost:$CLIENT_PORT"
echo ""
echo "Usage: $0 [sync|aiohttp]"
echo "Press Ctrl+C to stop the proxy system"

# Wait for processes
wait 