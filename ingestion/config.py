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
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o3-mini",
    "o4-mini",
]

# Pricing per 1K tokens (USD) — April 2026
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":       {"input": 0.0025,  "output": 0.01},
    "gpt-4.1":      {"input": 0.002,   "output": 0.008},
    "gpt-4.1-mini": {"input": 0.0004,  "output": 0.0016},
    "gpt-4.1-nano": {"input": 0.0001,  "output": 0.0004},
    "o3-mini":      {"input": 0.0011,  "output": 0.0044},
    "o4-mini":      {"input": 0.0011,  "output": 0.0044},
}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated USD cost for a model call."""
    pricing = MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (prompt_tokens / 1000) * pricing["input"]
    output_cost = (completion_tokens / 1000) * pricing["output"]
    return round(input_cost + output_cost, 6)
