# Copyright 2023-2024 SGLang Team
# Copyright 2025 ModelBest Inc. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import ast
from typing import Any, Optional, Tuple
from uuid import uuid4

from verl.utils.reward_score import gsm8k

from .base_tool import BaseTool
from .schemas import OpenAIFunctionToolSchema

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("VERL_LOGGING_LEVEL", "WARN"))


class Gsm8kTool(BaseTool):
    """A demo tool for calculating the reward of gsm8k.

    - `to_openai_function_tool_schema`: return the tool schema in OpenAI format.
    - `create`: create a tool instance for a trajectory.
    - `execute`: execute the tool.
    - `calc_reward`: calculate the reward respect to tool state.
    - `release`: release the tool instance.
    """

    def __init__(self, config: dict, tool_schema: OpenAIFunctionToolSchema):
        """
        _tool_schema = OpenAIFunctionToolSchema.model_validate({
            "type": "function",
            "function": {
                "name": "check_gsm8k_answer",
                "description": "A tool for checking the answer of gsm8k. (1.0 if parsed answer is correct, 0.0 if parsed answer is incorrect or not correctly parsed)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                            "type": "string",
                            "description": "The model's answer to the GSM8K math problem, must be the final result, i.e. no calculation expression is allowed in the answer.",
                        },
                    },
                    "required": ["answer"],
                },
            }
        })
        """
        super().__init__(config, tool_schema)
        self._instance_dict = {}

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return self.tool_schema

    async def create(self, instance_id: Optional[str] = None, ground_truth: Optional[str] = None, **kwargs) -> str:
        if instance_id is None:
            instance_id = str(uuid4())
        self._instance_dict[instance_id] = {
            "response": "",
            "ground_truth": ground_truth,
            "reward": 0.0,
        }
        return instance_id

    async def execute(self, instance_id: str, parameters: dict[str, Any], **kwargs) -> Tuple[str, float, dict]:
        answer = parameters.get("answer", "")
        if not isinstance(answer, str):
            answer = str(answer)
        # if is not a float point number, return 0.0
        # if not answer.replace(".", "").isdigit():
        #     return f"Current parsed {answer=} is not a float point number, invalid answer.", 0.0, {}

        if answer.startswith("#### "):
            self._instance_dict[instance_id]["response"] = answer
        else:
            self._instance_dict[instance_id]["response"] = "#### " + answer

        reward = await self.calc_reward(instance_id)
        # penalty for non improved answer submission
        # tool_reward = 0.0 if reward > self._instance_dict[instance_id]["reward"] else -0.05
        # update the reward
        self._instance_dict[instance_id]["reward"] = reward
        logger.info(f"Tool called with {instance_id=} {answer=} ground_truth={self._instance_dict[instance_id]['ground_truth']}  {reward=}")

        return f"Your answer is {answer=} correct={reward},", reward, {}

    async def calc_reward(self, instance_id: str, **kwargs) -> float:
        return gsm8k.compute_score(
            self._instance_dict[instance_id]["response"],
            self._instance_dict[instance_id]["ground_truth"],
            method="flexible",
            format_score=0.0,
            score=1.0,
        )

    async def release(self, instance_id: str, **kwargs) -> None:
        del self._instance_dict[instance_id]


class CalculatorTool(BaseTool):
    """A calculator tool for evaluating mathematical expressions.

    - `get_openai_tool_schema`: return the tool schema in OpenAI format.
    - `create`: create a tool instance for a trajectory.
    - `execute`: execute the tool by evaluating the mathematical expression.
    - `calc_reward`: calculate the reward (always 1.0 for successful calculations).
    - `release`: release the tool instance.
    """

    def __init__(self, config: dict, tool_schema: OpenAIFunctionToolSchema):
        super().__init__(config, tool_schema)
        self._instance_dict = {}

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return self.tool_schema

    async def create(self, instance_id: Optional[str] = None, **kwargs) -> str:
        if instance_id is None:
            instance_id = str(uuid4())
        self._instance_dict[instance_id] = {
            "expression": "",
            "result": None,
            "error": None,
            "reward": 0.0,
        }
        return instance_id

    def _is_safe_expression(self, expr: str) -> bool:
        """Check if the expression contains only safe mathematical operations."""
        try:
            tree = ast.parse(expr, mode='eval')
            for node in ast.walk(tree):
                # Allow only specific node types for mathematical operations
                if isinstance(node, (ast.Expression, ast.Name, ast.Constant, 
                                   ast.BinOp, ast.UnaryOp, ast.Call,
                                   # Mathematical operators
                                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
                                   ast.UAdd, ast.USub, ast.Not, ast.Invert)):
                    # Check for function calls - only allow math module functions
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            # Allow common math functions
                            allowed_functions = {
                                'abs', 'round', 'min', 'max', 'pow', 'sum',
                                'int', 'float', 'complex'
                            }
                            if node.func.id not in allowed_functions:
                                return False
                        elif isinstance(node.func, ast.Attribute):
                            # Allow math module functions
                            if (isinstance(node.func.value, ast.Name) and 
                                node.func.value.id == 'math'):
                                return True
                            return False
                else:
                    return False
            return True
        except SyntaxError:
            return False

    def _evaluate_expression(self, expr: str) -> Tuple[Any, Optional[str]]:
        """Safely evaluate a mathematical expression."""
        try:
            # Clean the expression
            expr = expr.strip()
            
            # Check if expression is safe
            if not self._is_safe_expression(expr):
                return None, "Expression contains unsafe operations"
            
            # Import math module for mathematical functions
            import math
            
            # Create a safe namespace with only mathematical operations
            safe_namespace = {
                '__builtins__': {},
                'math': math,
                'abs': abs,
                'round': round,
                'min': min,
                'max': max,
                'pow': pow,
                'sum': sum,
                'int': int,
                'float': float,
                'complex': complex,
            }
            
            # Evaluate the expression
            result = eval(expr, safe_namespace)
            return result, None
            
        except Exception as e:
            return None, f"Error evaluating expression: {str(e)}"

    async def execute(self, instance_id: str, parameters: dict[str, Any], **kwargs) -> Tuple[str, float, dict]:
        expression = parameters.get("expression", "")
        if not isinstance(expression, str):
            expression = str(expression)
        logger.info(f"Calculator executed with {instance_id=} {expression=}")
        # Store the expression
        self._instance_dict[instance_id]["expression"] = expression
        
        # Evaluate the expression
        result, error = self._evaluate_expression(expression)
        
        if error:
            self._instance_dict[instance_id]["error"] = error
            self._instance_dict[instance_id]["result"] = None
            self._instance_dict[instance_id]["reward"] = 0.0
            logger.warning(f"Calculator error with {instance_id=} {expression=}: {error}")
            return f"Error: {error}", 0.0, {}
        
        # Store the result
        self._instance_dict[instance_id]["result"] = result
        self._instance_dict[instance_id]["error"] = None
        self._instance_dict[instance_id]["reward"] = 0.0
        
        logger.info(f"Calculator executed with {instance_id=} {expression=} result={result}")
        
        return f"The result of {expression} is {result}", 0.0, {}

    async def calc_reward(self, instance_id: str, **kwargs) -> float:
        """Calculate reward - 1.0 for successful calculations, 0.0 for errors."""
        return self._instance_dict[instance_id]["reward"]

    async def release(self, instance_id: str, **kwargs) -> None:
        del self._instance_dict[instance_id]
