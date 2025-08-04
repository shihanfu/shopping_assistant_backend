"""
Async LLM interface supporting OpenAI and AWS Bedrock providers.
Unified interface: input is OpenAI message format, output is string content.
Also supports function calling with structured responses.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import aioboto3
import openai
from omegaconf import DictConfig


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    def __init__(self, config: DictConfig, semaphore: asyncio.Semaphore):
        self.config = config
        self.semaphore = semaphore
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    async def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Generate completion from OpenAI format messages, return content string"""
        pass

    @abstractmethod
    async def complete_with_tools(self, messages: list[dict[str, str]], tools: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """Generate completion with function calling support, return structured response"""
        pass

    @abstractmethod
    async def close(self):
        """Clean up resources"""
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI API provider using official async SDK"""

    def __init__(self, config: DictConfig, semaphore: asyncio.Semaphore):
        super().__init__(config, semaphore)
        self.client = openai.AsyncOpenAI(api_key=config.api_key, base_url=config.get("base_url"), timeout=config.get("timeout", 60), max_retries=config.get("max_retries", 2))
        self.model = config.get("model", "gpt-4")

    async def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Generate completion using OpenAI API"""
        async with self.semaphore:
            response = await self.client.chat.completions.create(
                model=kwargs.get("model", self.model),
                messages=messages,
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                top_p=kwargs.get("top_p"),
                frequency_penalty=kwargs.get("frequency_penalty"),
                presence_penalty=kwargs.get("presence_penalty"),
                stop=kwargs.get("stop"),
                stream=False,
            )

            content = response.choices[0].message.content
            self.logger.debug(f"OpenAI response: {content}")
            return content

    async def complete_with_tools(self, messages: list[dict[str, str]], tools: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """Generate completion with function calling using OpenAI API"""
        async with self.semaphore:
            # Prepare request with tools
            request_kwargs = {
                "model": kwargs.get("model", self.model),
                "messages": messages,
                "tools": tools,
                "temperature": kwargs.get("temperature"),
                "max_tokens": kwargs.get("max_tokens"),
                "top_p": kwargs.get("top_p"),
                "frequency_penalty": kwargs.get("frequency_penalty"),
                "presence_penalty": kwargs.get("presence_penalty"),
                "stop": kwargs.get("stop"),
                "stream": False,
            }

            # Add tool_choice if specified
            if "tool_choice" in kwargs:
                request_kwargs["tool_choice"] = kwargs["tool_choice"]

            response = await self.client.chat.completions.create(**request_kwargs)

            choice = response.choices[0]
            message = choice.message

            # Format response consistently
            result = {"content": message.content or "", "tool_calls": [], "finish_reason": choice.finish_reason}

            # Process tool calls if present
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    result["tool_calls"].append({"id": tool_call.id, "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments}, "type": tool_call.type})

            self.logger.debug(f"OpenAI function calling response: {result}")
            return result

    async def close(self):
        """Close OpenAI client"""
        await self.client.close()


class BedrockProvider(LLMProvider):
    """AWS Bedrock provider using official boto3 SDK with Converse API"""

    def __init__(self, config: DictConfig, semaphore: asyncio.Semaphore):
        super().__init__(config, semaphore)
        self.region = config.get("region", "us-east-1")
        self.model_id = config.get("model_id", "anthropic.claude-3-sonnet-20240229-v1:0")
        self.session = None
        self.client = None

        # Thinking configuration
        self.thinking_enabled = config.get("thinking", {}).get("enabled", False)
        self.thinking_budget_tokens = config.get("thinking", {}).get("budget_tokens", 32000)

    async def _get_client(self):
        """Get or create Bedrock client"""
        # Always create a fresh session and client to avoid reuse issues
        self.session = aioboto3.Session()
        self.client = self.session.client("bedrock-runtime", region_name=self.region)
        return self.client

    async def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Generate completion using Bedrock Converse API"""
        async with self.semaphore:
            client = await self._get_client()

            # Convert OpenAI messages to Converse API format
            converse_messages = []
            system_messages = []

            for msg in messages:
                if msg["role"] == "system":
                    system_messages.append({"text": msg["content"]})
                else:
                    converse_messages.append({"role": msg["role"], "content": [{"text": msg["content"]}]})

            # Prepare inference config
            inference_config = {
                "maxTokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
                "topP": kwargs.get("top_p"),
            }

            # Add stop sequences if provided
            stop_sequences = kwargs.get("stop_sequences", kwargs.get("stop"))
            if stop_sequences:
                inference_config["stopSequences"] = stop_sequences if isinstance(stop_sequences, list) else [stop_sequences]

            # Use Converse API
            converse_kwargs = {"modelId": kwargs.get("model_id", self.model_id), "messages": converse_messages, "inferenceConfig": inference_config}

            # Add system messages if present
            if system_messages:
                converse_kwargs["system"] = system_messages

            # Add thinking configuration if enabled
            if self.thinking_enabled:
                converse_kwargs["additionalModelRequestFields"] = {"reasoning_config": {"type": "enabled", "budget_tokens": self.thinking_budget_tokens}}
                inference_config["maxTokens"] += self.thinking_budget_tokens

            async with client as bedrock_client:
                response = await bedrock_client.converse(**converse_kwargs)

            # Extract content string
            output = response["output"]["message"]
            content = ""
            print(output)
            print(response)
            # Process content blocks
            if "content" in output:
                for content_block in output["content"]:
                    if "text" in content_block:
                        # if content_block["type"] == "text":
                        content += content_block["text"]

            # Log thinking content if present (when thinking is enabled)
            if self.thinking_enabled and "reasoning" in output:
                reasoning_content = output["reasoning"].get("content", "")
                self.logger.debug(f"Bedrock thinking content: {reasoning_content}")

            self.logger.debug(f"Bedrock response: {content}")
            return content

    async def complete_with_tools(self, messages: list[dict[str, str]], tools: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """Generate completion with function calling using Bedrock Converse API"""
        async with self.semaphore:
            client = await self._get_client()

            # Convert OpenAI messages to Converse API format
            converse_messages = []
            system_messages = []

            for msg in messages:
                if msg["role"] == "system":
                    system_messages.append({"text": msg["content"]})
                elif msg["role"] == "tool":
                    # Handle tool response messages
                    converse_messages.append({"role": "user", "content": [{"text": f"Tool result: {msg['content']}"}]})
                else:
                    converse_messages.append({"role": msg["role"], "content": [{"text": msg["content"]}]})

            # Convert OpenAI tools format to Bedrock format
            tool_config = None
            if tools:
                tool_config = {"tools": []}

                for tool in tools:
                    if tool["type"] == "function":
                        func = tool["function"]
                        bedrock_tool = {"toolSpec": {"name": func["name"], "description": func["description"], "inputSchema": {"json": func["parameters"]}}}
                        tool_config["tools"].append(bedrock_tool)

            # Prepare inference config
            inference_config = {
                "maxTokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
                "topP": kwargs.get("top_p"),
            }

            # Add stop sequences if provided
            stop_sequences = kwargs.get("stop_sequences", kwargs.get("stop"))
            if stop_sequences:
                inference_config["stopSequences"] = stop_sequences if isinstance(stop_sequences, list) else [stop_sequences]

            # Use Converse API with tools
            converse_kwargs = {"modelId": kwargs.get("model_id", self.model_id), "messages": converse_messages, "inferenceConfig": inference_config}

            # Add system messages if present
            if system_messages:
                converse_kwargs["system"] = system_messages

            # Add tool configuration if present
            if tool_config:
                converse_kwargs["toolConfig"] = tool_config

            # Add thinking configuration if enabled
            if self.thinking_enabled:
                converse_kwargs["additionalModelRequestFields"] = {"reasoning_config": {"type": "enabled", "budget_tokens": self.thinking_budget_tokens}}

            async with client as bedrock_client:
                response = await bedrock_client.converse(**converse_kwargs)

            # Extract response and format consistently
            output = response["output"]["message"]
            result = {"content": "", "tool_calls": [], "finish_reason": response.get("stopReason", "stop")}

            # Process content
            if "content" in output:
                for content_block in output["content"]:
                    if "text" in content_block:
                        result["content"] += content_block["text"]
                    elif "toolUse" in content_block:
                        # Convert Bedrock tool use to OpenAI format
                        tool_use = content_block["toolUse"]
                        result["tool_calls"].append({"id": tool_use["toolUseId"], "function": {"name": tool_use["name"], "arguments": json.dumps(tool_use["input"])}, "type": "function"})

            # Include thinking content if present (when thinking is enabled)
            if self.thinking_enabled and "reasoning" in output:
                reasoning_content = output["reasoning"].get("content", "")
                result["reasoning_content"] = reasoning_content
                self.logger.debug(f"Bedrock thinking content: {reasoning_content}")

            self.logger.debug(f"Bedrock function calling response: {result}")
            return result

    async def close(self):
        """Close Bedrock client"""
        # aioboto3 session cleanup is handled automatically
        pass


class LLMClient:
    """Main LLM client with provider abstraction and concurrency control"""

    def __init__(self, config: DictConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Set up concurrency control
        max_concurrent = config.get("max_concurrent", 5)
        self.semaphore = asyncio.Semaphore(max_concurrent)

        # Store generation defaults from config
        self.generation_defaults = config.get("generation", {})

        # Initialize provider
        provider_name = config.get("provider", "openai").lower()
        if provider_name == "openai":
            self.provider = OpenAIProvider(config.openai, self.semaphore)
        elif provider_name == "bedrock":
            self.provider = BedrockProvider(config.bedrock, self.semaphore)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider_name}")

        self.logger.info(f"Initialized LLM client with {provider_name} provider, max_concurrent={max_concurrent}")

    async def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Generate completion from OpenAI format messages, return content string"""
        # Merge config defaults with provided kwargs
        merged_kwargs = dict(self.generation_defaults)
        merged_kwargs.update(kwargs)

        # Convert Hydra ListConfig to regular list for JSON serialization
        if "stop" in merged_kwargs and merged_kwargs["stop"] is not None:
            merged_kwargs["stop"] = list(merged_kwargs["stop"]) if merged_kwargs["stop"] else None

        return await self.provider.complete(messages, **merged_kwargs)

    async def complete_with_tools(self, messages: list[dict[str, str]], tools: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """Generate completion with function calling support, return structured response"""
        # Merge config defaults with provided kwargs
        merged_kwargs = dict(self.generation_defaults)
        merged_kwargs.update(kwargs)

        # Convert Hydra ListConfig to regular list for JSON serialization
        if "stop" in merged_kwargs and merged_kwargs["stop"] is not None:
            merged_kwargs["stop"] = list(merged_kwargs["stop"]) if merged_kwargs["stop"] else None

        return await self.provider.complete_with_tools(messages, tools, **merged_kwargs)

    async def complete_many(self, requests: list[dict[str, Any]]) -> list[str]:
        """Generate multiple completions concurrently, return list of content strings"""
        tasks = []
        for request in requests:
            messages = request.get("messages")
            if not messages:
                raise ValueError("Each request must have 'messages' field")
            kwargs = {k: v for k, v in request.items() if k != "messages"}
            task = self.complete(messages, **kwargs)
            tasks.append(task)

        return await asyncio.gather(*tasks)

    async def close(self):
        """Clean up resources"""
        await self.provider.close()

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        _ = exc_type, exc_val, exc_tb  # Unused parameters
        await self.close()


# Convenience function for quick usage
async def create_llm_client(config: DictConfig) -> LLMClient:
    """Create and return an LLM client instance"""
    return LLMClient(config)
