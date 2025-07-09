# HTTP Proxy Client/Server System

This system provides a client-server HTTP proxy that can handle large requests by splitting them into chunks and communicating through an AWS SigV4 proxy. It's designed to work around the 2MB body size limit of the AWS API Gateway.

## Architecture

The system consists of three components:

1. **Proxy Client** (`proxy_client.py`) - Accepts HTTP proxy requests from applications and splits large requests into chunks
2. **Proxy Server** (`proxy_server.py`) - Receives chunks, assembles them into complete requests, and forwards to target servers
3. **AWS SigV4 Proxy** (`proxy.py`) - Handles authentication and communication between client and server

## How It Works

### For Small Requests (≤ 1MB):
1. Client receives HTTP proxy request
2. Client creates connection on server via AWS proxy
3. Client sends single chunk with request data
4. Server forwards request to target server
5. Server streams response back to client

### For Large Requests (> 1MB):
1. Client receives HTTP proxy request
2. Client creates connection on server via AWS proxy
3. Client splits request body into 1MB chunks
4. Client sends chunks sequentially to server
5. When final chunk is sent, server assembles complete request
6. Server forwards assembled request to target server
7. Server streams response back in chunks

## Setup

### Prerequisites

1. Python 3.7+
2. Required packages:
   ```bash
   pip install requests botocore requests-aws4auth
   ```
3. AWS credentials configured (via AWS CLI, environment variables, or IAM role)

### Configuration

Update the configuration in both `proxy_client.py` and `proxy_server.py`:

```python
# AWS SigV4 Proxy configuration
API_GATEWAY_URL = "https://your-api-gateway-url.execute-api.region.amazonaws.com"
AWS_REGION = "your-aws-region"
SERVICE = "execute-api"

# Ports
CLIENT_LISTEN_PORT = 8080  # Proxy client port
SERVER_LISTEN_PORT = 9090  # Proxy server port
```

## Usage

### 1. Start the Proxy Server

```bash
python proxy_server.py
```

The server will start listening on port 9090 and should be accessible through your AWS SigV4 proxy.

### 2. Start the Proxy Client

```bash
python proxy_client.py
```

The client will start listening on port 8080 and accept HTTP proxy requests.

### 3. Configure Your Application

Configure your application to use the proxy client:

```python
import requests

proxies = {
    'http': 'http://localhost:8080',
    'https': 'http://localhost:8080'
}

# Small request
response = requests.get('http://example.com', proxies=proxies)

# Large request (will be automatically chunked)
large_data = "x" * (3 * 1024 * 1024)  # 3MB
response = requests.post('http://example.com/upload', data=large_data, proxies=proxies)
```

### 4. Using with curl

```bash
# Small request
curl -x http://localhost:8080 http://httpbin.org/get

# Large request
curl -x http://localhost:8080 -X POST -d "$(printf 'x%.0s' {1..3145728})" http://httpbin.org/post
```

## Testing

Run the test script to verify the system works:

```bash
python test_proxy.py
```

This will:
1. Start both proxy client and server
2. Test small requests (no chunking needed)
3. Test large requests (chunking required)
4. Clean up processes

## API Endpoints

The proxy server exposes these endpoints:

- `POST /proxy/connection` - Create a new connection
- `POST /proxy/chunk` - Send a chunk of data
- `GET /proxy/response` - Get response chunks

### Connection Creation

```json
{
  "connection_id": "uuid",
  "target_host": "example.com",
  "method": "POST",
  "path": "/upload",
  "headers": {"Content-Type": "application/json"},
  "body_size": 3145728
}
```

### Chunk Headers

- `X-Connection-ID` - Connection identifier
- `X-Chunk-Final` - "true" if this is the final chunk

### Response Headers

- `X-More-Chunks` - "true" if more response chunks are available
- `X-Chunk-Index` - Index of the requested chunk

## Error Handling

The system includes comprehensive error handling:

- Connection timeouts (5 minutes)
- Automatic cleanup of old connections
- Proper error responses for failed requests
- Retry mechanisms for chunk delivery

## Security Considerations

1. The proxy client and server communicate through the AWS SigV4 proxy, which provides authentication
2. Connection IDs are UUIDs to prevent guessing
3. Connections are automatically cleaned up to prevent resource exhaustion
4. Headers are filtered to remove proxy-specific headers before forwarding

## Limitations

1. Only supports HTTP (not HTTPS tunneling)
2. Maximum chunk size is 1MB (configurable)
3. Connections are kept in memory (not suitable for very high concurrency)
4. No persistent connections (each request creates a new connection)

## Troubleshooting

### Common Issues

1. **AWS credentials not configured**
   - Ensure AWS credentials are properly configured
   - Check that the API Gateway URL is correct

2. **Connection refused**
   - Verify both client and server are running
   - Check that ports are not blocked by firewall

3. **Large requests fail**
   - Ensure chunk size is under 2MB limit
   - Check that all chunks are being sent before requesting response

4. **Memory usage**
   - Monitor connection count and cleanup
   - Adjust cleanup timeout if needed

### Debug Mode

Add debug logging by modifying the `log_message` method in both client and server handlers. 