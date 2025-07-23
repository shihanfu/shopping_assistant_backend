#!/usr/bin/env python3
"""
Batch Agent - Run multiple WebAgent tasks concurrently
Run with: python -m rl_web_agent.entrypoints.batch_agent --task_ids 1,2,3,4,5 --max_concurrent 3
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from hydra import compose, initialize
from hydra.core.global_hydra import GlobalHydra

from rl_web_agent.agent import create_web_agent
from rl_web_agent.env import WebAgentEnv

# Logger will be configured in main() after loading config
logger = logging.getLogger(__name__)


class TaskTracer:
    """Tracks task execution with observation and action traces"""

    def __init__(self, task_id: str, output_dir: Path):
        self.task_id = task_id
        self.output_dir = output_dir
        self.trace = []
        self.start_time = None
        self.end_time = None
        self.task_config = None
        self.result = None

    def start_task(self, task_config: dict):
        """Initialize task tracking"""
        self.start_time = datetime.now()
        self.task_config = task_config
        self.trace = []

    def add_step(self, step_num: int, observation: dict, action: dict, llm_response: str):
        """Add a step to the trace"""
        step_data = {"step": step_num, "timestamp": datetime.now().isoformat(), "observation": observation, "action": action, "llm_response": llm_response}
        self.trace.append(step_data)

    def finish_task(self, result: dict):
        """Finalize task tracking"""
        self.end_time = datetime.now()
        self.result = result

    def save_results(self):
        """Save trace and results to files"""
        task_dir = self.output_dir / f"task_{self.task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        # Save trace
        trace_file = task_dir / "trace.json"
        trace_data = {
            "task_id": self.task_id,
            "task_config": self.task_config,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else None,
            "trace": self.trace,
        }

        with open(trace_file, "w") as f:
            json.dump(trace_data, f, indent=2, default=str)

        # Save result
        result_file = task_dir / "result.json"
        result_data = {
            "task_id": self.task_id,
            "task_config": self.task_config,
            "result": self.result,
            "execution_time": {"start": self.start_time.isoformat() if self.start_time else None, "end": self.end_time.isoformat() if self.end_time else None, "duration_seconds": (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else None},
            "trace_summary": {"total_steps": len(self.trace), "final_score": self.result.get("score", 0.0) if self.result else 0.0, "success": self.result.get("success", False) if self.result else False, "terminated": self.result.get("terminated", False) if self.result else False},
        }

        with open(result_file, "w") as f:
            json.dump(result_data, f, indent=2, default=str)

        logger.info(f"Saved results for task {self.task_id} to {task_dir}")


async def run_single_task(task_id: str, task_config: dict, cfg: Any, output_dir: Path, semaphore: asyncio.Semaphore) -> dict:
    """Run a single task with tracing and result saving"""

    async with semaphore:  # Control concurrency
        tracer = TaskTracer(task_id, output_dir)
        temp_user_data_dir = None
        temp_cache_dir = None

        try:
            logger.info(f"Starting task {task_id}: {task_config.get('intent', 'Unknown intent')}")

            # Create temporary directories for browser data (unique per task)
            temp_user_data_dir = tempfile.mkdtemp(prefix=f"webagent_task_{task_id}_userdata_")
            temp_cache_dir = tempfile.mkdtemp(prefix=f"webagent_task_{task_id}_cache_")

            # Clone config for this task to avoid conflicts
            import copy

            task_cfg = copy.deepcopy(cfg)
            task_cfg.environment.browser.user_data_dir = temp_user_data_dir
            task_cfg.environment.browser.cache_dir = temp_cache_dir

            # Create environment and agent using the proper factory functions
            env = WebAgentEnv(task_cfg.environment)
            agent = await create_web_agent(task_cfg.llm)

            # Start tracing
            tracer.start_task(task_config)

            try:
                # Setup environment with task
                await env.setup(task_config)
                logger.info(f"Task {task_id}: Environment setup complete")

                # Use the WebAgent's run_task method - it handles everything!
                result = await agent.run_task(env, task_config["intent"], max_steps=50)

                # Extract trace information from agent's conversation and action history
                trace_steps = []
                conversation_pairs = []

                # Group conversation history into user/assistant pairs
                for i in range(0, len(agent.conversation_history), 2):
                    if i + 1 < len(agent.conversation_history):
                        user_msg = agent.conversation_history[i]
                        assistant_msg = agent.conversation_history[i + 1]
                        conversation_pairs.append((user_msg, assistant_msg))

                # Create trace steps from conversation pairs and action history
                for step_num, ((user_msg, assistant_msg), action) in enumerate(zip(conversation_pairs, agent.action_history, strict=False), 1):
                    # Extract observation from user message (it contains the formatted observation)
                    observation_text = user_msg["content"]

                    # Create a simplified observation dict for tracing
                    # (The full observation is embedded in the user message text)
                    trace_observation = {"step": step_num, "observation_prompt": observation_text, "note": "Full observation data is embedded in the observation_prompt"}

                    step_data = {
                        "step": step_num,
                        "timestamp": datetime.now().isoformat(),  # Approximate timestamp
                        "observation": trace_observation,
                        "action": action,
                        "llm_response": assistant_msg["content"],
                    }
                    trace_steps.append(step_data)

                # Add all trace steps to tracer
                tracer.trace = trace_steps

                # Finish tracing
                tracer.finish_task(result)

                logger.info(f"Task {task_id} completed - Success: {result['success']}, Score: {result['score']}")

                return result

            finally:
                await env.close()
                await agent.close()  # Clean up agent resources

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            # Save error result
            error_result = {"success": False, "score": 0.0, "answer": "", "steps": 0, "terminated": False, "error": str(e)}
            tracer.finish_task(error_result)
            return error_result

        finally:
            # Save results regardless of success/failure
            try:
                tracer.save_results()
            except Exception as e:
                logger.error(f"Failed to save results for task {task_id}: {e}")

            # Clean up temporary directories
            for temp_dir in [temp_user_data_dir, temp_cache_dir]:
                if temp_dir and os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as e:
                        logger.warning(f"Failed to clean up {temp_dir}: {e}")


async def run_batch_tasks(task_ids: list[str], tasks_dir: Path, output_dir: Path, max_concurrent: int = 3):
    """Run multiple tasks concurrently"""

    # Load configuration
    config_dir = "../../rl_web_agent/conf"
    config_name = "config"

    if GlobalHydra().is_initialized():
        GlobalHydra.instance().clear()

    with initialize(version_base=None, config_path=config_dir):
        cfg = compose(config_name=config_name)

    # Configure logging
    log_level = getattr(logging, cfg.log_level.upper())
    logging.basicConfig(level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Suppress verbose logging
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiobotocore").setLevel(logging.WARNING)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load task configurations
    task_configs = {}
    for task_id in task_ids:
        task_file = tasks_dir / f"{task_id}.json"
        if not task_file.exists():
            logger.error(f"Task file not found: {task_file}")
            continue

        with open(task_file) as f:
            task_configs[task_id] = json.load(f)

    if not task_configs:
        logger.error("No valid task configurations found")
        return

    logger.info(f"Starting batch execution of {len(task_configs)} tasks with max_concurrent={max_concurrent}")

    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)

    # Create tasks
    tasks = []
    for task_id, task_config in task_configs.items():
        task = asyncio.create_task(run_single_task(task_id, task_config, cfg, output_dir, semaphore))
        tasks.append((task_id, task))

    # Run all tasks
    results = {}
    completed = 0
    total = len(tasks)

    for task_id, task in tasks:
        try:
            result = await task
            results[task_id] = result
            completed += 1
            logger.info(f"Progress: {completed}/{total} tasks completed")
        except Exception as e:
            logger.error(f"Task {task_id} failed with exception: {e}")
            results[task_id] = {"success": False, "error": str(e)}
            completed += 1

    # Save batch summary
    summary_file = output_dir / "batch_summary.json"
    summary = {"total_tasks": total, "completed_tasks": completed, "max_concurrent": max_concurrent, "results": results, "success_count": sum(1 for r in results.values() if r.get("success", False)), "execution_time": datetime.now().isoformat()}

    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    logger.info(f"Batch execution completed. Results saved to {output_dir}")
    logger.info(f"Success rate: {summary['success_count']}/{total} ({summary['success_count'] / total * 100:.1f}%)")


def signal_handler(signum, frame):
    """Handle interrupt signals gracefully"""
    logger.info(f"Received signal {signum}. Cleaning up...")
    sys.exit(0)


def main():
    """Main entry point for batch agent execution"""
    parser = argparse.ArgumentParser(description="Run multiple WebAgent tasks concurrently")
    parser.add_argument("--task_ids", required=True, help="Comma-separated list of task IDs")
    parser.add_argument("--tasks_dir", default="thirdparty/webarena/config_files", help="Directory containing task JSON files")
    parser.add_argument("--output_dir", default="results", help="Output directory for results and traces")
    parser.add_argument("--max_concurrent", type=int, default=3, help="Maximum number of concurrent tasks")

    args = parser.parse_args()

    # Parse task IDs
    task_ids = [tid.strip() for tid in args.task_ids.split(",")]

    # Convert paths
    tasks_dir = Path(args.tasks_dir)
    output_dir = Path(args.output_dir)

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(run_batch_tasks(task_ids, tasks_dir, output_dir, args.max_concurrent))
    except KeyboardInterrupt:
        logger.info("Batch execution interrupted by user")
    except Exception as e:
        logger.error(f"Batch execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
