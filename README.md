# Multi-Agent Research Copilot

Starter implementation of a centralized multi-agent workflow for research and report generation.

## What this includes

- FastAPI service with project run and retrieval endpoints.
- Five specialized agents:
  - Orchestrator
  - Researcher
  - Analyst
  - Writer
  - Reviewer
- Shared `ProjectState` schema for deterministic handoffs.
- Tool layer for web search, page fetch, and in-memory state storage.
- Reviewer loop with configurable retry limits and quality checks.
- Pytest coverage for workflow baseline and API health.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## API

- `GET /health`
- `POST /v1/projects/run`
- `GET /v1/projects/{request_id}`

### Run request example

```bash
curl -X POST http://127.0.0.1:8000/v1/projects/run \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Compare 3 coral restoration startups by cost, scalability, and technology",
    "companies": ["Archireef", "Coral Vita", "SECORE International"]
  }'
```

## Test

```bash
pytest
```

## Notes

- The researcher uses DuckDuckGo search when available and falls back to a stub provider.
- The initial implementation keeps orchestration centralized to reduce agent chatter and simplify debugging.
- State persistence is in-memory for MVP speed; swap `ProjectStore` for SQLite/Postgres when ready.
- If `OPENAI_API_KEY` is set, analyst/writer/reviewer use the OpenAI Responses API with fallback to local heuristics on errors.
- Company research runs concurrently, with worker count controlled by `MAX_CONCURRENT_RESEARCH`.

## Config

Main runtime settings are defined in `.env`:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`
- `OPENAI_TIMEOUT_SECONDS`
- `MAX_CONCURRENT_RESEARCH`
- `MAX_REVIEW_LOOPS`
- `MIN_SOURCES_PER_COMPANY`
- `DEFAULT_COMPANIES`
- `REQUEST_TIMEOUT_SECONDS`

When OpenAI is enabled, each run includes `metadata.llm_enabled: true`.
