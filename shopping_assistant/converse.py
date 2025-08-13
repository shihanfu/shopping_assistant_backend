# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import logging
import boto3
import asyncio
from botocore.exceptions import ClientError
from prompts.system_prompt import SYSTEM_PROMPT
from tool_config import TOOL_CONFIG
import json
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

from rl_web_agent.env import WebAgentEnv
import hydra
from hydra import initialize, compose

# Global WebAgentEnv instance
env = None

async def setup_environment():
    """Initialize the WebAgentEnv with proper configuration."""
    global env
    
    # Initialize Hydra context, point to config directory
    with initialize(config_path="../rl_web_agent/conf", version_base=None):
        # Compose the config object with the main config name (no .yaml)
        cfg = compose(config_name="config")
    
    env = WebAgentEnv(cfg.environment)
    await env.setup({
        "sites": [
            "shopping"
        ]
    })
    logger.info("WebAgentEnv initialized successfully")

async def cleanup_environment():
    """Clean up the WebAgentEnv instance."""
    global env
    if env:
        await env.close()
        logger.info("WebAgentEnv closed successfully")

async def search(query: str) -> str:
    """Search for products using WebAgentEnv."""
    global env
    if not env:
        return "Error: WebAgentEnv not initialized"
    
    try:
        # URL encode the query for the search URL
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        url_template = "http://metis.lti.cs.cmu.edu:7770/catalogsearch/result/?q={query}"
        search_url = url_template.format(query=encoded_query)
        
        await env.step(f'{{"action": "goto_url", "url": "{search_url}"}}')
        observation = await env.observation()
        
        # Return the HTML content directly as the tool response
        return observation.get("html", "No HTML content available")
        
    except Exception as e:
        logger.error(f"Error in search function: {e}")
        return f"Error during search: {str(e)}"

async def visit_product(product_url: str) -> str:
    """Visit a product page using WebAgentEnv."""
    global env
    if not env:
        return "Error: WebAgentEnv not initialized"
    
    try:
        await env.step(f'{{"action": "goto_url", "url": "{product_url}"}}')
        observation = await env.observation()
        
        # Return the HTML content directly as the tool response
        return observation.get("html", "No HTML content available")
        
    except Exception as e:
        logger.error(f"Error in visit_product function: {e}")
        return f"Error visiting product: {str(e)}"

async def generate_conversation_async(bedrock_client,
                                    model_id,
                                    system_prompts,
                                    messages,
                                    tool_config):
    """
    Sends messages to a model with async tool handling.
    """

    temperature = 0.5
    top_k = 200

    inference_config = {"temperature": temperature}
    additional_model_fields = {"top_k": top_k}

    try:
        response = bedrock_client.converse(
            modelId=model_id,
            messages=messages,
            system=system_prompts,
            inferenceConfig=inference_config,
            additionalModelRequestFields=additional_model_fields,
            toolConfig=tool_config
        )

        output_message = response['output']['message']
        messages.append(output_message)
    except Exception as e:
        logger.error(f"Error in main model call: {e}")
        # Create a fallback response
        output_message = {
            "role": "assistant",
            "content": [{"text": f"I encountered an error: {str(e)}"}]
        }
        messages.append(output_message)
        return messages, output_message

    if response['stopReason'] == 'tool_use':
        tool_requests = output_message['content']
        tool_result_contents = []

        for tool_request in tool_requests:
            if 'toolUse' in tool_request:
                tool = tool_request['toolUse']
                tool_name = tool['name']
                tool_input = tool['input']
                tool_use_id = tool['toolUseId']
                print(f"🛠️ Tool used: {tool_name} with input {tool_input}")

                # Handle async tool calls
                try:
                    if tool_name == 'search':
                        result_text = await search(tool_input['query'])
                        tool_result = {
                            "toolUseId": tool_use_id,
                            "content": [{"text": result_text}]
                        }
                    elif tool_name == 'visit_product':
                        result_text = await visit_product(tool_input['product_url'])
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

                tool_result_contents.append({"toolResult": tool_result})

        if tool_result_contents:
            # Add a single user message containing ALL toolResult blocks
            messages.append({
                "role": "user",
                "content": tool_result_contents
            })

            # Single follow-up model call after providing all tool results
            try:
                response = bedrock_client.converse(
                    modelId=model_id,
                    messages=messages,
                    system=system_prompts,
                    inferenceConfig=inference_config,
                    additionalModelRequestFields=additional_model_fields,
                    toolConfig=tool_config
                )
                output_message = response['output']['message']
                messages.append(output_message)
            except Exception as e:
                logger.error(f"Error in follow-up model call: {e}")
                output_message = {
                    "role": "assistant",
                    "content": [{"text": f"I encountered an error processing the tool results: {str(e)}"}]
                }
                messages.append(output_message)

    return messages, output_message

def generate_conversation(bedrock_client,
                          model_id,
                          system_prompts,
                          messages,
                          tool_config):
    """
    Wrapper for async conversation generation.
    """
    return asyncio.run(generate_conversation_async(bedrock_client, model_id, system_prompts, messages, tool_config))

async def main_async():
    """Async main function."""
    model_id = "arn:aws:bedrock:us-east-1:248189905876:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    system_prompts = [{"text": SYSTEM_PROMPT}]
    messages = []

    try:
        # Setup WebAgentEnv
        await setup_environment()
        
        session = boto3.Session(profile_name='yuxuanlu', region_name='us-east-1')
        bedrock_client = session.client('bedrock-runtime')

        while True:
            try:
                user_input = input(" User: ").strip()
                
                if not user_input:
                    continue
                
                # Add user message to conversation
                user_message = {
                    "role": "user",
                    "content": [{"text": user_input}]
                }

                messages.append(user_message)
                
                try:
                    messages, ai_message = await generate_conversation_async(
                        bedrock_client, model_id, system_prompts, messages, TOOL_CONFIG
                    )

                    for content_item in ai_message.get("content", []):
                        if "text" in content_item:
                            print(f"Assistant: {content_item['text']}")
                            break
                except Exception as e:
                    logger.error(f"Error in conversation generation: {e}")
                    print(f"Assistant: I encountered an error. Let me start fresh.")
                    # Clear the message history to avoid corruption
                    messages = []

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error: {e}")

    except ClientError as err:
        message = err.response['Error']['Message']
        logger.error("A client error occurred: %s", message)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")

    finally:
        # Cleanup WebAgentEnv
        await cleanup_environment()

    print(f"\nConversation ended. Total messages: {len(messages)}")

def main():
    """Main function that runs the async main."""
    asyncio.run(main_async())

if __name__ == "__main__":
    main() 
