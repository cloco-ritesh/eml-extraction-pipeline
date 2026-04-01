"""
Output: saves per-model extraction JSONs and the validation report alongside the .eml file.
"""

import json
from pathlib import Path

from ingestion.schemas import ExtractionResult, ValidationReport


def save_results(
    eml_path: Path,
    results: list[ExtractionResult],
    report: ValidationReport,
) -> list[Path]:
    """Save all outputs alongside the .eml file. Returns list of created file paths."""
    stem = eml_path.stem
    output_dir = eml_path.parent
    created: list[Path] = []

    # Save per-model extraction results
    for result in results:
        filename = f"{stem}_{result.model_name}.json"
        filepath = output_dir / filename
        filepath.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        created.append(filepath)

    # Save validation report
    report_path = output_dir / f"{stem}_validation_report.json"
    report_path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    created.append(report_path)

    return created
