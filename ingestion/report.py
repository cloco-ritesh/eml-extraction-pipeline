"""
Human-readable report generator: produces both HTML and Markdown validation reports.
"""

from pathlib import Path

from ingestion.schemas import ExtractionResult, ValidationReport


def _agreement_icon(ratio: float) -> str:
    """Return a visual indicator for agreement ratio."""
    if ratio >= 1.0:
        return "ALL AGREE"
    if ratio >= 0.75:
        return "MOSTLY"
    if ratio >= 0.5:
        return "SPLIT"
    return "DISAGREE"


def _bar(value: float, max_value: float, width: int = 20) -> str:
    """Render an ASCII bar for markdown."""
    if max_value == 0:
        return ""
    filled = int((value / max_value) * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def generate_markdown(
    results: list[ExtractionResult],
    report: ValidationReport,
) -> str:
    """Generate a Markdown validation report."""
    lines: list[str] = []
    w = lines.append

    w(f"# Validation Report")
    w("")
    w(f"**Email:** `{report.eml_filename}`")
    w(f"**Timestamp:** {report.timestamp}")
    w(f"**Overall Confidence:** {report.overall_confidence:.0%}")
    w(f"**Recommended Model:** `{report.recommended_extraction}`")
    w("")

    # --- Agreement Matrix ---
    w("## Field Agreement Matrix")
    w("")
    model_names = [r.model_name for r in results]
    header = "| Field | " + " | ".join(f"`{m}`" for m in model_names) + " | Agreement |"
    sep = "|" + "|".join(["---"] * (len(model_names) + 2)) + "|"
    w(header)
    w(sep)

    for fa in report.field_agreements:
        cells = []
        for m in model_names:
            val = fa.values.get(m)
            display = _truncate(val, 25) if val else "—"
            cells.append(display)
        icon = _agreement_icon(fa.agreement_ratio)
        ratio_str = f"{fa.agreement_count}/{len(model_names)} {icon}"
        w(f"| `{fa.field_name}` | " + " | ".join(cells) + f" | {ratio_str} |")

    w("")

    # --- Cost vs Accuracy ---
    w("## Cost vs Accuracy")
    w("")
    w("| Model | Cost (USD) | Latency | Agreement Rate | Value |")
    w("|---|---|---|---|---|")

    max_rate = max((cs.agreement_rate for cs in report.cost_summaries), default=1.0)
    for cs in report.cost_summaries:
        bar = _bar(cs.agreement_rate, max(max_rate, 0.01))
        w(f"| `{cs.model_name}` | ${cs.estimated_cost_usd:.4f} | {cs.latency_seconds:.1f}s | {cs.agreement_rate:.0%} | {bar} |")

    w("")

    # --- Ranking ---
    w("## Model Ranking")
    w("")
    for i, model in enumerate(report.accuracy_ranking, 1):
        medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"{i}.")
        cs = next((c for c in report.cost_summaries if c.model_name == model), None)
        cost_str = f"${cs.estimated_cost_usd:.4f}" if cs else "—"
        rate_str = f"{cs.agreement_rate:.0%}" if cs else "—"
        w(f"{medal} **{model}** — accuracy: {rate_str}, cost: {cost_str}")

    w("")

    # --- Recommended Output ---
    w("## Recommended Extraction")
    w("")
    w(f"Source: `{report.recommended_extraction}`")
    w("")
    w("```json")
    w(report.best_output.model_dump_json(indent=2))
    w("```")
    w("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafafa; }
h1 { border-bottom: 3px solid #2563eb; padding-bottom: 0.5rem; }
h2 { color: #2563eb; margin-top: 2rem; }
.meta { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem 1.5rem; margin: 1rem 0; }
.meta .winner { font-size: 1.3rem; color: #16a34a; font-weight: 700; }
table { width: 100%; border-collapse: collapse; margin: 1rem 0; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
th { background: #2563eb; color: #fff; padding: 0.6rem 0.8rem; text-align: left; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em; }
td { padding: 0.5rem 0.8rem; border-bottom: 1px solid #f0f0f0; font-size: 0.9rem; }
tr:last-child td { border-bottom: none; }
.agree-all { background: #dcfce7; color: #166534; font-weight: 600; }
.agree-most { background: #fef9c3; color: #854d0e; }
.agree-split { background: #fee2e2; color: #991b1b; }
.bar { display: inline-block; height: 14px; border-radius: 3px; }
.bar-fill { background: #2563eb; }
.bar-empty { background: #e5e7eb; }
pre { background: #1e293b; color: #e2e8f0; padding: 1rem; border-radius: 8px; overflow-x: auto; font-size: 0.85rem; }
.ranking { list-style: none; padding: 0; }
.ranking li { padding: 0.5rem 0; font-size: 1rem; }
"""


def _html_bar(value: float, max_value: float, width_px: int = 200) -> str:
    """Render an HTML bar."""
    if max_value == 0:
        return ""
    fill_px = int((value / max_value) * width_px)
    empty_px = width_px - fill_px
    return (
        f'<span class="bar"><span class="bar bar-fill" style="width:{fill_px}px"></span>'
        f'<span class="bar bar-empty" style="width:{empty_px}px"></span></span>'
    )


def _agreement_css_class(ratio: float) -> str:
    if ratio >= 1.0:
        return "agree-all"
    if ratio >= 0.75:
        return "agree-most"
    return "agree-split"


def generate_html(
    results: list[ExtractionResult],
    report: ValidationReport,
) -> str:
    """Generate an HTML validation report."""
    model_names = [r.model_name for r in results]

    # Agreement table rows
    agreement_rows = ""
    for fa in report.field_agreements:
        cells = ""
        for m in model_names:
            val = fa.values.get(m)
            display = _escape(_truncate(val, 30)) if val else "—"
            cells += f"<td>{display}</td>"
        icon = _agreement_icon(fa.agreement_ratio)
        css = _agreement_css_class(fa.agreement_ratio)
        cells += f'<td class="{css}">{fa.agreement_count}/{len(model_names)} {icon}</td>'
        agreement_rows += f"<tr><td><code>{fa.field_name}</code></td>{cells}</tr>\n"

    # Cost table rows
    max_rate = max((cs.agreement_rate for cs in report.cost_summaries), default=1.0)
    cost_rows = ""
    for cs in report.cost_summaries:
        bar = _html_bar(cs.agreement_rate, max(max_rate, 0.01))
        cost_rows += (
            f"<tr><td><code>{cs.model_name}</code></td>"
            f"<td>${cs.estimated_cost_usd:.4f}</td>"
            f"<td>{cs.latency_seconds:.1f}s</td>"
            f"<td>{cs.agreement_rate:.0%}</td>"
            f"<td>{bar}</td></tr>\n"
        )

    # Ranking list
    ranking_items = ""
    medals = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}
    for i, model in enumerate(report.accuracy_ranking):
        medal = medals.get(i, f"{i+1}.")
        cs = next((c for c in report.cost_summaries if c.model_name == model), None)
        cost_str = f"${cs.estimated_cost_usd:.4f}" if cs else "—"
        rate_str = f"{cs.agreement_rate:.0%}" if cs else "—"
        ranking_items += f"<li>{medal} <strong>{model}</strong> — accuracy: {rate_str}, cost: {cost_str}</li>\n"

    model_headers = "".join(f"<th>{m}</th>" for m in model_names)
    best_json = _escape(report.best_output.model_dump_json(indent=2))

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Validation Report — {_escape(report.eml_filename)}</title>
<style>{_CSS}</style></head>
<body>
<h1>Validation Report</h1>

<div class="meta">
  <div><strong>Email:</strong> <code>{_escape(report.eml_filename)}</code></div>
  <div><strong>Timestamp:</strong> {report.timestamp}</div>
  <div><strong>Overall Confidence:</strong> {report.overall_confidence:.0%}</div>
  <div class="winner">Recommended: {report.recommended_extraction}</div>
</div>

<h2>Field Agreement Matrix</h2>
<table>
<tr><th>Field</th>{model_headers}<th>Agreement</th></tr>
{agreement_rows}
</table>

<h2>Cost vs Accuracy</h2>
<table>
<tr><th>Model</th><th>Cost (USD)</th><th>Latency</th><th>Agreement</th><th>Visual</th></tr>
{cost_rows}
</table>

<h2>Model Ranking</h2>
<ol class="ranking">
{ranking_items}
</ol>

<h2>Recommended Extraction</h2>
<p>Source: <code>{report.recommended_extraction}</code></p>
<pre>{best_json}</pre>

</body></html>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_reports(
    eml_path: Path,
    results: list[ExtractionResult],
    report: ValidationReport,
) -> list[Path]:
    """Generate and save both HTML and Markdown reports. Returns created file paths."""
    stem = eml_path.stem
    output_dir = eml_path.parent
    created: list[Path] = []

    md_content = generate_markdown(results, report)
    md_path = output_dir / f"{stem}_report.md"
    md_path.write_text(md_content, encoding="utf-8")
    created.append(md_path)

    html_content = generate_html(results, report)
    html_path = output_dir / f"{stem}_report.html"
    html_path.write_text(html_content, encoding="utf-8")
    created.append(html_path)

    return created
