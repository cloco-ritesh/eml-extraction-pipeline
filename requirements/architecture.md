# EML Processing Pipeline — Implementation Plan

## Context

Build an automated EML processing pipeline that monitors a folder for `.eml` files, extracts structured data using 3 OpenAI models in parallel + OCR independently, cross-validates all 4 outputs, and saves results alongside the original file. The `ingestion_poc` project is currently empty scaffolding.

---

## Tech Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Parallelism | `asyncio` + `AsyncOpenAI` | Native async support in OpenAI SDK; `asyncio.gather()` for concurrent I/O; OCR runs in `run_in_executor` since it's CPU-bound |
| OCR | `easyocr` | Pure pip install (no system binary like Tesseract); better accuracy on HTML-rendered text; built-in confidence scores |
| HTML-to-image | `imgkit` + `wkhtmltopdf` | Lightweight; fallback to plain-text OCR if binary missing |
| Schema validation | `pydantic` v2 | Enforces extraction contract; JSON serialization built-in |
| File watcher | `watchdog` | Standard choice; dispatch to async pipeline via `run_coroutine_threadsafe` |

---

## Folder Structure

```
ingestion_poc/
    .env.example                # OPENAI_API_KEY=sk-...
    .gitignore
    requirements.txt
    README.md
    run.py                      # CLI: `python run.py watch` or `python run.py process <file.eml>`
    ingestion/
        __init__.py
        config.py               # Load .env, model pricing, shared constants
        schemas.py              # Pydantic models: ExtractedFields, ExtractionResult, ValidationReport
        parser.py               # EML parsing -> ParsedEmail dataclass
        llm_extractor.py        # Async OpenAI extraction (single reusable function, parameterized by model)
        ocr_extractor.py        # HTML->image + EasyOCR extraction
        validator.py            # Cross-validation: field agreement, confidence, cost/accuracy ranking
        pipeline.py             # Orchestrator: parse -> extract (LLM+OCR parallel) -> validate -> save
        output.py               # Save per-model JSONs + validation report alongside .eml
        watcher.py              # watchdog folder monitor
        tests/
            __init__.py
            fixtures/
                .gitkeep
            test_parser.py
            test_llm_extractor.py
            test_ocr_extractor.py
            test_validator.py
            test_pipeline.py
    watch_folder/
        .gitkeep
```

---

## Module Breakdown

### 1. `config.py` — Configuration
- Load `OPENAI_API_KEY` from `.env`
- Define `EXTRACTION_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1"]`
- Model pricing dict for cost tracking
- `WATCH_FOLDER` path from env

### 2. `schemas.py` — Data Contracts
- `ExtractedFields(BaseModel)` — flat, chart-ready schema: sender, date, category, entities, monetary_amounts, key_dates, action_items, summary
- `ExtractionResult(BaseModel)` — wraps ExtractedFields + model_name, token_usage, estimated_cost_usd, latency_seconds
- `FieldAgreement(BaseModel)` — per-field comparison: values by model, agreement_count, agreement_ratio, confidence, recommended_value
- `ValidationReport(BaseModel)` — field_agreements list, overall_confidence, cost_summary, accuracy_ranking, recommended_extraction, best_output

### 3. `parser.py` — EML Parsing
- `ParsedEmail` dataclass: subject, sender, recipients, date, plain_text, html_body, attachments list, raw_headers
- `parse_eml(path) -> ParsedEmail` — uses Python `email` stdlib
- Reuses patterns from reference project's `parsers/common.py` (get_plain_text, get_html from MIME walk)

### 4. `llm_extractor.py` — OpenAI Extraction
- Single `async extract_with_model(parsed_email, model_name) -> ExtractionResult` function
- Uses `AsyncOpenAI` with `response_format={"type": "json_object"}`
- System prompt enforces the ExtractedFields schema with strict rules (ISO dates, numeric amounts, enum categories)
- Tracks prompt_tokens, completion_tokens, calculates cost from MODEL_PRICING
- `async extract_all_models(parsed_email, models) -> list[ExtractionResult]` — runs `asyncio.gather()`

### 5. `ocr_extractor.py` — OCR Extraction
- `html_to_image(html) -> bytes` — renders HTML body to PNG via imgkit
- `extract_with_ocr(parsed_email) -> ExtractionResult` — OCR images, parse text into ExtractedFields
- Lazy-initialized `easyocr.Reader` singleton
- Returns `model_name="ocr"`, `estimated_cost_usd=0.0`

### 6. `validator.py` — Cross-Validation
- `cross_validate(results, eml_filename) -> ValidationReport`
- For each field: collect values from 4 extractors, normalize, fuzzy-match (difflib), compute agreement_count/ratio
- Confidence weighting: LLM models 0.3 each, OCR 0.1
- Rank models by consensus agreement rate; recommend best by agreement_rate/cost ratio
- Output designed for direct chart consumption (bar charts for cost, heatmap for field agreement)

### 7. `pipeline.py` — Orchestrator
- `async process_eml(path)` — parse -> gather(LLM extractions, OCR in executor) -> validate -> save
- Logs progress to stdout

### 8. `output.py` — File Output
- Saves alongside `.eml`: `{stem}_{model}.json` per extractor + `{stem}_validation_report.json`

### 9. `watcher.py` — Folder Monitor
- watchdog `FileSystemEventHandler` on `.eml` creation
- Dispatches to `process_eml` via `asyncio.run_coroutine_threadsafe`
- 0.5s debounce to handle partial file writes

### 10. `run.py` — CLI Entry Point
- `python run.py watch` — start folder watcher
- `python run.py process <path.eml>` — process single file

---

## Data Flow

```
.eml file dropped into watch_folder/
        |
        v
  [watcher.py] detects FileCreatedEvent
        |
        v
  [pipeline.py] process_eml()
        |
        v
  [parser.py] parse_eml() --> ParsedEmail
        |
        +---> [llm_extractor.py] extract_all_models()
        |         |
        |         +---> asyncio.gather(
        |                   extract_with_model(parsed, "gpt-4o"),
        |                   extract_with_model(parsed, "gpt-4o-mini"),
        |                   extract_with_model(parsed, "gpt-4.1"),
        |               )
        |         |
        |         +--> 3x ExtractionResult
        |
        +---> [ocr_extractor.py] extract_with_ocr()  (via run_in_executor)
        |         |
        |         +--> 1x ExtractionResult
        |
        v
  [validator.py] cross_validate(4 results)
        |
        +--> ValidationReport (field agreements, confidence, cost, ranking)
        |
        v
  [output.py] save_results()
        |
        +--> 4 per-model JSON files
        +--> 1 validation_report.json
```

---

## requirements.txt

```
watchdog>=4.0.0
openai>=1.30.0
python-dotenv>=1.0.0
easyocr>=1.7.0
pydantic>=2.5.0
Pillow>=10.0.0
imgkit>=1.2.3
pytest>=7.4.0
```

System dependency: `wkhtmltopdf` for HTML-to-image (`brew install wkhtmltopdf` on macOS).

---

## Implementation Order

**Phase 1 — Foundation** (implement first)
1. `config.py`, `schemas.py`, `parser.py`
2. `test_parser.py` with fixture `.eml` files

**Phase 2 — Extractors** (implement Step 1 + Step 2)
3. `llm_extractor.py` + `test_llm_extractor.py`
4. `ocr_extractor.py` + `test_ocr_extractor.py`

**Phase 3 — Validation + Output** (implement Step 3)
5. `validator.py` + `test_validator.py`
6. `output.py`

**Phase 4 — Integration**
7. `pipeline.py` + `test_pipeline.py`
8. `watcher.py`, `run.py`

**Phase 5 — Polish**
9. `.env.example`, `.gitignore`, `README.md`

---

## Verification

1. **Unit tests**: `python -m pytest ingestion/tests/ -v`
2. **Single file**: `python run.py process watch_folder/sample.eml` — check 5 JSON files created alongside
3. **Watcher**: `python run.py watch` -> drop `.eml` into `watch_folder/` -> verify auto-processing
4. **Validation report**: Open `_validation_report.json`, verify field agreement matrix and cost summary are present and chart-ready
