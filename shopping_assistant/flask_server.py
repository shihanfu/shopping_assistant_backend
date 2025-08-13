# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import logging
import boto3
import asyncio
import uuid
import json
from quart import Quart, request, jsonify
from quart_cors import cors
from botocore.exceptions import ClientError
from prompts.system_prompt import SYSTEM_PROMPT
from tool_config import TOOL_CONFIG
from rl_web_agent.env import WebAgentEnv
import hydra
from hydra import initialize, compose
import threading

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

app = Quart(__name__)
app = cors(app, allow_origin="*")

# Global shared WebAgentEnv instance
global_env = None
env_lock = threading.Lock()

# Global session storage
sessions = {}
session_lock = threading.Lock()

class Session:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.messages = []
        self.bedrock_client = None
        self.model_id = "arn:aws:bedrock:us-east-1:248189905876:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        self.system_prompts = [{"text": SYSTEM_PROMPT}]
        self.tool_config = TOOL_CONFIG
        self._lock = threading.Lock()

    async def search(self, query: str) -> str:
        """Search for products using global WebAgentEnv."""
        global global_env
        if not global_env:
            return "Error: WebAgentEnv not initialized"
        
        try:
            # URL encode the query for the search URL
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            url_template = "http://metis.lti.cs.cmu.edu:7770/catalogsearch/result/?q={query}"
            search_url = url_template.format(query=encoded_query)
            
            # await global_env.step(f'{{"action": "goto_url", "url": "{search_url}"}}')
            print(f"Searching for {query} at {search_url}")
            await global_env.goto_url(search_url)
            print("Goto URL done")
            observation = await global_env.observation()
            
            # Return the HTML content directly as the tool response
            return observation.get("html", "No HTML content available")
            
        except Exception as e:
            logger.error(f"Error in search function: {e}")
            return f"Error during search: {str(e)}"

    async def visit_product(self, product_url: str) -> str:
        """Visit a product page using global WebAgentEnv."""
        global global_env
        if not global_env:
            return "Error: WebAgentEnv not initialized"
        
        try:
            await global_env.step(f'{{"action": "goto_url", "url": "{product_url}"}}')
            observation = await global_env.observation()
            
            # Return the HTML content directly as the tool response
            return observation.get("html", "No HTML content available")
            
        except Exception as e:
            logger.error(f"Error in visit_product function: {e}")
            return f"Error visiting product: {str(e)}"

    async def generate_conversation_async(self, user_message: str):
        """
        Sends messages to a model with async tool handling.
        """
        # Add user message to conversation
        user_msg = {
            "role": "user",
            "content": [{"text": user_message}]
        }
        self.messages.append(user_msg)

        temperature = 0.5
        top_k = 200

        inference_config = {"temperature": temperature}
        additional_model_fields = {"top_k": top_k}

        try:
            response = self.bedrock_client.converse(
                modelId=self.model_id,
                messages=self.messages,
                system=self.system_prompts,
                inferenceConfig=inference_config,
                additionalModelRequestFields=additional_model_fields,
                toolConfig=self.tool_config
            )

            output_message = response['output']['message']
            self.messages.append(output_message)
        except Exception as e:
            logger.error(f"Error in main model call: {e}")
            # Create a fallback response
            output_message = {
                "role": "assistant",
                "content": [{"text": f"I encountered an error: {str(e)}"}]
            }
            self.messages.append(output_message)
            return output_message

        if response['stopReason'] == 'tool_use':
            tool_requests = output_message['content']
            
            for tool_request in tool_requests:
                if 'toolUse' in tool_request:
                    tool = tool_request['toolUse']
                    tool_name = tool['name']
                    tool_input = tool['input']
                    tool_use_id = tool['toolUseId']
                    logger.info(f"🛠️ Tool used: {tool_name} with input {tool_input}")
                    
                    # Handle async tool calls
                    try:
                        if tool_name == 'search':
                            result_text = await self.search(tool_input['query'])
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        elif tool_name == 'visit_product':
                            result_text = await self.visit_product(tool_input['product_url'])
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        else:
                            # fallback: unknown tool
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": f"Unknown tool: {tool_name}"}],
                                "status": "error"
                            }
                    except Exception as e:
                        logger.error(f"Error executing tool {tool_name}: {e}")
                        tool_result = {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"Error executing tool {tool_name}: {str(e)}"}],
                            "status": "error"
                        }
                        
                    # Add tool result back to message list
                    tool_result_message = {
                        "role": "user",
                        "content": [{"toolResult": tool_result}]
                    }
                    self.messages.append(tool_result_message)

                    # Second model call: send tool result to model
                    try:
                        response = self.bedrock_client.converse(
                            modelId=self.model_id,
                            messages=self.messages,
                            system=self.system_prompts,
                            inferenceConfig=inference_config,
                            additionalModelRequestFields=additional_model_fields,
                            toolConfig=self.tool_config
                        )
                        output_message = response['output']['message']
                        self.messages.append(output_message)
                    except Exception as e:
                        logger.error(f"Error in second model call: {e}")
                        # Create a fallback response
                        output_message = {
                            "role": "assistant",
                            "content": [{"text": f"I encountered an error processing the tool result: {str(e)}"}]
                        }
                        self.messages.append(output_message)

        return output_message

    async def initialize_bedrock(self):
        """Initialize Bedrock client for the session."""
        with self._lock:
            if self.bedrock_client is None:
                session = boto3.Session(profile_name='yuxuanlu', region_name='us-east-1')
                self.bedrock_client = session.client('bedrock-runtime')
                logger.info(f"Bedrock client initialized for session {self.session_id}")

def create_session():
    """Create a new session and return session ID."""
    session_id = str(uuid.uuid4())
    
    with session_lock:
        sessions[session_id] = Session(session_id)
    
    logger.info(f"Created new session: {session_id}")
    return session_id

def get_session(session_id: str):
    """Get session by ID."""
    with session_lock:
        return sessions.get(session_id)

def cleanup_session(session_id: str):
    """Clean up a session."""
    with session_lock:
        if session_id in sessions:
            del sessions[session_id]
            logger.info(f"Cleaned up session: {session_id}")

async def setup_global_environment():
    """Initialize the global WebAgentEnv with proper configuration."""
    global global_env
    
    with env_lock:
        if global_env is None:
            # Initialize Hydra context, point to config directory
            with initialize(config_path="../rl_web_agent/conf", version_base=None):
                # Compose the config object with the main config name (no .yaml)
                cfg = compose(config_name="config")
            
            global_env = WebAgentEnv(cfg.environment)
            await global_env.setup({
                "sites": [
                    "shopping"
                ]
            })
            logger.info("Global WebAgentEnv initialized successfully")

async def cleanup_global_environment():
    """Clean up the global WebAgentEnv instance."""
    global global_env
    
    with env_lock:
        if global_env:
            await global_env.close()
            global_env = None
            logger.info("Global WebAgentEnv closed successfully")

@app.route('/create-session', methods=['POST'])
async def create_session_api():
    """API endpoint to create a new session."""
    try:
        if global_env is None:
            await setup_global_environment()
        session_id = create_session()
        return jsonify({
            "success": True,
            "session_id": session_id
        }), 200
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/chat', methods=['POST'])
async def chat_api():
    """API endpoint to send a message and get assistant response."""
    try:
        data = await request.get_json()
        if not data or 'session_id' not in data or 'message' not in data:
            return jsonify({
                "success": False,
                "error": "Missing required fields: session_id and message"
            }), 400
        
        session_id = data['session_id']
        message = data['message']
        
        session = get_session(session_id)
        if not session:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404
        
        # Check if global environment is ready
        if global_env is None:
            return jsonify({
                "success": False,
                "error": "Global environment not initialized"
            }), 503
        
        # Initialize Bedrock client
        await session.initialize_bedrock()
        
        # Generate conversation
        ai_message = await session.generate_conversation_async(message)
        
        # Extract text content from AI message
        response_text = ""
        for content_item in ai_message.get("content", []):
            if "text" in content_item:
                response_text = content_item['text']
                break
        
        return jsonify({
            "success": True,
            "response": response_text
        }), 200
        
    except Exception as e:
        logger.error(f"Error in chat API: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/cleanup-session', methods=['POST'])
async def cleanup_session_api():
    """API endpoint to cleanup a session."""
    try:
        data = await request.get_json()
        if not data or 'session_id' not in data:
            return jsonify({
                "success": False,
                "error": "Missing required field: session_id"
            }), 400
        
        session_id = data['session_id']
        cleanup_session(session_id)
        
        return jsonify({
            "success": True,
            "message": f"Session {session_id} cleaned up successfully"
        }), 200
        
    except Exception as e:
        logger.error(f"Error in cleanup session API: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/health', methods=['GET'])
async def health_check():
    """Health check endpoint."""
    return jsonify({
        "success": True,
        "status": "healthy",
        "active_sessions": len(sessions),
        "global_env_initialized": global_env is not None
    }), 200

if __name__ == '__main__':
    # Start Quart server
    app.run(host='0.0.0.0', port=5000, debug=False) 