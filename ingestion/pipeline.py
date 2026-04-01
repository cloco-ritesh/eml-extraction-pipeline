"""
Orchestrator: ties parse -> extract (LLM + OCR in parallel) -> validate -> save.
"""

import asyncio
import logging
from pathlib import Path

from ingestion.config import EXTRACTION_MODELS
from ingestion.llm_extractor import extract_all_models
from ingestion.ocr_extractor import extract_with_ocr
from ingestion.output import save_results
from ingestion.parser import parse_eml
from ingestion.report import save_reports
from ingestion.validator import cross_validate

logger = logging.getLogger(__name__)


async def process_eml(eml_path: Path) -> None:
    """Full pipeline: parse -> extract (LLM + OCR parallel) -> validate -> save."""
    logger.info("Processing: %s", eml_path.name)

    # Step 1: Parse
    parsed = parse_eml(eml_path)
    logger.info("Parsed email: subject=%r, sender=%r", parsed.subject, parsed.sender)

    # Step 2: Extract in parallel — LLM models (async) + OCR (in executor)
    loop = asyncio.get_running_loop()
    llm_task = extract_all_models(parsed, EXTRACTION_MODELS)
    ocr_task = loop.run_in_executor(None, extract_with_ocr, parsed)

    llm_results, ocr_result = await asyncio.gather(llm_task, ocr_task)
    all_results = list(llm_results) + [ocr_result]

    for r in all_results:
        logger.info(
            "  %s: cost=$%.4f, latency=%.1fs, tokens=%d",
            r.model_name,
            r.estimated_cost_usd,
            r.latency_seconds,
            r.token_usage.total_tokens,
        )

    # Step 3: Cross-validate
    report = cross_validate(all_results, eml_path.name)
    logger.info(
        "Validation: confidence=%.2f, recommended=%s",
        report.overall_confidence,
        report.recommended_extraction,
    )

    # Step 4: Save outputs (JSON + human-readable reports)
    created_files = save_results(eml_path, all_results, report)
    report_files = save_reports(eml_path, all_results, report)
    all_files = created_files + report_files

    logger.info("Saved %d files:", len(all_files))
    for f in all_files:
        logger.info("  -> %s", f.name)
