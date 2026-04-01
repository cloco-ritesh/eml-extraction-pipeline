"""
Pydantic models defining the extraction contract, results, and validation report.
All schemas are chart/graph-ready: flat structures, numeric amounts, ISO dates.
"""

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """A named entity extracted from the email."""
    name: str
    type: str  # "person", "organization", "location", "product", "reference_number"
    value: str | None = None


class KeyDate(BaseModel):
    """A date extracted from the email with a descriptive label."""
    label: str
    date: str  # ISO-8601: YYYY-MM-DD


class MonetaryAmount(BaseModel):
    """A monetary value extracted from the email."""
    label: str
    amount: float
    currency: str = "AUD"


class ExtractedFields(BaseModel):
    """Structured data extracted from an email. Chart-ready flat schema."""
    sender_name: str | None = None
    sender_email: str | None = None
    subject: str | None = None
    date: str | None = None  # ISO-8601
    category: str | None = None  # invoice, receipt, notification, report, correspondence, marketing, legal, other
    entities: list[Entity] = Field(default_factory=list)
    key_dates: list[KeyDate] = Field(default_factory=list)
    monetary_amounts: list[MonetaryAmount] = Field(default_factory=list)
    line_items: list[dict[str, str | float | None]] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    summary: str | None = None


class TokenUsage(BaseModel):
    """Token usage from an LLM call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ExtractionResult(BaseModel):
    """Result from a single extractor (LLM model or OCR)."""
    model_name: str
    extracted: ExtractedFields
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    estimated_cost_usd: float = 0.0
    latency_seconds: float = 0.0
    raw_response: str | None = None


class FieldAgreement(BaseModel):
    """Per-field comparison across all extractors."""
    field_name: str
    values: dict[str, str | None]  # {model_name: extracted_value_as_string}
    agreement_count: int
    agreement_ratio: float  # 0.0 - 1.0
    confidence: float
    recommended_value: str | None = None


class CostSummary(BaseModel):
    """Cost and performance summary for a single model."""
    model_name: str
    estimated_cost_usd: float
    latency_seconds: float
    agreement_rate: float  # how often this model agreed with consensus


class ValidationReport(BaseModel):
    """Cross-validation report comparing all 4 extraction outputs."""
    eml_filename: str
    timestamp: str  # ISO-8601
    field_agreements: list[FieldAgreement]
    overall_confidence: float
    cost_summaries: list[CostSummary]
    accuracy_ranking: list[str]  # model names ranked by agreement with consensus
    recommended_extraction: str  # best model name
    best_output: ExtractedFields
