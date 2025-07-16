import logging
from pathlib import Path

import playwright
from omegaconf import DictConfig


class WebAgentEnv:
    def __init__(self, config: DictConfig):
        self.config = config
        self.context_manager = None
        self.browser = None
        self.context = None
        self.page = None

        # Set up logging
        logging.basicConfig(level=getattr(logging, config.log_level))
        self.logger = logging.getLogger(__name__)

    async def setup(self):
        """Initialize the browser environment with configuration"""
        self.context_manager = await playwright.async_playwright().start()

        # Browser launch options
        launch_options = {
            "headless": self.config.browser.headless,
        }

        # Add proxy if enabled
        if self.config.proxy.enabled:
            launch_options["proxy"] = {"server": self.config.proxy.server}

        # Get browser type
        browser_type = getattr(self.context_manager, self.config.browser.browser_type)
        self.browser = await browser_type.launch(**launch_options)

        # Context options
        context_options = {"viewport": {"width": self.config.browser.viewport_width, "height": self.config.browser.viewport_height}}

        # Add extra headers if configured
        if self.config.browser.extra_http_headers:
            context_options["extra_http_headers"] = self.config.browser.extra_http_headers

        # Add user agent if configured
        if self.config.browser.user_agent:
            context_options["user_agent"] = self.config.browser.user_agent

        self.context = await self.browser.new_context(**context_options)

        # Add init script if it exists
        init_script_path = Path(self.config.environment.init_script_path)
        if init_script_path.exists():
            with open(init_script_path) as f:
                await self.context.add_init_script(f.read())
        else:
            self.logger.warning(f"Init script not found: {init_script_path}")

        self.page = await self.context.new_page()
        await self.page.goto(self.config.environment.target_url)

    async def reset(self):
        """Reset the environment to initial state"""
        await self.page.goto(self.config.environment.target_url)

    async def step(self, action):
        """Execute an action in the environment"""
        await self.page.click(action)

    async def get_page_content(self):
        """Get parsed page content using the parser script"""
        parser_script_path = Path(self.config.environment.parser_script_path)
        if parser_script_path.exists():
            with open(parser_script_path) as f:
                parser_code = f.read()
            return await self.page.evaluate(parser_code)
        else:
            self.logger.warning(f"Parser script not found: {parser_script_path}")
            return {"html": await self.page.content()}

    async def close(self):
        """Clean up and close the browser"""
        if self.context_manager:
            await self.context_manager.close()
