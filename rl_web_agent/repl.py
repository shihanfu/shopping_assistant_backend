#!/usr/bin/env python3
"""
Human-friendly REPL interface for the RL Web Agent.
Translates simple function calls to JSON actions for easier manual testing.
"""

import asyncio
import json
import logging
import re
import shutil
import sys
from pathlib import Path

import hydra
from omegaconf import DictConfig
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

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
        self.temp_user_data_dir = None
        self.session = PromptSession(history=FileHistory(".repl_history"))

    async def _async_input(self, prompt_text: str) -> str:
        """Async input using prompt-toolkit with proper signal handling"""
        with patch_stdout():
            try:
                return await self.session.prompt_async(prompt_text)
            except (KeyboardInterrupt, EOFError):
                raise KeyboardInterrupt() from None

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
        self.obs = await self._setup_env()
        print("✅ Environment ready!")
        print("")

        # Show initial observation
        await self._show_observation()

        # Main REPL loop
        while True:
            try:
                command = await self._async_input("🌐 > ")
                command = command.strip()

                if not command:
                    continue

                if command.lower() in ["exit", "quit", "q"]:
                    break
                elif command.lower() == "help":
                    await self._show_help()
                elif command.lower() == "obs":
                    self.obs = await self.env.observation()
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

        # Clean up temporary user data directory
        if self.temp_user_data_dir and Path(self.temp_user_data_dir).exists():
            try:
                shutil.rmtree(self.temp_user_data_dir)
                self.logger.debug(f"Cleaned up temp user data dir: {self.temp_user_data_dir}")
            except Exception as e:
                self.logger.debug(f"Failed to cleanup temp dir: {e}")

        # History is automatically saved by PromptSession

    async def _setup_env(self):
        """Initialize the web agent environment"""
        # For REPL, use the persistent browser_session directory directly
        # This ensures cache persists across sessions
        session_dir = Path(self.cfg.environment.browser.user_data_dir).resolve()
        session_dir.mkdir(parents=True, exist_ok=True)

        # Don't use temporary directory for REPL - use the configured session dir
        # This way cache and session data both persist
        self.temp_user_data_dir = str(session_dir)

        self.env = WebAgentEnv(self.cfg.environment)

        # Use fake task config from main.py
        fake_task = {"sites": ["shopping"], "task_id": 1, "require_login": False, "start_url": "http://metis.lti.cs.cmu.edu:7770", "intent": "Interactive testing session"}

        obs = await self.env.setup(fake_task)
        return obs

    async def _reset_env(self):
        """Reset the environment"""
        print("🔄 Resetting environment...")
        self.obs = await self.env.reset()
        print("✅ Environment reset!")
        print("")
        # Auto-observe after reset
        await self._show_observation()

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

    def _safe_print(self, text: str):
        """Print with error handling for blocking I/O"""
        try:
            print(text)
            sys.stdout.flush()
        except BlockingIOError:
            # If output is blocked, try writing smaller chunks
            try:
                for chunk in [text[i : i + 100] for i in range(0, len(text), 100)]:
                    print(chunk, end="")
                    sys.stdout.flush()
                print()  # Final newline
            except Exception:
                # Last resort - just skip this output
                print("⚠️  Output truncated due to I/O blocking")

    async def _show_observation(self):
        """Display current observation with detailed formatting"""
        try:
            obs = self.obs
            if obs is None:
                self._safe_print("\n" + "=" * 80)
                self._safe_print("📊 FULL OBSERVATION")
                self._safe_print("=" * 80)
                self._safe_print("❌ No observation data available")
                return

            self._safe_print("\n" + "=" * 80)
            self._safe_print("📊 FULL OBSERVATION")
            self._safe_print("=" * 80)

            # Basic page info (only if env.page exists)
            if self.env and self.env.page:
                self._safe_print(f"🔗 URL: {self.env.page.url}")
                self._safe_print(f"📑 Title: {await self.env.page.title()}")
            else:
                self._safe_print("🔗 URL: Not available")
                self._safe_print("📑 Title: Not available")
            self._safe_print("")

            # HTML - Show full HTML first
            if obs.get("html"):
                self._safe_print("🌐 FULL HTML")
                self._safe_print("-" * 40)
                try:
                    # Simple HTML pretty printing with regex-based indentation
                    html = obs["html"]
                    # Add newlines after > and before <
                    html = re.sub(r">([^<\s])", r">\n\1", html)
                    html = re.sub(r"([^>\s])<", r"\1\n<", html)
                    html = re.sub(r"><", r">\n<", html)

                    # Split into lines and add indentation
                    lines = html.split("\n")
                    indented_lines = []
                    indent_level = 0

                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue

                        # Decrease indent for closing tags
                        if line.startswith("</"):
                            indent_level = max(0, indent_level - 1)

                        # Add indented line
                        indented_lines.append("  " * indent_level + line)

                        # Increase indent for opening tags (but not self-closing or closing tags)
                        if line.startswith("<") and not line.startswith("</") and not line.endswith("/>") and not any(tag in line for tag in ["<br>", "<img", "<input", "<meta", "<link"]):
                            indent_level += 1

                    self._safe_print("\n".join(indented_lines))
                except Exception as e:
                    # Fallback to raw HTML if pretty printing fails
                    self._safe_print(f"<!-- Pretty print failed: {e} -->")
                    self._safe_print(obs["html"])
                self._safe_print("")

            # Clickable elements
            if obs.get("clickable_elements"):
                self._safe_print(f"🖱️  CLICKABLE ELEMENTS ({len(obs['clickable_elements'])})")
                self._safe_print("-" * 40)
                for i, elem_id in enumerate(obs["clickable_elements"], 1):
                    self._safe_print(f"  {i:2d}. {elem_id}")
                self._safe_print("")

            # Input elements with detailed info
            if obs.get("input_elements"):
                self._safe_print(f"⌨️  INPUT ELEMENTS ({len(obs['input_elements'])})")
                self._safe_print("-" * 40)
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
                    self._safe_print(f"  {i:2d}. {elem_id} [{elem_type}] {status}")
                    if value:
                        # Truncate long values to prevent blocking
                        safe_value = value[:30] + ("..." if len(value) > 30 else "")
                        self._safe_print(f"      Value: '{safe_value}'")
                self._safe_print("")

            # Tabs
            if obs.get("tabs"):
                self._safe_print(f"🗂️  TABS ({len(obs['tabs'])})")
                self._safe_print("-" * 40)
                for tab in obs["tabs"]:
                    active = "🟢 ACTIVE" if tab.get("is_active") else "⚪ inactive"
                    tab_title = tab.get("title", "Untitled")[:40]
                    self._safe_print(f"  {tab.get('id'):2d}. {active} - {tab_title}")
                self._safe_print("")

            self._safe_print("=" * 80)

        except Exception as e:
            self._safe_print(f"❌ Error getting observation: {e}")
            import traceback

            # Truncate traceback to prevent blocking
            tb_lines = traceback.format_exc().split("\n")[:10]
            self._safe_print(f"Error details: {' '.join(tb_lines)}")

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
            self.obs = result

            if result.get("error"):
                print(f"❌ Action failed: {result['error']}")
            else:
                print("✅ Action completed successfully!")

            # Auto-observe after every action
            await self._show_observation()

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
