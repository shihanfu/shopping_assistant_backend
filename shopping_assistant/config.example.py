# Configuration file for shopping assistant
import os
from pathlib import Path

# Get the directory where this config file is located
CONFIG_DIR = Path(__file__).parent

# Model configuration
MODEL_CONFIG = {
    "model_id": "arn:aws:bedrock:us-east-1:248189905876:inference-profile/us.anthropic.claude-3-5-haiku-20241022-v1:0",
    "temperature": 0.5,
    "top_k": 200,
    "region": "us-east-1"
}

# Server configuration
SERVER_CONFIG = {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": False
}

# Search configuration
SEARCH_CONFIG = {
    "base_url": "http://52.91.223.130:7770/catalogsearch/result/?q={query}"
}

# Logging configuration
LOGGING_CONFIG = {
    "level": "INFO",
    "log_file": "./shopping_assistant.log"
}

def get_model_id():
    """Get model ID from config or environment variable"""
    return os.getenv("SHOPPING_MODEL_ID", MODEL_CONFIG["model_id"])

def get_temperature():
    """Get temperature from config or environment variable"""
    return float(os.getenv("SHOPPING_TEMPERATURE", MODEL_CONFIG["temperature"]))

def get_top_k():
    """Get top_k from config or environment variable"""
    return int(os.getenv("SHOPPING_TOP_K", MODEL_CONFIG["top_k"]))

def get_server_port():
    """Get server port from config or environment variable"""
    return int(os.getenv("SHOPPING_PORT", SERVER_CONFIG["port"]))

def get_server_host():
    """Get server host from config or environment variable"""
    return os.getenv("SHOPPING_HOST", SERVER_CONFIG["host"])
