#!/usr/bin/env python3
"""
Human-friendly REPL interface for the RL Web Agent.
Translates simple function calls to JSON actions for easier manual testing.
"""

import asyncio
import json
import logging
import re

import hydra
from aioconsole import ainput
from omegaconf import DictConfig

from rl_web_agent.env import WebAgentEnv


class ActionParser:
    """Parse human-friendly actions into JSON format"""

    def parse(self, action: str) -> dict:
        """
        Parse human-friendly action format into JSON structure.

        Examples:
            click(login_button) -> {"action": "click", "target": "login_button"}
            type(username, john_doe, enter=true) -> {"action": "type", "target": "username", "text": "john_doe", "enter": true}
            goto(https://example.com) -> {"action": "goto_url", "url": "https://example.com"}
        """
        action = action.strip()
        if not action.endswith(")"):
            raise ValueError(f"Invalid action format: {action}")

        # Extract function name and arguments
        match = re.match(r"(\w+)\((.*)\)$", action)
        if not match:
            raise ValueError(f"Invalid action format: {action}")

        func_name, args_str = match.groups()

        # Parse arguments
        args = []
        kwargs = {}

        if args_str.strip():
            # Split by commas, but handle nested parentheses
            parts = []
            current = ""
            paren_depth = 0

            for char in args_str:
                if char == "," and paren_depth == 0:
                    parts.append(current.strip())
                    current = ""
                else:
                    if char == "(":
                        paren_depth += 1
                    elif char == ")":
                        paren_depth -= 1
                    current += char

            if current.strip():
                parts.append(current.strip())

            for part in parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    # Convert boolean strings
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    kwargs[key] = value
                else:
                    args.append(part)

        # Map function names to action structures
        return self._map_action(func_name, args, kwargs)

    def _map_action(self, func_name: str, args: list, kwargs: dict) -> dict:
        """Map function calls to JSON action format"""

        if func_name == "click":
            return {"action": "click", "target": args[0]}

        elif func_name == "type":
            result = {"action": "type", "target": args[0], "text": args[1] if len(args) > 1 else ""}
            if "enter" in kwargs:
                result["enter"] = kwargs["enter"]
            return result

        elif func_name == "hover":
            return {"action": "hover", "target": args[0]}

        elif func_name == "select":
            return {"action": "select", "target": args[0], "value": args[1] if len(args) > 1 else ""}

        elif func_name == "clear":
            return {"action": "clear", "target": args[0]}

        elif func_name == "press":
            result = {"action": "key_press", "key": args[0]}
            if "target" in kwargs:
                result["target"] = kwargs["target"]
            return result

        elif func_name == "goto":
            return {"action": "goto_url", "url": args[0]}

        elif func_name == "back":
            return {"action": "back"}

        elif func_name == "forward":
            return {"action": "forward"}

        elif func_name == "refresh":
            return {"action": "refresh"}

        elif func_name == "new_tab":
            result = {"action": "new_tab"}
            if len(args) > 0:
                result["url"] = args[0]
            return result

        elif func_name == "switch_tab":
            return {"action": "switch_tab", "tab_id": int(args[0])}

        elif func_name == "close_tab":
            return {"action": "close_tab", "tab_id": int(args[0])}

        else:
            raise ValueError(f"Unknown action: {func_name}")


class WebAgentREPL:
    """Interactive REPL for the Web Agent"""

    def __init__(self, cfg: DictConfig):
        self.cfg = cfg
        self.env = None
        self.parser = ActionParser()
        self.logger = logging.getLogger(__name__)

    async def start(self):
        """Start the REPL session"""
        print("🤖 RL Web Agent REPL")
        print("=" * 50)
        print("Human-friendly action format:")
        print("  click(element_id)")
        print("  type(element_id, text, enter=true)")
        print("  goto(url)")
        print("  hover(element_id)")
        print("  select(element_id, value)")
        print("  new_tab(url)")
        print("  switch_tab(tab_id)")
        print("")
        print("Special commands:")
        print("  help - Show this help")
        print("  obs - Get current observation")
        print("  exit - Exit REPL")
        print("  reset - Reset environment")
        print("=" * 50)

        # Setup environment
        print("🔧 Setting up environment...")
        await self._setup_env()
        print("✅ Environment ready!")
        print("")

        # Main REPL loop
        while True:
            try:
                command = await ainput("🌐 > ")
                command = command.strip()

                if not command:
                    continue

                if command.lower() in ["exit", "quit", "q"]:
                    break
                elif command.lower() == "help":
                    await self._show_help()
                elif command.lower() == "obs":
                    await self._show_observation()
                elif command.lower() == "reset":
                    await self._reset_env()
                else:
                    await self._execute_action(command)

            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

        # Cleanup
        if self.env:
            await self.env.close()

    async def _setup_env(self):
        """Initialize the web agent environment"""
        self.env = WebAgentEnv(self.cfg.environment)

        # Use fake task config from main.py
        fake_task = {"sites": ["shopping"], "task_id": 1, "require_login": False, "start_url": "http://metis.lti.cs.cmu.edu:7770", "intent": "Interactive testing session"}

        await self.env.setup(fake_task)

    async def _reset_env(self):
        """Reset the environment"""
        print("🔄 Resetting environment...")
        await self.env.reset()
        print("✅ Environment reset!")

    async def _show_help(self):
        """Show help information"""
        print(
            """
🆘 Available Actions:

Basic Interactions:
  click(element_id)                    - Click an element
  type(element_id, text)              - Type text into input
  type(element_id, text, enter=true)  - Type text and press Enter
  hover(element_id)                   - Hover over element
  select(element_id, value)           - Select dropdown option
  clear(element_id)                   - Clear input field

Navigation:
  goto(url)                           - Navigate to URL
  back()                              - Go back in history
  forward()                           - Go forward in history
  refresh()                           - Refresh page

Keyboard:
  press(key)                          - Press key globally
  press(key, target=element_id)       - Press key on element

Tab Management:
  new_tab()                           - Open new empty tab
  new_tab(url)                        - Open new tab with URL
  switch_tab(tab_id)                  - Switch to tab by ID
  close_tab(tab_id)                   - Close tab by ID

Special Commands:
  obs                                 - Show current page observation
  reset                               - Reset environment to start state
  help                                - Show this help
  exit                                - Exit REPL
        """
        )

    async def _show_observation(self):
        """Display current observation with detailed formatting"""
        try:
            obs = await self.env.observation()
            print("\n" + "=" * 80)
            print("📊 FULL OBSERVATION")
            print("=" * 80)

            # Basic page info
            print(f"🔗 URL: {self.env.page.url}")
            print(f"📑 Title: {await self.env.page.title()}")
            print("")

            # Clickable elements
            if obs.get("clickable_elements"):
                print(f"🖱️  CLICKABLE ELEMENTS ({len(obs['clickable_elements'])})")
                print("-" * 40)
                for i, elem_id in enumerate(obs["clickable_elements"], 1):
                    print(f"  {i:2d}. {elem_id}")
                print("")

            # Input elements with detailed info
            if obs.get("input_elements"):
                print(f"⌨️  INPUT ELEMENTS ({len(obs['input_elements'])})")
                print("-" * 40)
                for i, inp in enumerate(obs["input_elements"], 1):
                    elem_id = inp.get("id", "unnamed")
                    elem_type = inp.get("type", "text")
                    value = inp.get("value", "")
                    can_edit = inp.get("canEdit", True)
                    is_focused = inp.get("isFocused", False)

                    status_icons = []
                    if is_focused:
                        status_icons.append("🎯")
                    if not can_edit:
                        status_icons.append("🔒")

                    status = " ".join(status_icons)
                    print(f"  {i:2d}. {elem_id} [{elem_type}] {status}")
                    if value:
                        print(f"      Value: '{value[:50]}{'...' if len(value) > 50 else ''}'")
                print("")

            # Select elements
            if obs.get("select_elements"):
                print(f"📋 SELECT ELEMENTS ({len(obs['select_elements'])})")
                print("-" * 40)
                for i, sel in enumerate(obs["select_elements"], 1):
                    elem_id = sel.get("id", "unnamed")
                    value = sel.get("value", "")
                    multiple = sel.get("multiple", False)
                    selected_values = sel.get("selectedValues", [])

                    mult_indicator = " [MULTIPLE]" if multiple else ""
                    print(f"  {i:2d}. {elem_id}{mult_indicator}")
                    if multiple and selected_values:
                        print(f"      Selected: {', '.join(selected_values)}")
                    elif value:
                        print(f"      Selected: '{value}'")
                print("")

            # Forms
            if obs.get("forms"):
                print(f"📝 FORMS ({len(obs['forms'])})")
                print("-" * 40)
                for i, form in enumerate(obs["forms"], 1):
                    form_id = form.get("id", "unnamed")
                    submittable = form.get("isSubmittable", False)
                    status = "✅ Ready" if submittable else "❌ Invalid"
                    print(f"  {i:2d}. {form_id} - {status}")
                print("")

            # Tabs
            if obs.get("tabs"):
                print(f"🗂️  TABS ({len(obs['tabs'])})")
                print("-" * 40)
                for tab in obs["tabs"]:
                    active = "🟢 ACTIVE" if tab.get("is_active") else "⚪ inactive"
                    tab_title = tab.get("title", "Untitled")[:40]
                    print(f"  {tab.get('id'):2d}. {active} - {tab_title}")
                print("")

            # HTML preview (first 500 chars of processed HTML)
            if obs.get("html"):
                html_preview = obs["html"][:500]
                print("🌐 HTML PREVIEW")
                print("-" * 40)
                # Simple formatting to make it more readable
                html_preview = html_preview.replace("<", "\n  <").replace(">", ">\n  ")
                print(f"{html_preview[:300]}...")
                print(f"\n  [Total HTML length: {len(obs['html'])} characters]")

            print("=" * 80)

        except Exception as e:
            print(f"❌ Error getting observation: {e}")
            import traceback

            print(f"Full error: {traceback.format_exc()}")

    async def _execute_action(self, command: str):
        """Execute a user action"""
        try:
            # Check if it's already JSON format
            if command.strip().startswith("{"):
                action_json = command
            else:
                # Parse human-friendly format
                action_data = self.parser.parse(command)
                action_json = json.dumps(action_data)

            print(f"🚀 Executing: {action_json}")

            # Execute action
            result = await self.env.step(action_json)

            if result.get("error"):
                print(f"❌ Action failed: {result['error']}")
            else:
                print("✅ Action completed successfully!")

                # Show brief status update
                print(f"📍 Current URL: {self.env.page.url}")

                # If there was a tab operation, show tab info
                if "tab_id" in str(action_json):
                    tabs = result.get("tabs", [])
                    active_tab = next((t for t in tabs if t.get("is_active")), None)
                    if active_tab:
                        print(f"📋 Active tab: {active_tab.get('title', 'Untitled')}")

        except Exception as e:
            print(f"❌ Error executing action: {e}")
            self.logger.debug(f"Action parsing error for '{command}'", exc_info=True)


# Fake task config for testing (same as main.py)
FAKE_TASK_CONFIG = {"sites": ["shopping"], "task_id": 1, "require_login": False, "start_url": "http://metis.lti.cs.cmu.edu:7770", "intent": "Interactive REPL session"}


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    """Main entry point for the REPL"""
    logging.basicConfig(level=cfg.log_level)

    # Create and run REPL
    repl = WebAgentREPL(cfg)

    try:
        asyncio.run(repl.start())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")


if __name__ == "__main__":
    main()
