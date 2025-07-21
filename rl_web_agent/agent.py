"""
Web Agent that uses LLM providers to complete web-based tasks with chain-of-thought reasoning.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from omegaconf import DictConfig

from rl_web_agent.env import WebAgentEnv
from rl_web_agent.llm import create_llm_client


class WebAgent:
    """
    LLM-powered web agent that can complete tasks using chain-of-thought reasoning.
    """

    def __init__(self, llm_config: DictConfig):
        """
        Initialize the web agent with LLM configuration.

        Args:
            llm_config: Configuration for LLM provider
        """
        self.llm_config = llm_config
        self.llm_provider = None
        self.logger = logging.getLogger(__name__)
        self.max_steps = 50  # Maximum number of actions to prevent infinite loops

        # Conversation history - each user message is an observation, each assistant message is an action
        self.conversation_history = []

        # Load prompt template
        prompt_path = Path(__file__).parent / "prompts" / "chain_of_thought.txt"
        with open(prompt_path) as f:
            self.prompt_template = f.read()

    async def setup(self):
        """Initialize the LLM provider"""
        self.llm_provider = await create_llm_client(self.llm_config)

    async def close(self):
        """Clean up resources"""
        if self.llm_provider:
            await self.llm_provider.close()

    def _create_chain_of_thought_prompt(self, objective: str, observation: dict[str, Any], previous_action: str = "None") -> str:
        """
        Create a chain-of-thought prompt for web navigation.

        Args:
            objective: The task objective
            observation: Current page observation
            previous_action: Previous action taken

        Returns:
            Formatted prompt string
        """
        # Extract key elements from observation
        url = observation["tabs"][0]["url"] if observation["tabs"] else "unknown"

        # Debug: Check observation structure
        self.logger.debug(f"Observation keys: {list(observation.keys())}")
        if "error" in observation and observation["error"]:
            self.logger.warning(f"Observation contains error: {observation['error']}")

        # Build simplified observation representation
        try:
            obs_text = self._build_observation_text(observation)
        except Exception as e:
            self.logger.error(f"Error building observation text: {e}")
            self.logger.error(f"Observation content: {observation}")
            raise

        # Format the prompt template
        prompt = self.prompt_template.format(url=url, objective=objective, previous_action=previous_action, observation=obs_text)

        return prompt

    def _build_observation_text(self, observation: dict[str, Any]) -> str:
        """
        Build a simplified observation representation from the observation data.

        Args:
            observation: Current page observation

        Returns:
            Formatted observation string
        """
        obs_parts = []

        # Add clickable elements
        clickable_elements = observation["clickable_elements"]
        if clickable_elements:
            obs_parts.append("CLICKABLE ELEMENTS:")
            for element_id in clickable_elements:
                obs_parts.append(f"  - {element_id} (clickable)")

        # Add hoverable elements
        hoverable_elements = observation["hoverable_elements"]
        if hoverable_elements:
            obs_parts.append("\nHOVERABLE ELEMENTS (may have tooltips/dropdowns):")
            for element_id in hoverable_elements:
                obs_parts.append(f"  - {element_id} (hoverable)")

        # Add input elements
        input_elements = observation["input_elements"]
        if input_elements:
            obs_parts.append("\nINPUT ELEMENTS:")
            for element in input_elements:
                element_id = element["id"]
                element_type = element["type"]
                placeholder = element.get("placeholder", "")  # Keep .get() for optional HTML attributes
                value = element.get("value", "")  # Keep .get() for optional HTML attributes

                desc = f"  - {element_id} (type: {element_type}"
                if placeholder:
                    desc += f", placeholder: '{placeholder}'"
                if value:
                    desc += f", current value: '{value}'"
                desc += ")"
                obs_parts.append(desc)

        # Add a simplified view of the page content
        try:
            html_content = observation["html"]  # This should always be present
            # Extract key visible text content (simplified)
            text_content = self._extract_key_text(html_content)
            if text_content:
                obs_parts.append(f"\nVISIBLE TEXT:\n{text_content}")
        except KeyError as e:
            self.logger.error(f"Missing 'html' key in observation: {e}")
            obs_parts.append(f"\nERROR: No HTML content available - {e}")

        return "\n".join(obs_parts) if obs_parts else "No interactive elements found on the page."

    def _build_observation_message(self, observation: dict[str, Any]) -> str:
        """
        Build an observation message for the conversation context.

        Args:
            observation: Current page observation

        Returns:
            Formatted observation message
        """
        obs_parts = []

        # Add current URL and tabs info
        if observation.get("tabs"):
            current_tab = next((tab for tab in observation["tabs"] if tab.get("is_active")), observation["tabs"][0])
            obs_parts.append(f"CURRENT PAGE: {current_tab['url']}")
            obs_parts.append(f"PAGE TITLE: {current_tab['title']}")

            if len(observation["tabs"]) > 1:
                obs_parts.append(f"OPEN TABS: {len(observation['tabs'])}")

        # Add error information if present
        if observation.get("error"):
            obs_parts.append(f"ERROR: {observation['error']}")

        # Add termination status
        if observation.get("terminated"):
            obs_parts.append("STATUS: Task terminated")
            if observation.get("score") is not None:
                obs_parts.append(f"SCORE: {observation['score']}")
            if observation.get("model_answer"):
                obs_parts.append(f"FINAL ANSWER: {observation['model_answer']}")

        # Add interactive elements
        obs_parts.append("\n" + self._build_observation_text(observation))

        return "\n".join(obs_parts)

    def _extract_key_text(self, html_content: str) -> str:
        """Extract key visible text from HTML content."""
        # This is a simplified extraction
        # In a real implementation, you might use BeautifulSoup or similar

        # Remove script and style content
        text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Clean up whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def _parse_action(self, response: str) -> str:
        """
        Parse the LLM response to extract the JSON action.

        Args:
            response: LLM response containing thought and action

        Returns:
            JSON action string
        """
        # Look for ACTION: line
        action_match = re.search(r"ACTION:\s*(.+)", response, re.IGNORECASE | re.DOTALL)
        if not action_match:
            # Fallback: look for JSON-like patterns
            json_pattern = r'\{[^}]*"action"[^}]*\}'
            json_match = re.search(json_pattern, response)
            if json_match:
                return json_match.group(0)

            # If no clear action found, return terminate
            return json.dumps({"action": "terminate", "answer": "Unable to determine next action"})

        action_text = action_match.group(1).strip()

        # Try to extract JSON from the action text
        json_pattern = r'\{[^}]*"action"[^}]*\}'
        json_match = re.search(json_pattern, action_text)
        if json_match:
            return json_match.group(0)

        # If not valid JSON, try to convert to JSON
        return self._convert_to_json_action(action_text)

    def _convert_to_json_action(self, action_text: str) -> str:
        """Convert human-readable action to JSON format."""
        action_text = action_text.strip()

        # Handle common action patterns and convert to JSON
        if action_text.startswith("terminate "):
            answer = action_text[10:].strip().strip("\"'")
            return json.dumps({"action": "terminate", "answer": answer})
        elif action_text.startswith("click "):
            target = action_text[6:].strip()
            return json.dumps({"action": "click", "target": target})
        elif action_text.startswith("type "):
            parts = action_text[5:].strip().split(" ", 1)
            if len(parts) >= 2:
                target = parts[0]
                text = parts[1].strip().strip("\"'")
                return json.dumps({"action": "type", "target": target, "text": text, "enter": True})
            else:
                return json.dumps({"action": "terminate", "answer": "Invalid type command"})
        elif action_text.startswith("goto_url "):
            url = action_text[9:].strip().strip("\"'")
            return json.dumps({"action": "goto_url", "url": url})
        elif action_text.startswith("scroll "):
            direction = action_text[7:].strip().lower()
            return json.dumps({"action": "scroll", "direction": direction})
        elif action_text == "back":
            return json.dumps({"action": "back"})
        elif action_text == "forward":
            return json.dumps({"action": "forward"})
        else:
            # If we can't parse it, terminate with the text as answer
            return json.dumps({"action": "terminate", "answer": f"Unknown action: {action_text}"})

    async def run_task(self, env: WebAgentEnv, objective: str, max_steps: int = None) -> dict[str, Any]:
        """
        Run a complete task using the web agent with conversation context.

        Args:
            env: WebAgentEnv instance (already set up)
            objective: Task objective description
            max_steps: Maximum number of steps (overrides default)

        Returns:
            Dictionary with task results including final score, answer, and step count
        """
        if not self.llm_provider:
            raise RuntimeError("LLM provider not initialized. Call setup() first.")

        max_steps = max_steps or self.max_steps
        step_count = 0

        self.logger.info(f"Starting task: {objective}")

        # Initialize conversation with system message and objective
        self.conversation_history = [
            {
                "role": "system",
                "content": f"You are a web automation agent. Your objective is: {objective}\n\n"
                + "I will provide you with observations from a web page, and you must respond with JSON actions.\n"
                + "Each of my messages contains the current state of the web page.\n"
                + "Each of your responses should be a JSON action to execute.\n\n"
                + "Available actions:\n"
                + '- {"action": "click", "target": "element_id"}\n'
                + '- {"action": "type", "target": "element_id", "text": "text to type", "enter": true/false}\n'
                + '- {"action": "goto_url", "url": "https://example.com"}\n'
                + '- {"action": "scroll", "direction": "up/down"}\n'
                + '- {"action": "back"}\n'
                + '- {"action": "forward"}\n'
                + '- {"action": "terminate", "answer": "final answer"}\n\n'
                + "Always respond with valid JSON.",
            }
        ]

        # Get initial observation
        observation = await env.observation()

        try:
            for step in range(max_steps):
                step_count += 1
                self.logger.info(f"Step {step_count}: Processing observation")

                # Check if task is already terminated
                if observation.get("terminated", False):
                    self.logger.info("Task already terminated by environment")
                    break

                # Add observation as user message to conversation
                observation_text = self._build_observation_message(observation)
                self.conversation_history.append({"role": "user", "content": observation_text})

                # Get LLM response with full conversation context
                self.logger.info(f"Step {step_count}: Querying LLM with conversation context")
                response = await self.llm_provider.complete(self.conversation_history)

                self.logger.info(f"LLM Response: {response[:200]}...")

                # Parse action from response
                try:
                    action_json = self._parse_action(response)
                except Exception as e:
                    self.logger.error(f"Error parsing action from response: {e}")
                    self.logger.error(f"Full LLM response: {response}")
                    raise

                self.logger.info(f"Step {step_count}: Executing action: {action_json}")

                # Add action as assistant message to conversation
                self.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": action_json,  # Store the action JSON as the assistant response
                    }
                )

                # Execute action and get next observation
                observation = await env.step(action_json)

                # Check if task is terminated after step
                if observation["terminated"]:
                    self.logger.info("Task terminated")
                    break

                # Brief pause to allow page to update
                await asyncio.sleep(0.5)

            # Get final results
            final_observation = await env.observation()
            final_score = final_observation["score"]
            final_answer = final_observation["model_answer"]
            terminated = final_observation["terminated"]

            result = {"success": terminated and final_score > 0.0, "score": final_score, "answer": final_answer, "steps": step_count, "terminated": terminated, "max_steps_reached": step_count >= max_steps}

            self.logger.info(f"Task completed: {result}")
            return result

        except Exception as e:
            import traceback

            self.logger.error(f"Error during task execution: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return {"success": False, "score": 0.0, "answer": f"Error: {str(e)}", "steps": step_count, "terminated": False, "max_steps_reached": False, "error": str(e)}

    async def run_task_from_config(self, env_config: DictConfig, task_config: dict[str, Any]) -> dict[str, Any]:
        """
        Run a task from configuration files.

        Args:
            env_config: Environment configuration
            task_config: Task configuration with objective and evaluation

        Returns:
            Dictionary with task results
        """
        env = WebAgentEnv(env_config)

        try:
            await env.setup(task_config)

            # Extract objective from task config
            objective = task_config.get("intent", "Complete the given task")

            # Run the task
            result = await self.run_task(env, objective)

            return result

        finally:
            await env.close()


async def create_web_agent(llm_config: DictConfig) -> WebAgent:
    """
    Create and initialize a WebAgent.

    Args:
        llm_config: LLM configuration

    Returns:
        Initialized WebAgent instance
    """
    agent = WebAgent(llm_config)
    await agent.setup()
    return agent
