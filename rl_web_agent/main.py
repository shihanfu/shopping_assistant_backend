import asyncio
import logging

import hydra
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf

from .config import WebAgentConfig
from .env import WebAgentEnv

# Register the config schema with Hydra
cs = ConfigStore.instance()
cs.store(name="base_config", node=WebAgentConfig)

logger = logging.getLogger(__name__)


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    """Main entry point for the RL Web Agent with Hydra configuration"""

    # Print the configuration
    logger.info("Configuration:")
    logger.info(OmegaConf.to_yaml(cfg))

    # Run the async main function
    asyncio.run(async_main(cfg))


async def async_main(cfg: DictConfig) -> None:
    """Async main function that runs the web agent"""

    # Create the web agent environment
    env = WebAgentEnv(cfg)

    try:
        # Setup the environment
        await env.setup()
        logger.info("Web agent environment setup complete")

        # Example usage: get page content
        content = await env.get_page_content()
        logger.info(f"Page loaded with {len(content.get('html', ''))} characters")

        # Example: hover over an element (similar to the notebook)
        if "css=[data-semantic-id=bliss_lemon_sage_han]" in content.get("html", ""):
            await env.page.hover("css=[data-semantic-id=bliss_lemon_sage_han]")
            logger.info("Hovered over element")

            # Get updated content
            content = await env.get_page_content()
            logger.info("Updated page content after hover")

        # Keep the browser open for a moment if not headless
        if not cfg.browser.headless:
            logger.info("Browser is open. Press Ctrl+C to close.")
            try:
                await asyncio.sleep(30)  # Keep open for 30 seconds
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")

    except Exception as e:
        logger.error(f"Error running web agent: {e}")
        raise
    finally:
        # Clean up
        await env.close()
        logger.info("Web agent environment closed")


if __name__ == "__main__":
    main()
