#!/usr/bin/env python3
import asyncio
import logging

import hydra
from omegaconf import DictConfig

from rl_web_agent.env import WebAgentEnv

# Fake task config for testing
FAKE_TASK_CONFIG = {"sites": ["shopping"], "task_id": 1, "require_login": False, "start_url": "http://metis.lti.cs.cmu.edu:7770", "intent": "Find the price of Bliss Lemon Sage Hand Cream", "eval": {"eval_types": ["string_match"], "reference_answers": {"exact_match": "$24.00"}}}


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    """Main entry point for the web agent"""
    logging.basicConfig(level=cfg.log_level)
    logger = logging.getLogger(__name__)

    # Create and setup environment
    env = WebAgentEnv(cfg.environment)

    async def run():
        try:
            await env.setup(FAKE_TASK_CONFIG)
            logger.info("Environment setup complete")
            # TODO: Add agent interaction logic here
            await asyncio.sleep(1000)
        finally:
            await env.close()

    # Run the async environment
    asyncio.run(run())


if __name__ == "__main__":
    main()
