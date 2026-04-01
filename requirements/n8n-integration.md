# n8n Dockerized Workflow — Implementation Plan

## Context

Add a dockerized n8n instance to the project that orchestrates the existing EML extraction pipeline visually. Start with a simple flow that wraps the Python CLI, then iterate toward native n8n nodes.

---

## Folder Structure

```
ingestion_poc/
    n8n/                            # NEW — all n8n stuff lives here
        docker-compose.yml          # n8n + volumes
        Dockerfile                  # Extends n8n image with Python + pipeline deps
        workflows/                  # Exported workflow JSONs (version-controlled)
            eml_processing.json
        .env.example                # N8N_BASIC_AUTH, OPENAI_API_KEY
    ingestion/                      # EXISTING — unchanged
    run.py                          # EXISTING — unchanged
    requirements.txt                # EXISTING — unchanged
    watch_folder/                   # EXISTING — shared with n8n container
```

---

## Docker Setup

### `n8n/Dockerfile`
Extends `docker.n8n.io/n8nio/n8n` with Python 3 and the pipeline's lightweight deps (openai, pydantic, python-dotenv). **Excludes EasyOCR** (2GB PyTorch) — OCR degrades gracefully.

### `n8n/docker-compose.yml`
- **n8n** service on port 5678
- Bind-mounts:
  - `../watch_folder` → `/watch_folder` (trigger source + output destination)
  - `../ingestion` + `../run.py` + `../requirements.txt` → `/pipeline/` (read-only)
  - `./n8n-data` → `/home/node/.n8n` (persistent n8n data)
- Env vars: `OPENAI_API_KEY`, `GENERIC_TIMEZONE`, `N8N_RUNNERS_ENABLED`

---

## Phase 1 — Simple Flow (build first)

n8n wraps the existing Python pipeline. Zero Python code changes.

### Workflow: 6 nodes

```
[Local File Trigger]  →  [Execute Command]  →  [IF: success?]
   /watch_folder/*.eml     python3 run.py        ├─ Yes → [Read File: report.json] → [Set: summary]
                           process <file>         └─ No  → [Log error]
```

**Node details:**

1. **Local File Trigger** — watches `/watch_folder/` for new `.eml` files, 2s stability threshold
2. **Execute Command** — `cd /pipeline && python3 run.py process "{{ $json.path }}"`, 120s timeout
3. **IF** — check exit code === 0
4. **Read File** — read `{stem}_validation_report.json` from `/watch_folder/`
5. **Set** — extract `recommended_extraction`, `overall_confidence`, `cost_summaries` for display
6. **NoOp/Log** — error path placeholder

---

## Phase 2 — Native n8n Nodes (iterate later)

Replace Execute Command with native nodes for per-step visibility:

```
[Local File Trigger]
    → [Code: Parse EML (Python)]
    → [Split: 6 branches]
        → [OpenAI/HTTP Request × 6 models in parallel]
    → [Merge results]
    → [Code: Cross-validate (Python)]
    → [Code: Generate reports (Python)]
    → [Write File: save outputs]
```

- Use **HTTP Request** nodes (not OpenAI node) for full control over `response_format` and reasoning model handling
- Port `parser.py` logic to a **Python Code** node (stdlib only, no deps)
- Port `validator.py` + `report.py` to **Python Code** nodes
- Add EasyOCR as a separate Docker service if needed

---

## OpenAI API Key Handling

- **Phase 1**: Passed as env var in docker-compose → Python reads `os.environ["OPENAI_API_KEY"]` (already works)
- **Phase 2**: Migrates to n8n's encrypted credential store when using native OpenAI/HTTP Request nodes

---

## Verification

1. `cd n8n && docker compose up` — n8n UI at http://localhost:5678
2. Drop an `.eml` into `watch_folder/` — workflow triggers automatically
3. Check execution history in n8n UI — should show success with report data
4. Verify output files created in `watch_folder/`
