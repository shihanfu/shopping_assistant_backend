#!/usr/bin/env python3
"""
Example usage of the async LLM client with OpenAI and Bedrock providers.
"""

import asyncio
import logging

import hydra
from omegaconf import DictConfig

from rl_web_agent.llm import create_llm_client


async def test_openai_completion(config):
    """Test OpenAI completion"""
    print("🔵 Testing OpenAI completion...")

    # Override config to use OpenAI
    config.llm.provider = "openai"

    async with await create_llm_client(config.llm) as client:
        # Single completion
        from rl_web_agent.prompts import load_prompt

        system_prompt = load_prompt("helpful_assistant")
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "What is the capital of France?"}]

        result = await client.complete(messages)
        print(f"Response: {result}")


async def test_bedrock_completion(config):
    """Test Bedrock completion"""
    print("🟠 Testing Bedrock completion...")

    # Override config to use Bedrock
    config.llm.provider = "bedrock"

    try:
        async with await create_llm_client(config.llm) as client:
            # Single completion
            messages = [{"role": "user", "content": "What is the capital of Germany?"}]

            result = await client.complete(messages)
            print(f"Response: {result}")
    except Exception as e:
        print(f"Bedrock test failed (expected if AWS not configured): {e}")


async def test_concurrent_completions(config):
    """Test concurrent completions"""
    print("🚀 Testing concurrent completions...")

    config.llm.provider = "openai"
    config.llm.max_concurrent = 3

    async with await create_llm_client(config.llm) as client:
        # Multiple requests
        requests = [{"messages": [{"role": "user", "content": f"Count to {i}"}], "max_tokens": 50} for i in range(1, 6)]

        results = await client.complete_many(requests)

        for i, result in enumerate(results, 1):
            print(f"Request {i}: {result}")


async def run_tests(cfg: DictConfig) -> None:
    """Main test function"""
    logging.basicConfig(level=logging.INFO)

    print("🤖 LLM Client Example")
    print("=" * 50)

    # Test different functionalities
    if cfg.llm.openai.get("api_key"):
        await test_openai_completion(cfg)
        print()
        await test_concurrent_completions(cfg)
        print()
    else:
        print("⚠️  OPENAI_API_KEY not set, skipping OpenAI tests")

    await test_bedrock_completion(cfg)

    print("\n✅ Example completed!")


@hydra.main(version_base=None, config_path="../rl_web_agent/conf", config_name="config")
def main(cfg: DictConfig) -> None:
    """Hydra entry point"""
    asyncio.run(run_tests(cfg))


if __name__ == "__main__":
    main()
