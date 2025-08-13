#!/bin/bash

# Start the Shopping Assistant Chat Server with correct AWS configuration

echo "🛍️ Starting Shopping Assistant Chat Server..."
echo "📡 Server will be available at: http://localhost:5000"
echo "🌐 Chat widget can be injected into: http://metis.lti.cs.cmu.edu:7770/"
echo "📋 Use the injection tool at: shopping_assistant/inject_chat.html"
echo ""

# Set AWS environment variables
export AWS_PROFILE=yuxuanlu
export AWS_DEFAULT_REGION=us-east-1

echo "✅ AWS Profile: $AWS_PROFILE"
echo "✅ AWS Region: $AWS_DEFAULT_REGION"
echo ""

# Start the chat server
python shopping_assistant/run_chat_server.py 