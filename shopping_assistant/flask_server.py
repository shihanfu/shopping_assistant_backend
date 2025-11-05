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
from shopping_assistant.config import get_model_id, get_state_model_id, get_temperature, get_top_k, get_server_port, get_server_host, MAGENTO_API_CONFIG

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
        
        # Initialize conversation state
        self.conversation_state = {
            "product_category": None,
            "search_query": None,
            "user_intention": None,  # One of: "product recommendation", "product detail QA", "product comparison"
            "inferred_user_preferences": {
                "usage_scenario": None,
                "budget": None,
                "explicit_preferences": [],
                "implicit_preferences": [],
            },
            "inferred_product_attributes": []  # Array of dimension objects per prompt spec
        }
        
        # Load conversation state update prompt
        import os
        prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "update_conversation_state.txt")
        with open(prompt_path, "r") as f:
            self.state_update_prompt = f.read()
        
        print(f"Session initialized with model {self.model_id}")

    async def get_magento_admin_token(self) -> str:
        """Get a fresh admin token from Magento API."""
        import aiohttp
        
        token_url = f"{MAGENTO_API_CONFIG['base_url']}{MAGENTO_API_CONFIG['token_endpoint']}"
        payload = {
            "username": MAGENTO_API_CONFIG["admin_username"],
            "password": MAGENTO_API_CONFIG["admin_password"]
        }
        
        logger.info(f"[AUTH] Requesting admin token from {token_url}")
        logger.info(f"[AUTH] Using username: {MAGENTO_API_CONFIG['admin_username']}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(token_url, json=payload, headers={"Content-Type": "application/json"}) as response:
                    #logger.info(f"[AUTH] Token request status: {response.status}")
                    #logger.info(f"[AUTH] Token response headers: {dict(response.headers)}")
                    
                    if response.status == 200:
                        token = await response.json()
                        # Remove quotes if present - Magento returns token as string
                        token_str = str(token).strip('"').strip("'")
                        logger.info(f"[AUTH] Successfully obtained admin token (length: {len(token_str)})")
                        return token_str
                    else:
                        error_text = await response.text()
                        #logger.error(f"[AUTH] Failed to get token. Status: {response.status}")
                        logger.error(f"[AUTH] Error response: {error_text}")
                        raise Exception(f"Failed to get admin token: {response.status} - {error_text}")
        except Exception as e:
            #logger.error(f"[AUTH] Exception while getting token: {e}")
            import traceback
            #logger.error(f"[AUTH] Traceback: {traceback.format_exc()}")
            raise

    async def search(self, query: str) -> str:
        """Search for products using Magento REST API. Returns first 50 products with core info only."""
        import aiohttp
        import urllib.parse
        
        # Get fresh admin token
        try:
            token = await self.get_magento_admin_token()
        except Exception as e:
            logger.error(f"[SEARCH] Failed to get admin token: {e}")
            return f"Error: Failed to authenticate with Magento API - {str(e)}"
        
        # API endpoint from config
        base_url = f"{MAGENTO_API_CONFIG['base_url']}{MAGENTO_API_CONFIG['products_endpoint']}"
        
        # Build query parameters
        # Strategy: Use OR logic - each word gets its own filter_group
        # Different filter_groups = OR logic (any word can match)
        query_words = query.strip().split()
        
        params = {}
        
        # Add filters for each word - each in separate filter_group for OR logic
        for idx, word in enumerate(query_words):
            search_value = f"%{word}%"
            params[f"searchCriteria[filter_groups][{idx}][filters][0][field]"] = "name"
            params[f"searchCriteria[filter_groups][{idx}][filters][0][value]"] = search_value
            params[f"searchCriteria[filter_groups][{idx}][filters][0][condition_type]"] = "like"
        
        # Pagination
        params["searchCriteria[pageSize]"] = "50"
        params["searchCriteria[currentPage]"] = "1"
        
        # Authorization header with fresh token
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        logger.info(f"[SEARCH] Starting search for query: '{query}'")
        logger.info(f"[SEARCH] Query words: {query_words}")
        logger.info(f"[SEARCH] Request params: {params}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(base_url, params=params, headers=headers) as response:
                    logger.info(f"[SEARCH] Request URL: {response.url}")
                    logger.info(f"[SEARCH] Response status: {response.status}")

                    
                    if response.status == 200:
                        data = await response.json()
                        total_items = len(data["items"])
                        total_count_available = data.get("total_count", total_items)
                        #logger.info(f"[SEARCH] API returned {total_items} products out of {total_count_available} total matches for query: '{query}'")
                        
                        # Warn if we're not getting the expected page size
                        #if total_count_available > total_items and total_items < 50:
                        #    logger.warning(f"[SEARCH] Expected up to 50 products but only received {total_items}. There are {total_count_available - total_items} more products available.")
                        
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
                                    image_url = f"{MAGENTO_API_CONFIG['base_url']}/media/catalog/product{image_file}"
                            
                            # Build simplified product object
                            product = {
                                "id": item["id"],
                                "name": item["name"],
                                "sku": item["sku"],
                                "price": item.get("price", 0),
                                "url": f"{MAGENTO_API_CONFIG['base_url']}/{attrs.get('url_key', '')}.html" if "url_key" in attrs else None,
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
                        
                        # Log search results summary
                        #logger.info(f"[SEARCH] Processed {len(simplified_products)} products successfully")
                        #product_names = [p["name"] for p in simplified_products[:5]]  # Log first 5 product names
                        #if len(simplified_products) > 5:
                        #    logger.info(f"[SEARCH] Sample products: {', '.join(product_names)} ... and {len(simplified_products) - 5} more")
                        #else:
                        #    logger.info(f"[SEARCH] Products: {', '.join(product_names)}")
                        
                        result = {
                            "total_count": len(simplified_products),
                            "total_available": total_count_available,
                            "products": simplified_products
                        }
                        
                        result_json = json.dumps(result, indent=2)
                        logger.info(f"[SEARCH] Found {len(simplified_products)} products for query: '{query}'")
                        return result_json
                    else:
                        error_text = await response.text()
                        logger.error(f"[SEARCH] API error - Status: {response.status}")
                        logger.error(f"[SEARCH] Error response: {error_text[:500]}")
                        
                        # Check if it's an authorization error
                        if response.status == 401 or "isn't authorized" in error_text:
                            logger.error(f"[SEARCH] Authorization failed - token may be invalid or expired")
                            logger.error(f"[SEARCH] This likely means the admin user doesn't have permission to access product catalog")
                            return f"Error: Authorization failed - admin token doesn't have permission to access products. Status: {response.status}"
                        
                        return f"Error: API returned status {response.status} - {error_text[:200]}"
        except Exception as e:
            logger.error(f"[SEARCH] Exception calling search API: {e}")
            import traceback
            logger.error(f"[SEARCH] Traceback: {traceback.format_exc()}")
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

    async def update_conversation_state(self):
        """Update conversation state by analyzing recent conversation history."""
        import time
        start_time = time.perf_counter()
        try:
            # Get all messages from current session
            recent_messages = self.messages
            
            # Format conversation history for the LLM
            format_start = time.perf_counter()
            conversation_text = ""
            for msg in recent_messages:
                role = msg["role"]
                content_parts = msg.get("content", [])
                for part in content_parts:
                    if "text" in part:
                        conversation_text += f"{role.upper()}: {part['text']}\n\n"
            
            # Construct the prompt
            full_prompt = f"{self.state_update_prompt}\n\n# Conversation History\n{conversation_text}\n\n# Current State\n{json.dumps(self.conversation_state, indent=2)}"
            format_time = time.perf_counter() - format_start
            logger.info(f"[STATE_UPDATE_TIMING] Formatting conversation history took {format_time:.3f}s")
            
            # Call LLM to extract state (using faster Haiku model)
            llm_start = time.perf_counter()
            state_model_id = get_state_model_id()
            response = self.bedrock_client.converse(
                modelId=state_model_id,
                messages=[{"role": "user", "content": [{"text": full_prompt}]}],
                inferenceConfig={"temperature": 0.0}  # Use low temperature for consistent extraction
            )
            llm_time = time.perf_counter() - llm_start
            logger.info(f"[STATE_UPDATE_TIMING] LLM API call (model: {state_model_id.split('/')[-1]}) took {llm_time:.3f}s")
            
            # Parse the response
            parse_start = time.perf_counter()
            assistant_content = response["output"]["message"]["content"]
            if assistant_content and len(assistant_content) > 0 and "text" in assistant_content[0]:
                response_text = assistant_content[0]["text"].strip()
                
                # Extract JSON from response (handle potential markdown code blocks)
                json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(1)
                elif response_text.startswith('```') and response_text.endswith('```'):
                    response_text = response_text[3:-3].strip()
                    if response_text.startswith('json'):
                        response_text = response_text[4:].strip()
                
                # Parse and update state
                new_state = json.loads(response_text)
                with self._lock:
                    self.conversation_state = new_state
                
                parse_time = time.perf_counter() - parse_start
                logger.info(f"[STATE_UPDATE_TIMING] Parsing and updating state took {parse_time:.3f}s")
                
                # Log only the final conversation state
                logger.info(f"[CONVERSATION_STATE] {json.dumps(self.conversation_state, indent=2)}")
                
                # Log total timing
                elapsed_time = time.perf_counter() - start_time
                logger.info(f"[STATE_UPDATE_TIMING] Total conversation state update completed in {elapsed_time:.3f}s")
                
        except json.JSONDecodeError as e:
            elapsed_time = time.perf_counter() - start_time
            logger.error(f"[STATE_UPDATE_TIMING] Failed after {elapsed_time:.3f}s")
            logger.error(f"[STATE_UPDATE] Failed to parse LLM response as JSON: {e}")
            logger.error(f"[STATE_UPDATE] Response text: {response_text}")
        except Exception as e:
            elapsed_time = time.perf_counter() - start_time
            logger.error(f"[STATE_UPDATE_TIMING] Failed after {elapsed_time:.3f}s")
            logger.error(f"[STATE_UPDATE] Error updating conversation state: {e}")
            import traceback
            logger.error(f"[STATE_UPDATE] Traceback: {traceback.format_exc()}")  

    

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

        # Update conversation state after user message
        state_update_start = time.perf_counter()
        await self.update_conversation_state()
        state_update_time = time.perf_counter() - state_update_start
        logger.info(f"[TIMING] update_conversation_state call took {state_update_time:.3f}s")
        
        # Inject conversation state as context for the main LLM
        context_inject_start = time.perf_counter()
        with self._lock:
            # Extract user preferences
            user_prefs = self.conversation_state["inferred_user_preferences"]
            
            # Format explicit preferences
            explicit_prefs = user_prefs["explicit_preferences"]
            explicit_lines = []
            for pref in explicit_prefs:
                explicit_lines.append(f"  - {pref}")
            explicit_text = '\n'.join(explicit_lines) if explicit_lines else '  (none)'
            
            # Format implicit preferences
            implicit_prefs = user_prefs["implicit_preferences"]
            implicit_lines = []
            for pref in implicit_prefs:
                implicit_lines.append(f"  - {pref}")
            implicit_text = '\n'.join(implicit_lines) if implicit_lines else '  (none)'
            
            # Format product attributes (flat array of attribute objects)
            prod_attrs = self.conversation_state["inferred_product_attributes"]
            attributes_text = ""
            if prod_attrs and isinstance(prod_attrs, list):
                attr_lines = []
                for attr in prod_attrs:
                    attr_name = attr["name"]
                    attr_value = attr["value"]
                    importance = attr.get("importance", "medium")
                    is_explicit = '✓' if attr["is_explicit"] else '~'
                    attr_lines.append(f"  [{is_explicit}][{importance}] {attr_name}: {attr_value}")
                attributes_text = '\n'.join(attr_lines)
            else:
                attributes_text = "  (none)"
            
            # Group attributes by importance
            critical_attrs = [attr for attr in prod_attrs if attr.get("importance") == "critical"]
            high_attrs = [attr for attr in prod_attrs if attr.get("importance") == "high"]
            medium_attrs = [attr for attr in prod_attrs if attr.get("importance") == "medium"]
            low_attrs = [attr for attr in prod_attrs if attr.get("importance") == "low"]
            
            # Format grouped attributes
            grouped_attrs_text = ""
            if critical_attrs:
                grouped_attrs_text += "  Critical Requirements:\n"
                for attr in critical_attrs:
                    marker = '✓' if attr["is_explicit"] else '~'
                    grouped_attrs_text += f"    [{marker}] {attr['name']}: {attr['value']}\n"
            if high_attrs:
                grouped_attrs_text += "  High Priority:\n"
                for attr in high_attrs:
                    marker = '✓' if attr["is_explicit"] else '~'
                    grouped_attrs_text += f"    [{marker}] {attr['name']}: {attr['value']}\n"
            if medium_attrs:
                grouped_attrs_text += "  Medium Priority:\n"
                for attr in medium_attrs:
                    marker = '✓' if attr["is_explicit"] else '~'
                    grouped_attrs_text += f"    [{marker}] {attr['name']}: {attr['value']}\n"
            if low_attrs:
                grouped_attrs_text += "  Nice to Have:\n"
                for attr in low_attrs:
                    marker = '✓' if attr["is_explicit"] else '~'
                    grouped_attrs_text += f"    [{marker}] {attr['name']}: {attr['value']}\n"
            
            grouped_attrs_text = grouped_attrs_text.rstrip('\n') if grouped_attrs_text else "  (none)"
            
            state_context = f"""<user_needs_summary>
This is an automatically inferred summary of what the user is looking for. Use this to guide your product recommendations and responses.

Product Type: {self.conversation_state['product_category'] or '(none)'}
Search Query: {self.conversation_state['search_query'] or '(none)'}
Current Intent: {self.conversation_state['user_intention'] or '(none)'}

Usage Context:
- Usage Scenario: {user_prefs['usage_scenario'] or '(none)'}
- Budget: {user_prefs['budget'] or '(none)'}

User Preferences (What the user wants):
Explicitly Stated:
{explicit_text}
Inferred from Context:
{implicit_text}

Product Attributes (Specific requirements, prioritized):
Legend: [✓ = user stated, ~ = inferred]
{grouped_attrs_text}
</user_needs_summary>"""
            
            logger.info(f"Conversation state context:\n{state_context}")
            
            self.messages.append({
                "role": "user",
                "content": [{"text": state_context}],
                "createdAt": _now_iso(),
                "hidden": True
            })
        
        context_inject_time = time.perf_counter() - context_inject_start
        logger.info(f"[TIMING] Context injection took {context_inject_time:.3f}s")
        
        try:
            _llm_start = time.perf_counter()
            sanitized_messages = _normalize_tool_inputs(self.messages)
            #logger.info(f"sanitized_messages: {sanitized_messages}")
            response = self.bedrock_client.converse_stream(
                modelId=self.model_id,
                messages=sanitized_messages,
                system=self.system_prompts,
                inferenceConfig=inference_config,
                additionalModelRequestFields=additional_model_fields,
                toolConfig=self.tool_config,
            )
            _llm_elapsed = time.perf_counter() - _llm_start
            logger.info(f"[TIMING] LLM converse (initial) took {_llm_elapsed:.3f}s")

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
            _llm_elapsed = time.perf_counter() - _llm_start
            logger.error(f"[TIMING] LLM converse (initial) failed after {_llm_elapsed:.3f}s: {e}")
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
                    #logger.info(f"tool_request: {tool_request}")
                    tool = tool_request['toolUse']
                    tool_name = tool['name']
                    tool_input = tool['input']
                    tool_use_id = tool['toolUseId']
                    #logger.info(f"🛠️ Tool used: {tool_name} with input {tool_input}")
                    
                    # Notify client about tool use
                    yield {"type": "tool_use", "tool": tool_name, "input": tool_input}

                    # Handle async tool calls
                    try:
                        _tool_start = time.perf_counter()
                        #logger.info(f"[TOOL_EXEC] Starting execution of tool: {tool_name}")
                        #logger.info(f"[TOOL_EXEC] Tool input: {tool_input}")
                        
                        if tool_name == 'search':
                            result_text = await self.search(tool_input['query'])
                            #logger.info(f"[TOOL_EXEC] search returned {len(result_text)} characters")
                            #logger.info(f"[TOOL_EXEC] search result preview: {result_text[:500]}")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        elif tool_name == 'visit_product':
                            #logger.info(f"[TOOL_EXEC] visit_product input: {tool_input}")
                            result_text = await self.visit_product(tool_input['product_url'])
                            #logger.info(f"[TOOL_EXEC] visit_product returned {len(result_text or '')} characters")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        else:
                            logger.error(f"[TOOL_EXEC] Unknown tool requested: {tool_name}")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": f"Unknown tool: {tool_name}"}],
                                "status": "error"
                            }
                        
                        #logger.info(f"[TOOL_EXEC] Tool result structure: {json.dumps(tool_result, ensure_ascii=False)[:500]}")
                    except Exception as e:
                        import traceback
                        logger.error(f"[TOOL_EXEC] Exception executing tool {tool_name}: {e}")
                        logger.error(f"[TOOL_EXEC] Full traceback: {traceback.format_exc()}")
                        tool_result = {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"Error executing tool {tool_name}: {str(e)}"}],
                            "status": "error"
                        }
                    finally:
                        try:
                            _tool_elapsed = time.perf_counter() - _tool_start
                            logger.info(f"[TIMING] Tool '{tool_name}' took {_tool_elapsed:.3f}s")
                        except Exception:
                            pass

                    tool_result_contents.append({"toolResult": tool_result})
                    #logger.info(f"[TOOL_EXEC] Added tool result to contents, total results: {len(tool_result_contents)}")
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
                    #logger.info(f"[DEBUG] Total messages count: {len(sanitized_messages)}")
                    #total_chars = 0
                    #for idx, msg in enumerate(sanitized_messages):
                    #    msg_chars = len(json.dumps(msg, ensure_ascii=False))
                    #    total_chars += msg_chars
                    #    logger.info(f"[DEBUG] Message {idx} - role={msg['role']}, chars={msg_chars}, content_blocks={len(msg.get('content', []))}")
                    #    for cidx, content in enumerate(msg.get('content', [])):
                    #        if 'text' in content:
                    #            text_len = len(content['text'])
                    #            logger.info(f"[DEBUG]   Content[{cidx}] text length: {text_len}")
                    #            logger.info(f"[DEBUG]   Content[{cidx}] text preview: {content['text'][:500]}")
                    #        elif 'toolResult' in content:
                    #            tool_result = content['toolResult']
                    #            result_text = json.dumps(tool_result, ensure_ascii=False)
                    #            logger.info(f"[DEBUG]   Content[{cidx}] toolResult length: {len(result_text)}")
                    #            logger.info(f"[DEBUG]   Content[{cidx}] toolResult preview: {result_text[:500]}")
                    #        elif 'toolUse' in content:
                    #            tool_use = content['toolUse']
                    #            logger.info(f"[DEBUG]   Content[{cidx}] toolUse: {tool_use['name']}")
                    #logger.info(f"[DEBUG] Total characters in all messages: {total_chars}")
                    
                    response = self.bedrock_client.converse_stream(
                        modelId=self.model_id,
                        messages=sanitized_messages,
                        system=self.system_prompts,
                        inferenceConfig=inference_config,
                        additionalModelRequestFields=additional_model_fields,
                        toolConfig=self.tool_config
                    )
                    _llm_follow_elapsed = time.perf_counter() - _llm_follow_start
                    logger.info(f"[TIMING] LLM converse (after tools) took {_llm_follow_elapsed:.3f}s")
                    
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

                    #logger.info(f"output_message: {output_message}")
                    self.messages.append(output_message)
                except Exception as e:
                    _llm_follow_elapsed = time.perf_counter() - _llm_follow_start
                    logger.error(f"[TIMING] LLM converse (after tools) failed after {_llm_follow_elapsed:.3f}s: {e}")
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
        #print(f"new_messages: {new_messages}")
        self.messages = new_messages
        
        _function_elapsed = time.perf_counter() - _function_start_ms
        logger.info(f"[TIMING] generate_conversation_stream total {_function_elapsed:.3f}s")
        
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
            #logger.info(f"sanitized_messages: {sanitized_messages}")
            #logger.info(f"system_prompts: {self.system_prompts}")
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
            _llm_elapsed = time.perf_counter() - _llm_start
            logger.info(f"[TIMING] LLM converse (initial) took {_llm_elapsed:.3f}s")

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
            _llm_elapsed = time.perf_counter() - _llm_start
            logger.error(f"[TIMING] LLM converse (initial) failed after {_llm_elapsed:.3f}s: {e}")
            # Create a fallback response
            output_message = {
                "role": "assistant",
                "content": [{"text": f"I encountered an error: {str(e)}"}]
            }
            self.messages.append(output_message)
            _function_elapsed = time.perf_counter() - _function_start_ms
            logger.info(f"[TIMING] generate_conversation_async total {_function_elapsed:.3f}s (early return)")
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
                    #logger.info(f"🛠️ Tool used: {tool_name} with input {tool_input}")

                    # Handle async tool calls
                    try:
                        _tool_start = time.perf_counter()
                        #logger.info(f"[TOOL_EXEC] Starting execution of tool: {tool_name}")
                        #logger.info(f"[TOOL_EXEC] Tool input: {tool_input}")
                        
                        if tool_name == 'search':
                            result_text = await self.search(tool_input['query'])
                            #logger.info(f"[TOOL_EXEC] search returned {len(result_text)} characters")
                            #logger.info(f"[TOOL_EXEC] search result preview: {result_text[:500]}")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        elif tool_name == 'visit_product':
                            #logger.info(f"[TOOL_EXEC] visit_product input: {tool_input}")
                            result_text = await self.visit_product(tool_input['product_url'])
                            #logger.info(f"[TOOL_EXEC] visit_product returned {len(result_text or '')} characters")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": result_text}]
                            }
                        else:
                            logger.error(f"[TOOL_EXEC] Unknown tool requested: {tool_name}")
                            tool_result = {
                                "toolUseId": tool_use_id,
                                "content": [{"text": f"Unknown tool: {tool_name}"}],
                                "status": "error"
                            }
                        
                        #logger.info(f"[TOOL_EXEC] Tool result structure: {json.dumps(tool_result, ensure_ascii=False)[:500]}")
                    except Exception as e:
                        import traceback
                        logger.error(f"[TOOL_EXEC] Exception executing tool {tool_name}: {e}")
                        logger.error(f"[TOOL_EXEC] Full traceback: {traceback.format_exc()}")
                        tool_result = {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"Error executing tool {tool_name}: {str(e)}"}],
                            "status": "error"
                        }
                    finally:
                        try:
                            _tool_elapsed = time.perf_counter() - _tool_start
                            logger.info(f"[TIMING] Tool '{tool_name}' took {_tool_elapsed:.3f}s")
                        except Exception:
                            pass

                    tool_result_contents.append({"toolResult": tool_result})
                    #logger.info(f"[TOOL_EXEC] Added tool result to contents, total results: {len(tool_result_contents)}")

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
                    #logger.info(f"[DEBUG] Total messages count: {len(sanitized_messages)}")
                    #total_chars = 0
                    #for idx, msg in enumerate(sanitized_messages):
                    #    msg_chars = len(json.dumps(msg, ensure_ascii=False))
                    #    total_chars += msg_chars
                    #    logger.info(f"[DEBUG] Message {idx} - role={msg['role']}, chars={msg_chars}, content_blocks={len(msg.get('content', []))}")
                    #    for cidx, content in enumerate(msg.get('content', [])):
                    #        if 'text' in content:
                    #            text_len = len(content['text'])
                    #            logger.info(f"[DEBUG]   Content[{cidx}] text length: {text_len}")
                    #            logger.info(f"[DEBUG]   Content[{cidx}] text preview: {content['text'][:500]}")
                    #        elif 'toolResult' in content:
                    #            tool_result = content['toolResult']
                    #            result_text = json.dumps(tool_result, ensure_ascii=False)
                    #            logger.info(f"[DEBUG]   Content[{cidx}] toolResult length: {len(result_text)}")
                    #            logger.info(f"[DEBUG]   Content[{cidx}] toolResult preview: {result_text[:500]}")
                    #        elif 'toolUse' in content:
                    #            tool_use = content['toolUse']
                    #            logger.info(f"[DEBUG]   Content[{cidx}] toolUse: {tool_use['name']}")
                    #logger.info(f"[DEBUG] Total characters in all messages: {total_chars}")
                    
                    #logger.info(f"sanitized_messages: {sanitized_messages}")
                    #logger.info(f"system_prompts: {self.system_prompts}")
                    response = self.bedrock_client.converse_stream(
                        modelId=self.model_id,
                        messages=sanitized_messages,
                        system=self.system_prompts,
                        inferenceConfig=inference_config,
                        additionalModelRequestFields=additional_model_fields,
                        toolConfig=self.tool_config
                    )
                    _llm_follow_elapsed = time.perf_counter() - _llm_follow_start
                    logger.info(f"[TIMING] LLM converse (after tools) took {_llm_follow_elapsed:.3f}s")
                    
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
                    #logger.info(f"output_message: {output_message}")
                    self.messages.append(output_message)
                except Exception as e:
                    _llm_follow_elapsed = time.perf_counter() - _llm_follow_start
                    logger.error(f"[TIMING] LLM converse (after tools) failed after {_llm_follow_elapsed:.3f}s: {e}")
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
        #print(f"new_messages: {new_messages}")
        self.messages = new_messages
        _function_elapsed = time.perf_counter() - _function_start_ms
        logger.info(f"[TIMING] generate_conversation_async total {_function_elapsed:.3f}s")
        #logger.info(f"output_message: {output_message}")
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


@app.route('/sessions/<session_id>/conversation-state', methods=['GET'])
async def get_conversation_state_api(session_id):
    """Get the current conversation state for a session."""
    s = get_session(session_id)
    if not s:
        return jsonify({"success": False, "error": "Session not found"}), 404
    
    with s._lock:
        state = s.conversation_state
    
    return jsonify({
        "success": True,
        "conversation_state": state
    }), 200


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