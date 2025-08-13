#!/usr/bin/env python3
"""
Example client for the shopping assistant Flask server.
"""

import requests
import json
import time

# Server configuration
SERVER_URL = "http://localhost:5000"

def create_session():
    """Create a new session."""
    response = requests.post(f"{SERVER_URL}/create-session")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Session created: {data['session_id']}")
        return data['session_id']
    else:
        print(f"❌ Failed to create session: {response.text}")
        return None

def send_message(session_id, message):
    """Send a message to the chat API."""
    payload = {
        "session_id": session_id,
        "message": message
    }
    
    print(f"📤 Sending message: {message}")
    response = requests.post(f"{SERVER_URL}/chat", json=payload)
    if response.status_code == 200:
        data = response.json()
        print(f"🤖 Assistant: {data['response']}")
        return data['response']
    else:
        print(f"❌ Failed to send message: {response.text}")
        return None

def main():
    """Main function to demonstrate the API usage."""
    print("🛒 Shopping Assistant Flask Server Demo")
    print("=" * 50)
    
    # Create a session
    print("\n📝 Creating session...")
    session_id = create_session()
    if not session_id:
        return
    
    # Example conversation
    messages = [
        "Hello! I'm looking for a laptop.",
        "Can you search for laptops under $1000?",
        "What are the best options you found?",
        "Thank you for your help!"
    ]
    
    for i, message in enumerate(messages, 1):
        print(f"\n--- Message {i} ---")
        response = send_message(session_id, message)
        if not response:
            break
        
        # Add a small delay between messages
        if i < len(messages):
            time.sleep(1)
        
if __name__ == "__main__":
    main() 