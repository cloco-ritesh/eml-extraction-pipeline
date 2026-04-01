# EML Processing Pipeline POC

Automated email data extraction pipeline using 3 OpenAI models + OCR, with cross-validation.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### System dependencies

For OCR HTML-to-image conversion:
```bash
brew install wkhtmltopdf    # macOS
```

### Environment

```bash
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

## Usage

### Process a single .eml file
```bash
python run.py process path/to/email.eml
```

### Watch a folder for new .eml files
```bash
python run.py watch
# Drop .eml files into watch_folder/ — they'll be processed automatically
```

## Output

For each processed `.eml` file, the pipeline creates:

| File | Contents |
|------|----------|
| `{name}_gpt-4o.json` | Extraction from gpt-4o |
| `{name}_gpt-4o-mini.json` | Extraction from gpt-4o-mini |
| `{name}_gpt-4.1.json` | Extraction from gpt-4.1 |
| `{name}_ocr.json` | Extraction from EasyOCR |
| `{name}_validation_report.json` | Cross-validation report |

## Architecture

See [requirements/architecture.md](requirements/architecture.md) for the full design doc.

```
.eml → parser → [gpt-4o, gpt-4o-mini, gpt-4.1, OCR] (parallel) → validator → output
```

## Tests

```bash
python -m pytest ingestion/tests/ -v
```
