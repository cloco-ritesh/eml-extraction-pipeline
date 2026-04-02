"""
Configuration: environment loading, model definitions, and pricing constants.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
WATCH_FOLDER: str = os.environ.get("WATCH_FOLDER", "watch_folder")
PROJECT_ROOT: Path = Path(__file__).parent.parent

EXTRACTION_MODELS: list[str] = [
    "gpt-4o",
    "gpt-4.1-nano",
]

# Pricing per 1K tokens (USD) — April 2026
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":       {"input": 0.0025,  "output": 0.01},
    "gpt-4.1-nano": {"input": 0.0001,  "output": 0.0004},
}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated USD cost for a model call."""
    pricing = MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (prompt_tokens / 1000) * pricing["input"]
    output_cost = (completion_tokens / 1000) * pricing["output"]
    return round(input_cost + output_cost, 6)
