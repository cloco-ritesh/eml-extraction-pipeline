"""
Async OpenAI extraction: sends parsed email content to an LLM model
and returns structured JSON. Single reusable function parameterized by model name.
"""

import asyncio
import json
import logging
import re
import time

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2

from ingestion.config import OPENAI_API_KEY, MODEL_PRICING, calculate_cost
from ingestion.parser import ParsedEmail
from ingestion.schemas import ExtractedFields, ExtractionResult, TokenUsage

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Lazy-initialize the async OpenAI client."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


SYSTEM_PROMPT = """You are a structured data extractor for emails.
Given an email's subject, headers, plain text body, and attachment contents,
extract all structured information into the JSON schema below.

RULES:
- Return ONLY valid JSON matching the schema exactly.
- Use null for fields you cannot determine.
- Dates must be ISO-8601 format: "YYYY-MM-DD".
- Monetary amounts must be numeric (no currency symbols): {"label": "...", "amount": 1500.00, "currency": "AUD"}.
- Entities must use types: "person", "organization", "location", "product", "reference_number".
- The "category" field must be one of: "invoice", "receipt", "notification", "report", "correspondence", "marketing", "legal", "other".
- For line_items, each item should have: {"description": "...", "quantity": number_or_null, "unit_price": number_or_null, "total": number_or_null}.
- Extract ALL monetary values, dates, and entities you can find.

SCHEMA:
{
  "sender_name": "string or null",
  "sender_email": "string or null",
  "subject": "string or null",
  "date": "YYYY-MM-DD or null",
  "category": "string from allowed list",
  "entities": [{"name": "string", "type": "string", "value": "string or null"}],
  "key_dates": [{"label": "string", "date": "YYYY-MM-DD"}],
  "monetary_amounts": [{"label": "string", "amount": number, "currency": "string"}],
  "line_items": [{"description": "string", "quantity": number_or_null, "unit_price": number_or_null, "total": number_or_null}],
  "action_items": ["string"],
  "summary": "One-sentence summary of the email"
}"""


def _build_user_message(parsed: ParsedEmail) -> str:
    """Build the user message from parsed email content."""
    parts = [
        f"Subject: {parsed.subject}",
        f"From: {parsed.sender}",
        f"Date: {parsed.date}",
        "---",
        "Body:",
        parsed.plain_text[:8000] if parsed.plain_text else "(no plain text body)",
    ]

    if parsed.attachments:
        parts.append("---")
        parts.append(f"Attachments ({len(parsed.attachments)}):")
        for att in parsed.attachments:
            parts.append(f"  - {att.filename} ({att.content_type})")
            if att.content_type.startswith("text/"):
                try:
                    text = att.data.decode("utf-8", errors="replace")[:2000]
                    parts.append(f"    Content: {text}")
                except Exception:
                    parts.append("    (could not decode)")

    return "\n".join(parts)


# Reasoning models (o-series) don't support system messages, temperature, or
# response_format the same way as GPT models.
REASONING_MODELS = {"o3-mini", "o4-mini", "o3", "o4"}


async def extract_with_model(
    parsed_email: ParsedEmail,
    model_name: str,
) -> ExtractionResult:
    """Run extraction on a single OpenAI model. Returns structured result with cost tracking."""
    client = _get_client()
    user_message = _build_user_message(parsed_email)
    is_reasoning = model_name in REASONING_MODELS

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await _call_model(client, model_name, is_reasoning, user_message, parsed_email)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                logger.warning("%s attempt %d failed: %s — retrying in %ds", model_name, attempt, exc, RETRY_DELAY_SECONDS)
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
            else:
                logger.error("%s failed after %d attempts: %s", model_name, MAX_RETRIES, exc)

    # All retries exhausted — return empty result
    return ExtractionResult(
        model_name=model_name,
        extracted=ExtractedFields(),
        raw_response=f"ERROR: {last_error}",
    )


async def _call_model(
    client: AsyncOpenAI,
    model_name: str,
    is_reasoning: bool,
    user_message: str,
    parsed_email: ParsedEmail,
) -> ExtractionResult:
    """Single API call to a model. Raises on failure."""
    start = time.monotonic()

    if is_reasoning:
        # Reasoning models: no system message, no temperature, no response_format.
        # Combine system + user into a single user message with JSON instruction.
        combined = (
            f"{SYSTEM_PROMPT}\n\n---\n\n"
            f"{user_message}\n\n"
            "IMPORTANT: Respond with ONLY valid JSON matching the schema above. No markdown, no explanation."
        )
        response = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": combined}],
        )
    else:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
    latency = time.monotonic() - start

    raw_content = response.choices[0].message.content or "{}"
    usage = response.usage

    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    total_tokens = (usage.total_tokens if usage else 0)

    # Reasoning models may wrap JSON in ```json ... ``` blocks — strip it
    cleaned_content = raw_content.strip()
    if cleaned_content.startswith("```"):
        cleaned_content = re.sub(r"^```(?:json)?\s*\n?", "", cleaned_content)
        cleaned_content = re.sub(r"\n?```\s*$", "", cleaned_content)

    try:
        parsed_json = json.loads(cleaned_content)
        extracted = ExtractedFields.model_validate(parsed_json)
    except (json.JSONDecodeError, Exception):
        extracted = ExtractedFields()

    return ExtractionResult(
        model_name=model_name,
        extracted=extracted,
        token_usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
        estimated_cost_usd=calculate_cost(model_name, prompt_tokens, completion_tokens),
        latency_seconds=round(latency, 3),
        raw_response=raw_content,
    )


async def extract_all_models(
    parsed_email: ParsedEmail,
    models: list[str],
) -> list[ExtractionResult]:
    """Run extraction in parallel across all specified models."""
    tasks = [extract_with_model(parsed_email, m) for m in models]
    return list(await asyncio.gather(*tasks))
