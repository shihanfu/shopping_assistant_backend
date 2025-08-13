#!/usr/bin/env python3
"""
Launcher script for the Shopping Assistant Chat Server

This script starts the Flask chat server that provides the chat interface
for the shopping assistant widget.
"""

import sys
import os
import logging

# Add the parent directory to the Python path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shopping_assistant.chat_app import app

if __name__ == "__main__":
    print("🛍️ Starting Shopping Assistant Chat Server...")
    print("📡 Server will be available at: http://localhost:5000")
    print("🌐 Chat widget can be injected into: http://metis.lti.cs.cmu.edu:7770/")
    print("📋 Use the injection tool at: shopping_assistant/inject_chat.html")
    print("\n" + "="*60)
    print("Server is running! Press Ctrl+C to stop.")
    print("="*60 + "\n")
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True) 