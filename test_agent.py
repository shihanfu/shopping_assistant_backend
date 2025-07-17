#!/usr/bin/env python3
"""
Test script for the WebAgent implementation.
"""

import asyncio
import logging
from pathlib import Path

import hydra

from rl_web_agent.agent import create_web_agent
from rl_web_agent.env import WebAgentEnv

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_web_agent():
    """Test the WebAgent with a simple task."""

    # Load configuration
    config_path = Path("rl_web_agent/conf/config.yaml")
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        return

    # Initialize Hydra with the config, override to use Bedrock
    with hydra.initialize(config_path="rl_web_agent/conf", version_base=None):
        cfg = hydra.compose(config_name="config", overrides=["llm.provider=bedrock"])

    # Load test task from WebArena config
    import json

    with open("/Users/yuxuanlu/code/rl_web_agent/thirdparty/webarena/config_files/506.json") as f:
        test_task = json.load(f)

    # Create environment and agent
    env = WebAgentEnv(cfg.environment, cfg)  # Pass full config for accounts access
    agent = await create_web_agent(cfg.llm)

    try:
        # Setup environment with test task
        await env.setup(test_task)
        logger.info("Environment setup complete")

        # Run the task
        logger.info("Starting agent task execution")
        result = await agent.run_task(env, test_task["intent"], max_steps=10)

        # Print results
        logger.info(f"Task completed with result: {result}")

    except Exception as e:
        logger.error(f"Test failed with error: {e}")

    finally:
        # Cleanup
        await agent.close()
        await env.close()


if __name__ == "__main__":
    asyncio.run(test_web_agent())
