"""
Cross-validation: compares 4 extraction outputs (3 LLM + 1 OCR) field-by-field.
Produces a ValidationReport with agreement matrix, confidence scores, and cost ranking.
"""

from datetime import datetime, timezone
from difflib import SequenceMatcher

from ingestion.schemas import (
    CostSummary,
    ExtractedFields,
    ExtractionResult,
    FieldAgreement,
    ValidationReport,
)

# Fields to compare across extractors
COMPARABLE_FIELDS = [
    "sender_name",
    "sender_email",
    "subject",
    "date",
    "category",
    "summary",
]

# Confidence weights: LLM models count more than OCR.
# With 3 extractors (2 LLM + 1 OCR), weights sum to 1.0 on full agreement.
MODEL_WEIGHTS: dict[str, float] = {
    "gpt-4o":       0.40,
    "gpt-4.1-nano": 0.40,
    "ocr":          0.20,
}
DEFAULT_WEIGHT = 0.33  # fallback for unknown models

# Tie-break priority when agreement is equal (higher capability first)
MODEL_PRIORITY = ["gpt-4o", "gpt-4.1-nano", "ocr"]

FUZZY_THRESHOLD = 0.85


def _normalize(value: str | None) -> str:
    """Normalize a value for comparison: lowercase, strip whitespace."""
    if value is None:
        return ""
    return value.strip().lower()


def _values_match(a: str, b: str) -> bool:
    """Check if two normalized values match (exact or fuzzy)."""
    if a == b:
        return True
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_THRESHOLD


def _find_consensus_group(values: dict[str, str]) -> tuple[str | None, list[str]]:
    """Find the largest group of models that agree on a value.

    Returns (consensus_value, list_of_agreeing_model_names).
    """
    models = list(values.keys())
    best_group: list[str] = []
    best_value: str | None = None

    for i, model_a in enumerate(models):
        val_a = values[model_a]
        if not val_a:
            continue
        group = [model_a]
        for model_b in models[i + 1:]:
            if _values_match(val_a, values[model_b]):
                group.append(model_b)
        if len(group) > len(best_group):
            best_group = group
            best_value = val_a

    return best_value, best_group


def _compute_field_agreement(
    field_name: str,
    raw_values: dict[str, str | None],
) -> FieldAgreement:
    """Score a single field across all extractors."""
    normalized = {m: _normalize(v) for m, v in raw_values.items()}
    consensus_value, agreeing_models = _find_consensus_group(normalized)

    total_models = len(raw_values)
    agreement_count = len(agreeing_models)
    agreement_ratio = agreement_count / total_models if total_models > 0 else 0.0

    # Weighted confidence based on which models agree
    confidence = sum(MODEL_WEIGHTS.get(m, DEFAULT_WEIGHT) for m in agreeing_models)
    confidence = min(confidence, 1.0)

    # Recommended value: use the original (non-normalized) value from the highest-priority agreeing model
    recommended: str | None = None
    for model in MODEL_PRIORITY:
        if model in agreeing_models and raw_values.get(model):
            recommended = raw_values[model]
            break

    return FieldAgreement(
        field_name=field_name,
        values={m: v for m, v in raw_values.items()},
        agreement_count=agreement_count,
        agreement_ratio=round(agreement_ratio, 3),
        confidence=round(confidence, 3),
        recommended_value=recommended,
    )


def cross_validate(
    results: list[ExtractionResult],
    eml_filename: str,
) -> ValidationReport:
    """Compare all extraction results field-by-field and produce a validation report."""
    field_agreements: list[FieldAgreement] = []

    for field_name in COMPARABLE_FIELDS:
        raw_values: dict[str, str | None] = {}
        for result in results:
            value = getattr(result.extracted, field_name, None)
            raw_values[result.model_name] = str(value) if value is not None else None

        agreement = _compute_field_agreement(field_name, raw_values)
        field_agreements.append(agreement)

    # Overall confidence: mean of field confidences
    overall_confidence = 0.0
    if field_agreements:
        overall_confidence = sum(fa.confidence for fa in field_agreements) / len(field_agreements)

    # Track how often each model agrees with consensus
    model_agreement_counts: dict[str, int] = {r.model_name: 0 for r in results}
    for fa in field_agreements:
        consensus = fa.recommended_value
        if consensus is None:
            continue
        for model_name, value in fa.values.items():
            if value and _values_match(_normalize(value), _normalize(consensus)):
                model_agreement_counts[model_name] = model_agreement_counts.get(model_name, 0) + 1

    total_fields = len(COMPARABLE_FIELDS)

    # Cost summaries with agreement rates
    cost_summaries: list[CostSummary] = []
    for result in results:
        agreement_rate = model_agreement_counts.get(result.model_name, 0) / total_fields if total_fields > 0 else 0.0
        cost_summaries.append(
            CostSummary(
                model_name=result.model_name,
                estimated_cost_usd=result.estimated_cost_usd,
                latency_seconds=result.latency_seconds,
                agreement_rate=round(agreement_rate, 3),
            )
        )

    # Rank models by agreement rate (descending), then by cost (ascending) for ties
    ranked = sorted(
        cost_summaries,
        key=lambda cs: (-cs.agreement_rate, cs.estimated_cost_usd),
    )
    accuracy_ranking = [cs.model_name for cs in ranked]

    # Recommended extraction: highest agreement rate, lowest cost as tiebreaker
    recommended = accuracy_ranking[0] if accuracy_ranking else "gpt-4o"

    # Best output: from the recommended model
    best_output = ExtractedFields()
    for result in results:
        if result.model_name == recommended:
            best_output = result.extracted
            break

    return ValidationReport(
        eml_filename=eml_filename,
        timestamp=datetime.now(timezone.utc).isoformat(),
        field_agreements=field_agreements,
        overall_confidence=round(overall_confidence, 3),
        cost_summaries=cost_summaries,
        accuracy_ranking=accuracy_ranking,
        recommended_extraction=recommended,
        best_output=best_output,
    )
