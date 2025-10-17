# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import logging
import boto3
import asyncio
import uuid
import json
from click.core import V
from quart import Quart, request, jsonify
from quart_cors import cors
from botocore.exceptions import ClientError
# from prompts.system_prompt import SYSTEM_PROMPT
from shopping_assistant.prompts.system_prompt import SYSTEM_PROMPT
# from tool_config import TOOL_CONFIG
from shopping_assistant.tool_config import TOOL_CONFIG
from shopping_assistant.config import get_model_id, get_temperature, get_top_k, get_server_port, get_server_host

from rl_web_agent.env import WebAgentEnv
import hydra
from hydra import initialize, compose
import threading
from datetime import datetime
import time
import sys
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
# add a file logger
file_logger = logging.FileHandler('./shopping_assistant.log')
file_logger.setLevel(logging.INFO)
file_logger.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_logger)
logging.basicConfig(level=logging.INFO)

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

        # self.model_id = "arn:aws:bedrock:us-east-1:248189905876:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        self.model_id = get_model_id()
        # self.model_id = "arn:aws:bedrock:us-east-1:561287527800:inference-profile/us.anthropic.claude-3-haiku-20240307-v1:0"
        self.system_prompts = [{"text": SYSTEM_PROMPT}]
        self.tool_config = TOOL_CONFIG
        self._lock = threading.Lock()
        self.current_url = None
        print(f"Session initialized with model {self.model_id}")

    async def search(self, query: str) -> str:
        """Search for products using Magento REST API. Returns first 50 products with core info only."""
        import aiohttp
        import urllib.parse
        
        # API endpoint
        base_url = "http://52.91.223.130:7770/rest/V1/products"
        
        # Encode query with wildcards for LIKE search
        search_value = f"%{query}%"
        
        # Build query parameters
        params = {
            "searchCriteria[filter_groups][0][filters][0][field]": "name",
            "searchCriteria[filter_groups][0][filters][0][value]": search_value,
            "searchCriteria[filter_groups][0][filters][0][condition_type]": "like",
            "searchCriteria[pageSize]": "50",
            "fields": "items[id,name,sku,price,media_gallery_entries,custom_attributes]"
        }
        
        # Authorization header
        headers = {
            "Authorization": "Bearer eyJraWQiOiIxIiwiYWxnIjoiSFMyNTYifQ.eyJ1aWQiOjEsInV0eXBpZCI6MiwiaWF0IjoxNzYwNzM4MzQ1LCJleHAiOjE3NjA3NDE5NDV9._5NL8xJMF56gN_rdaoc7hxpSuofOihIcWHnLdoFaecY"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(base_url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Search API returned {len(data['items'])} products for query: {query}")
                        
                        # Extract only essential product information
                        simplified_products = []
                        for item in data["items"]:
                            # Extract key attributes from custom_attributes
                            attrs = {}
                            for attr in item.get("custom_attributes", []):
                                attr_code = attr["attribute_code"]
                                # Only keep essential attributes
                                if attr_code in ["description", "short_description", "url_key", "category_ids", "color", "size"]:
                                    attrs[attr_code] = attr["value"]
                            
                            # Extract first image URL from media_gallery_entries
                            image_url = None
                            media_entries = item.get("media_gallery_entries", [])
                            if media_entries and len(media_entries) > 0:
                                first_image = media_entries[0]
                                image_file = first_image.get("file", "")
                                if image_file:
                                    image_url = f"http://52.91.223.130:7770/media/catalog/product{image_file}"
                            
                            # Build simplified product object
                            product = {
                                "id": item["id"],
                                "name": item["name"],
                                "sku": item["sku"],
                                "price": item.get("price", 0),
                                "url": f"http://52.91.223.130:7770/{attrs.get('url_key', '')}.html" if "url_key" in attrs else None,
                                "image_url": image_url
                            }
                            
                            # Add optional attributes if present
                            if "description" in attrs:
                                product["description"] = attrs["description"][:500]  # Limit description length
                            if "color" in attrs:
                                product["color"] = attrs["color"]
                            if "size" in attrs:
                                product["size"] = attrs["size"]
                                
                            simplified_products.append(product)
                        
                        result = {
                            "total_count": len(simplified_products),
                            "products": simplified_products
                        }
                        
                        result_json = json.dumps(result, indent=2)
                        logger.info(f"Simplified search result: {len(result_json)} characters")
                        return result_json
                    else:
                        error_text = await response.text()
                        logger.error(f"Search API error {response.status}: {error_text}")
                        return f"Error: API returned status {response.status}"
        except Exception as e:
            logger.error(f"Error calling search API: {e}")
            return f"Error calling search API: {str(e)}"

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

    

    async def generate_conversation_stream(self, user_message: str):
        """
        Streaming version that yields chunks to the client as they arrive.
        Yields dict chunks: {"type": "text", "content": "..."} or {"type": "tool", "name": "search"}
        """
        _function_start_ms = time.perf_counter()
        # Add user message to conversation
        user_msg = {
            "role": "user",
            "content": [{"text": user_message}],
            "createdAt": _now_iso()
        }
        with self._lock:
            self.messages.append(user_msg)

        with self._lock:
            if self.current_url:
                self.messages.append({
                    "role": "user",
                    "content": [{"text": "The current url the user is on is: " + self.current_url}],
                    "createdAt": _now_iso(),
                    "hidden": True
                })

        temperature = get_temperature()
        top_k = get_top_k()

        inference_config = {"temperature": temperature}
        additional_model_fields = {"top_k": top_k}

        try:
            _llm_start = time.perf_counter()
            sanitized_messages = _normalize_tool_inputs(self.messages)
            logger.info(f"sanitized_messages: {sanitized_messages}")
            response = self.bedrock_client.converse_stream(
                modelId=self.model_id,
                messages=sanitized_messages,
                system=self.system_prompts,
                inferenceConfig=inference_config,
                additionalModelRequestFields=additional_model_fields,
                toolConfig=self.tool_config,
            )
            _llm_elapsed_ms = (time.perf_counter() - _llm_start) * 1000.0
            logger.info(f"[TIMING] LLM converse (initial) took {_llm_elapsed_ms:.2f} ms")

            # Process streaming response and yield chunks
            output_message = {"role": "assistant", "content": []}
            stop_reason = None

            for chunk in response["stream"]:
                if "contentBlockStart" in chunk:
                    content_block = chunk["contentBlockStart"]["start"]
                    if "toolUse" in content_block:
                        output_message["content"].append({"toolUse": content_block["toolUse"]})
                elif "contentBlockDelta" in chunk:
                    delta = chunk["contentBlockDelta"]["delta"]
                    if "text" in delta:
                        # Find or create text block
                        if not output_message["content"] or "text" not in output_message["content"][-1]:
                            output_message["content"].append({"text": ""})
                        output_message["content"][-1]["text"] += delta["text"]
                        # YIELD TEXT CHUNK TO CLIENT
                        yield {"type": "text", "content": delta["text"]}
                    elif "toolUse" in delta:
                        # Update last toolUse block
                        if output_message["content"] and "toolUse" in output_message["content"][-1]:
                            if "input" not in output_message["content"][-1]["toolUse"]:
                                output_message["content"][-1]["toolUse"]["input"] = ""
                            output_message["content"][-1]["toolUse"]["input"] += delta["toolUse"]["input"]
                elif "messageStop" in chunk:
                    stop_reason = chunk["messageStop"]["stopReason"]
                    # json.loads all tool input if there is any
                    for content in output_message["content"]:
                        if "toolUse" in content:
                            inp = content["toolUse"]["input"]
                            if isinstance(inp, str):
                                try:
                                    content["toolUse"]["input"] = json.loads(inp)
                                except Exception:
                                    content["toolUse"]["input"] = {}

            output_message['createdAt'] = _now_iso()
            with self._lock:
                self.messages.append(output_message)
        except Exception as e:
            _llm_elapsed_ms = (time.perf_counter() - _llm_start) * 1000.0
            logger.error(f"[TIMING] LLM converse (initial) failed after {_llm_elapsed_ms:.2f} ms: {e}")
            error_message = {
                "role": "assistant",
                "content": [{"text": f"I encountered an error: {str(e)}"}]
            }
            self.messages.append(error_message)
            yield {"type": "error", "content": str(e)}
            return

        # Handle tool use loop
        while stop_reason == 'tool_use':
            tool_requests = output_message['content']
            tool_result_contents = []

            for tool_request in tool_requests:
                if 'toolUse' in tool_request:
                    logger.info(f"tool_request: {tool_request}")
                    tool = tool_request['toolUse']
                    tool_name = tool['name']
                    tool_input = tool['input']
                    tool_use_id = tool['toolUseId']
                    logger.info(f"🛠️ Tool used: {tool_name} with input {tool_input}")
                    
                    # Notify client about tool use
                    yield {"type": "tool_use", "tool": tool_name, "input": tool_input}

                    # Handle async tool calls
                    try:
                        _tool_start = time.perf_counter()
                        if tool_name == 'search':
                            result_text = await self.search(tool_input['query'])
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        elif tool_name == 'visit_product':
                            logger.info(f"[TOOL] visit_product input: {tool_input}")
                            result_text = await self.visit_product(tool_input['product_url'])
                            logger.info(f"[TOOL] visit_product html_len={len(result_text or '')}")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        else:
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": f"Unknown tool: {tool_name}"}],
                                "status": "error"
                            }
                    except Exception as e:
                        import traceback
                        logger.error(f"Error executing tool {tool_name}: {traceback.format_exc()}")
                        logger.error(f"Error executing tool {tool_name}: {e}")
                        tool_result = {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"Error executing tool {tool_name}: {str(e)}"}],
                            "status": "error"
                        }
                    finally:
                        try:
                            _tool_elapsed_ms = (time.perf_counter() - _tool_start) * 1000.0
                            logger.info(f"[TIMING] Tool '{tool_name}' took {_tool_elapsed_ms:.2f} ms")
                        except Exception:
                            pass

                    tool_result_contents.append({"toolResult": tool_result})
                    # Notify client tool is complete
                    yield {"type": "tool_complete", "tool": tool_name}

            if tool_result_contents:
                # Add tool results to messages
                self.messages.append({
                    "role": "user",
                    "content": tool_result_contents
                })

                # Follow-up model call after tools
                try:
                    _llm_follow_start = time.perf_counter()
                    sanitized_messages = _normalize_tool_inputs(self.messages)
                    
                    # Log full message details for debugging
                    logger.info(f"[DEBUG] Total messages count: {len(sanitized_messages)}")
                    total_chars = 0
                    for idx, msg in enumerate(sanitized_messages):
                        msg_chars = len(json.dumps(msg, ensure_ascii=False))
                        total_chars += msg_chars
                        logger.info(f"[DEBUG] Message {idx} - role={msg['role']}, chars={msg_chars}, content_blocks={len(msg.get('content', []))}")
                        for cidx, content in enumerate(msg.get('content', [])):
                            if 'text' in content:
                                text_len = len(content['text'])
                                logger.info(f"[DEBUG]   Content[{cidx}] text length: {text_len}")
                                logger.info(f"[DEBUG]   Content[{cidx}] text preview: {content['text'][:500]}")
                            elif 'toolResult' in content:
                                tool_result = content['toolResult']
                                result_text = json.dumps(tool_result, ensure_ascii=False)
                                logger.info(f"[DEBUG]   Content[{cidx}] toolResult length: {len(result_text)}")
                                logger.info(f"[DEBUG]   Content[{cidx}] toolResult preview: {result_text[:500]}")
                            elif 'toolUse' in content:
                                tool_use = content['toolUse']
                                logger.info(f"[DEBUG]   Content[{cidx}] toolUse: {tool_use['name']}")
                    logger.info(f"[DEBUG] Total characters in all messages: {total_chars}")
                    
                    response = self.bedrock_client.converse_stream(
                        modelId=self.model_id,
                        messages=sanitized_messages,
                        system=self.system_prompts,
                        inferenceConfig=inference_config,
                        additionalModelRequestFields=additional_model_fields,
                        toolConfig=self.tool_config
                    )
                    _llm_follow_elapsed_ms = (time.perf_counter() - _llm_follow_start) * 1000.0
                    logger.info(f"[TIMING] LLM converse (after tools) took {_llm_follow_elapsed_ms:.2f} ms")
                    
                    # Process streaming response
                    output_message = {"role": "assistant", "content": []}
                    stop_reason = None

                    for chunk in response["stream"]:
                        if "contentBlockStart" in chunk:
                            content_block = chunk["contentBlockStart"]["start"]
                            if "toolUse" in content_block:
                                output_message["content"].append({"toolUse": content_block["toolUse"]})
                        elif "contentBlockDelta" in chunk:
                            delta = chunk["contentBlockDelta"]["delta"]
                            if "text" in delta:
                                # Find or create text block
                                if not output_message["content"] or "text" not in output_message["content"][-1]:
                                    output_message["content"].append({"text": ""})
                                output_message["content"][-1]["text"] += delta["text"]
                                # YIELD TEXT CHUNK TO CLIENT
                                yield {"type": "text", "content": delta["text"]}
                            elif "toolUse" in delta:
                                # Update last toolUse block
                                if output_message["content"] and "toolUse" in output_message["content"][-1]:
                                    if "input" not in output_message["content"][-1]["toolUse"]:
                                        output_message["content"][-1]["toolUse"]["input"] = ""
                                    output_message["content"][-1]["toolUse"]["input"] += delta["toolUse"]["input"]
                        elif "messageStop" in chunk:
                            stop_reason = chunk["messageStop"]["stopReason"]
                            # json.loads all tool input if there is any
                            for content in output_message["content"]:
                                if "toolUse" in content:
                                    inp = content["toolUse"]["input"]
                                    if isinstance(inp, str):
                                        try:
                                            content["toolUse"]["input"] = json.loads(inp)
                                        except Exception:
                                            content["toolUse"]["input"] = {}

                    logger.info(f"output_message: {output_message}")
                    self.messages.append(output_message)
                except Exception as e:
                    _llm_follow_elapsed_ms = (time.perf_counter() - _llm_follow_start) * 1000.0
                    logger.error(f"[TIMING] LLM converse (after tools) failed after {_llm_follow_elapsed_ms:.2f} ms: {e}")
                    error_message = {
                        "role": "assistant",
                        "content": [{"text": f"I encountered an error processing the tool results: {str(e)}"}]
                    }
                    self.messages.append(error_message)
                    yield {"type": "error", "content": str(e)}
                    return
            else:
                break

        # remove historical tool use and tool result from messages
        new_messages = []
        for m in self.messages:
            if m['role'] == 'assistant':
                should_remove = False
                for c in m['content']:
                    if 'toolUse' in c:
                        should_remove = True
                        break
                if not should_remove:
                    new_messages.append(m)
            if m['role'] == 'user':
                should_remove = False
                for c in m['content']:  
                    if 'toolResult' in c:
                        should_remove = True
                        break
                if not should_remove:
                    new_messages.append(m)
        print(f"new_messages: {new_messages}")
        self.messages = new_messages
        
        _function_elapsed_ms = (time.perf_counter() - _function_start_ms) * 1000.0
        logger.info(f"[TIMING] generate_conversation_stream total {_function_elapsed_ms:.2f} ms")
        
        # Signal completion
        yield {"type": "done"}

    async def generate_conversation_async(self, user_message: str):
        """
        Sends messages to a model with async tool handling.
        """
        _function_start_ms = time.perf_counter()
        # Add user message to conversation
        user_msg = {
            "role": "user",
            "content": [{"text": user_message}],
            "createdAt": _now_iso()
        }
        with self._lock:
            self.messages.append(user_msg)

        with self._lock:
            if self.current_url:
                self.messages.append({
                    "role": "user",
                    "content": [{"text": "The current url the user is on is: " + self.current_url}],
                    "createdAt": _now_iso(),
                    "hidden": True
                })
        #     except Exception as e:
        #         logger.warning(f"[PREVISIT] failed: {e}")

        temperature = get_temperature()
        top_k = get_top_k()

        inference_config = {"temperature": temperature}
        additional_model_fields = {"top_k": top_k}

        try:
            _llm_start = time.perf_counter()
            sanitized_messages = _normalize_tool_inputs(self.messages)
            logger.info(f"sanitized_messages: {sanitized_messages}")
            logger.info(f"system_prompts: {self.system_prompts}")
            response = self.bedrock_client.converse_stream(
                modelId=self.model_id,
                messages=sanitized_messages,
                system=self.system_prompts,
                inferenceConfig=inference_config,
                additionalModelRequestFields=additional_model_fields,
                toolConfig=self.tool_config,
                # performanceConfig={
                #     "latency": "optimized"
                # }
            )
            _llm_elapsed_ms = (time.perf_counter() - _llm_start) * 1000.0
            logger.info(f"[TIMING] LLM converse (initial) took {_llm_elapsed_ms:.2f} ms")

            # Process streaming response
            output_message = {"role": "assistant", "content": []}
            stop_reason = None

            for chunk in response["stream"]:
                if "contentBlockStart" in chunk:
                    content_block = chunk["contentBlockStart"]["start"]
                    if "toolUse" in content_block:
                        output_message["content"].append({"toolUse": content_block["toolUse"]})
                elif "contentBlockDelta" in chunk:
                    delta = chunk["contentBlockDelta"]["delta"]
                    if "text" in delta:
                        # Find or create text block
                        if not output_message["content"] or "text" not in output_message["content"][-1]:
                            output_message["content"].append({"text": ""})
                        output_message["content"][-1]["text"] += delta["text"]
                    elif "toolUse" in delta:
                        # Update last toolUse block
                        if output_message["content"] and "toolUse" in output_message["content"][-1]:
                            if "input" not in output_message["content"][-1]["toolUse"]:
                                output_message["content"][-1]["toolUse"]["input"] = ""
                            output_message["content"][-1]["toolUse"]["input"] += delta["toolUse"]["input"]
                elif "messageStop" in chunk:
                    stop_reason = chunk["messageStop"]["stopReason"]
                    # json.loads all tool input if there is any
                    for content in output_message["content"]:
                        if "toolUse" in content:
                            inp = content["toolUse"]["input"]
                            if isinstance(inp, str):
                                try:
                                    content["toolUse"]["input"] = json.loads(inp)
                                except Exception:
                                    content["toolUse"]["input"] = {}

            response['stopReason'] = stop_reason
            output_message['createdAt'] = _now_iso()
            with self._lock:
                self.messages.append(output_message)
        except Exception as e:
            _llm_elapsed_ms = (time.perf_counter() - _llm_start) * 1000.0
            logger.error(f"[TIMING] LLM converse (initial) failed after {_llm_elapsed_ms:.2f} ms: {e}")
            # Create a fallback response
            output_message = {
                "role": "assistant",
                "content": [{"text": f"I encountered an error: {str(e)}"}]
            }
            self.messages.append(output_message)
            _function_elapsed_ms = (time.perf_counter() - _function_start_ms) * 1000.0
            logger.info(f"[TIMING] generate_conversation_async total {_function_elapsed_ms:.2f} ms (early return)")
            return output_message

        while response['stopReason'] == 'tool_use':
            tool_requests = output_message['content']
            tool_result_contents = []

            for tool_request in tool_requests:
                if 'toolUse' in tool_request:
                    tool = tool_request['toolUse']
                    tool_name = tool['name']
                    tool_input = tool['input']
                    tool_use_id = tool['toolUseId']
                    logger.info(f"🛠️ Tool used: {tool_name} with input {tool_input}")

                    # Handle async tool calls
                    try:
                        _tool_start = time.perf_counter()
                        if tool_name == 'search':
                            result_text = await self.search(tool_input['query'])
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        elif tool_name == 'visit_product':
                            logger.info(f"[TOOL] visit_product input: {tool_input}")
                            result_text = await self.visit_product(tool_input['product_url'])
                            logger.info(f"[TOOL] visit_product html_len={len(result_text or '')}")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        
                        else:
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
                    finally:
                        try:
                            _tool_elapsed_ms = (time.perf_counter() - _tool_start) * 1000.0
                            logger.info(f"[TIMING] Tool '{tool_name}' took {_tool_elapsed_ms:.2f} ms")
                        except Exception:
                            pass

                    tool_result_contents.append({"toolResult": tool_result})

            if tool_result_contents:
                # Add a single user message containing ALL toolResult blocks
                self.messages.append({
                    "role": "user",
                    "content": tool_result_contents
                })

                # Single follow-up model call after providing all tool results
                try:
                    _llm_follow_start = time.perf_counter()
                    sanitized_messages = _normalize_tool_inputs(self.messages)
                    
                    # Log full message details for debugging
                    logger.info(f"[DEBUG] Total messages count: {len(sanitized_messages)}")
                    total_chars = 0
                    for idx, msg in enumerate(sanitized_messages):
                        msg_chars = len(json.dumps(msg, ensure_ascii=False))
                        total_chars += msg_chars
                        logger.info(f"[DEBUG] Message {idx} - role={msg['role']}, chars={msg_chars}, content_blocks={len(msg.get('content', []))}")
                        for cidx, content in enumerate(msg.get('content', [])):
                            if 'text' in content:
                                text_len = len(content['text'])
                                logger.info(f"[DEBUG]   Content[{cidx}] text length: {text_len}")
                                logger.info(f"[DEBUG]   Content[{cidx}] text preview: {content['text'][:500]}")
                            elif 'toolResult' in content:
                                tool_result = content['toolResult']
                                result_text = json.dumps(tool_result, ensure_ascii=False)
                                logger.info(f"[DEBUG]   Content[{cidx}] toolResult length: {len(result_text)}")
                                logger.info(f"[DEBUG]   Content[{cidx}] toolResult preview: {result_text[:500]}")
                            elif 'toolUse' in content:
                                tool_use = content['toolUse']
                                logger.info(f"[DEBUG]   Content[{cidx}] toolUse: {tool_use['name']}")
                    logger.info(f"[DEBUG] Total characters in all messages: {total_chars}")
                    
                    logger.info(f"sanitized_messages: {sanitized_messages}")
                    logger.info(f"system_prompts: {self.system_prompts}")
                    response = self.bedrock_client.converse_stream(
                        modelId=self.model_id,
                        messages=sanitized_messages,
                        system=self.system_prompts,
                        inferenceConfig=inference_config,
                        additionalModelRequestFields=additional_model_fields,
                        toolConfig=self.tool_config
                    )
                    _llm_follow_elapsed_ms = (time.perf_counter() - _llm_follow_start) * 1000.0
                    logger.info(f"[TIMING] LLM converse (after tools) took {_llm_follow_elapsed_ms:.2f} ms")
                    
                    # Process streaming response
                    output_message = {"role": "assistant", "content": []}
                    stop_reason = None

                    for chunk in response["stream"]:
                        if "contentBlockStart" in chunk:
                            content_block = chunk["contentBlockStart"]["start"]
                            if "toolUse" in content_block:
                                output_message["content"].append({"toolUse": content_block["toolUse"]})
                        elif "contentBlockDelta" in chunk:
                            delta = chunk["contentBlockDelta"]["delta"]
                            if "text" in delta:
                                # Find or create text block
                                if not output_message["content"] or "text" not in output_message["content"][-1]:
                                    output_message["content"].append({"text": ""})
                                output_message["content"][-1]["text"] += delta["text"]
                            elif "toolUse" in delta:
                                # Update last toolUse block
                                if output_message["content"] and "toolUse" in output_message["content"][-1]:
                                    if "input" not in output_message["content"][-1]["toolUse"]:
                                        output_message["content"][-1]["toolUse"]["input"] = ""
                                    output_message["content"][-1]["toolUse"]["input"] += delta["toolUse"]["input"]
                        elif "messageStop" in chunk:
                            stop_reason = chunk["messageStop"]["stopReason"]
                            # json.loads all tool input if there is any
                            for content in output_message["content"]:
                                if "toolUse" in content:
                                    inp = content["toolUse"]["input"]
                                    if isinstance(inp, str):
                                        try:
                                            content["toolUse"]["input"] = json.loads(inp)
                                        except Exception:
                                            content["toolUse"]["input"] = {}

                    response['stopReason'] = stop_reason
                    logger.info(f"output_message: {output_message}")
                    self.messages.append(output_message)
                except Exception as e:
                    _llm_follow_elapsed_ms = (time.perf_counter() - _llm_follow_start) * 1000.0
                    logger.error(f"[TIMING] LLM converse (after tools) failed after {_llm_follow_elapsed_ms:.2f} ms: {e}")
                    output_message = {
                        "role": "assistant",
                        "content": [{"text": f"I encountered an error processing the tool results: {str(e)}"}]
                    }
                    self.messages.append(output_message)
        # remove historical tool use and tool result from messages
        new_messages = []
        for m in self.messages:
            if m['role'] == 'assistant':
                should_remove = False
                for c in m['content']:
                    if 'toolUse' in c:
                        should_remove = True
                        break
                if not should_remove:
                    new_messages.append(m)
            if m['role'] == 'user':
                should_remove = False
                for c in m['content']:  
                    if 'toolResult' in c:
                        should_remove = True
                        break
                if not should_remove:
                    new_messages.append(m)
        print(f"new_messages: {new_messages}")
        self.messages = new_messages
        _function_elapsed_ms = (time.perf_counter() - _function_start_ms) * 1000.0
        logger.info(f"[TIMING] generate_conversation_async total {_function_elapsed_ms:.2f} ms")
        logger.info(f"output_message: {output_message}")
        return output_message

    async def initialize_bedrock(self):
        """Initialize Bedrock client for the session."""
        with self._lock:
            if self.bedrock_client is None:
                session = boto3.Session(region_name='us-east-1')
                self.bedrock_client = session.client('bedrock-runtime')
                logger.info(f"Bedrock client initialized for session {self.session_id}")

def create_session():
    """Create a new session and return session ID."""
    session_id = str(uuid.uuid4())
    
    with session_lock:
        sessions[session_id] = Session(session_id)
    
    logger.info(f"Created new session: {session_id}")
    return session_id

def get_session(session_id: str) -> Session:
    """Get session by ID."""
    with session_lock:
        return sessions.get(session_id)

def cleanup_session(session_id: str):
    """Clean up a session."""
    with session_lock:
        if session_id in sessions:
            del sessions[session_id]
            logger.info(f"Cleaned up session: {session_id}")

def _now_iso():
    return datetime.now().isoformat()

def _normalize_tool_inputs(messages):
    """Ensure all assistant toolUse.input fields are JSON objects (not strings)."""
    norm = []
    for m in messages:
        m2 = {"role": m["role"], "content": []}
        for c in m.get("content", []):
            if "toolUse" in c:
                tu = dict(c["toolUse"])
                inp = tu.get("input")
                if isinstance(inp, str):
                    try:
                        tu["input"] = json.loads(inp)
                    except Exception:
                        tu["input"] = {}
                elif inp is None:
                    tu["input"] = {}
                c = {"toolUse": tu}
            m2["content"].append(c)
        norm.append(m2)
    return norm

def _content_to_text(content_blocks):
    # Includes toolUse/toolResult
    parts = []
    for item in content_blocks or []:
        if not isinstance(item, dict):
            continue
        if "text" in item:
            parts.append(item["text"])
        elif "toolUse" in item:
            tool = item["toolUse"]
            name = tool["name"]
            tool_input = tool["input"]
            input_str = json.dumps(tool_input, ensure_ascii=False)
            parts.append(f"[toolUse:{name}] input={input_str}")
        elif "toolResult" in item:
            tool_result = item["toolResult"]
            tool_use_id = tool_result["toolUseId"]
            status_suffix = f" status={tool_result['status']}" if "status" in tool_result else ""
            texts = []
            for c in tool_result["content"]:
                if isinstance(c, dict) and "text" in c:
                    texts.append(c["text"])
            result_text = "\n".join(texts)
            parts.append(f"[toolResult:{tool_use_id}{status_suffix}] {result_text}")
    return "\n".join([p for p in parts if p])

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

@app.route('/chat-stream', methods=['GET'])
async def chat_stream_api():
    """API endpoint to send a message and stream assistant response."""
    try:
        data = request.args
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
        
        from urllib.parse import urlparse
        
        current_url = data.get('current_url')
        logger.info(f"Received current_url: {current_url}")

        if current_url:
            try:
                parsed = urlparse(current_url)
                host = parsed.hostname
                allowed = (
                    parsed.scheme in ('http', 'https') and host and (
                        host.endswith('metis.lti.cs.cmu.edu') or host == '52.91.223.130'
                    )
                )
                if allowed:
                    session.current_url = current_url
                    logger.info(f"[SESSION] {session_id} current_url set to {session.current_url}")
                else:
                    logger.warning(f"[SESSION] rejected current_url={current_url} (scheme={parsed.scheme}, host={host})")
            except Exception as e:
                logger.warning(f"[SESSION] invalid current_url={current_url}, err={e}")
            
        # Initialize Bedrock client
        await session.initialize_bedrock()
        
        # Stream the conversation
        async def generate():
            try:
                async for chunk in session.generate_conversation_stream(message):
                    # Send each chunk as Server-Sent Event
                    yield f"data: {json.dumps(chunk)}\n\n"
                    # await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error in streaming: {e}")
                import traceback
                traceback.print_exc()
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        
        return generate(), {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Error in chat stream API: {e}")
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
        
        from urllib.parse import urlparse
        
        current_url = data.get('current_url')
        logger.info(f"Received current_url: {current_url}")

        if current_url:
            try:
                parsed = urlparse(current_url)
                host = parsed.hostname
                allowed = (
                    parsed.scheme in ('http', 'https') and host and (
                        host.endswith('metis.lti.cs.cmu.edu') or host == '52.91.223.130'
                    )
                )
                if allowed:
                    session.current_url = current_url
                    logger.info(f"[SESSION] {session_id} current_url set to {session.current_url}")
                else:
                    logger.warning(f"[SESSION] rejected current_url={current_url} (scheme={parsed.scheme}, host={host})")
            except Exception as e:
                logger.warning(f"[SESSION] invalid current_url={current_url}, err={e}")
            
        # Initialize Bedrock client
        await session.initialize_bedrock()
        
        # Generate conversation
        ai_message = await session.generate_conversation_async(message)
        
        
        response_text = _content_to_text(ai_message.get("content", []))

        
        s = get_session(session_id)
        flat = []
        with s._lock:
            for m in s.messages:
                flat.append({
                    "role": m.get("role", ""),
                    "text": _content_to_text(m.get("content", [])),
                    "createdAt": m.get("createdAt", _now_iso()),
                    "hidden": m.get("hidden", False)

                })

        return jsonify({
            "success": True,
            "response": response_text,
            "messages": flat
        }), 200
        
    except Exception as e:
        # print traceback
        import traceback
        traceback.print_exc()
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

@app.route('/sessions/<session_id>/messages', methods=['GET'])
async def get_messages_api(session_id):
    s = get_session(session_id)
    if not s:
        return jsonify({"success": False, "error": "Session not found"}), 404


    flat = []
    with s._lock:
        for m in s.messages:
            flat.append({
                "role": m.get("role", ""),
                "text": _content_to_text(m.get("content", [])),
                "createdAt": m.get("createdAt", _now_iso()),
                "hidden": m.get("hidden", False)
            })
    return jsonify({"success": True, "messages": flat}), 200


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
    app.run(host=get_server_host(), port=get_server_port(), debug=False) 