# AI Agent Desktop

A local-first desktop application for running a multi-agent research workflow on your own machine.

It combines an Electron desktop shell, a FastAPI backend, SQLite persistence, and a coordinated team of AI agents that can research, analyze, write, and review reports from a single user prompt.

## Overview

`AI Agent Desktop` is designed to feel like a local research copilot:

- create threads
- send prompts in a chat-style interface
- watch agent progress live in the timeline
- inspect logs for each run
- keep all thread history and settings stored locally

The only external dependency is your model provider. In the current version, that means an OpenAI API key if you want live LLM-backed behavior.

## What The Product Does

When you send a prompt, the app runs a centralized multi-agent workflow:

1. `Orchestrator` plans the work.
2. `Research workers` gather evidence in parallel.
3. `Analyst` turns notes into structured comparisons.
4. `Writer` drafts the report.
5. `Reviewer` checks quality and requests revisions when needed.
6. The final output is saved back into the thread.

The app also carries thread memory into later runs using:

- a rolling thread summary
- recent turns from the conversation

That makes follow-up prompts feel more like an ongoing conversation instead of isolated one-off requests.

## Features

- Local desktop app powered by Electron
- FastAPI backend embedded behind the desktop shell
- Persistent local storage with SQLite
- Thread list and chat-style workspace
- Settings modal for OpenAI API key and model selection
- Logs modal for debugging agent activity
- Live run progress in the thread timeline
- In-thread error rendering for failed runs
- Parallel research workers
- Thread memory via summary plus recent messages
- Packaged macOS desktop build support

## Architecture

```text
Electron App
  -> FastAPI backend
    -> Local SQLite store
    -> Multi-agent workflow runner
      -> Orchestrator
      -> Parallel Research Workers
      -> Analyst
      -> Writer
      -> Reviewer
```

## Tech Stack

- `Electron` for the desktop shell
- `FastAPI` for the local backend
- `SQLite` for threads, messages, runs, logs, and settings
- `OpenAI Responses API` for LLM-backed agents
- `PyInstaller` for bundling the backend into a standalone executable
- `electron-builder` for macOS app packaging

## Repository Structure

```text
app/
  agents/        Agent roles and behavior
  api/           FastAPI routes
  local/         SQLite-backed local persistence
  schemas/       Shared workflow and API models
  tools/         OpenAI, search, scraping, thread-memory helpers
  workflows/     Centralized multi-agent runner
static/          Frontend JS and CSS
templates/       HTML templates for the local UI
electron/        Electron main/preload processes
tests/           API and workflow tests
```

## Requirements

### For development

You will need:

- `Python 3.11+` or newer
- `Node.js 18+` or newer
- `npm`
- an `OpenAI API key` for live LLM behavior

### For packaged desktop use

End users do not need Python installed if they use the packaged macOS app.
They still need their own OpenAI API key to enable live model-backed runs.

## Getting Started

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd AI-Agent
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install desktop dependencies

```bash
npm install
```

### 5. Create your env file

```bash
cp .env.example .env
```

You can leave `OPENAI_API_KEY` empty if you want to run the local fallback behavior first.

## Running The App

### Option A: Run the desktop app in development

This is the main local development workflow.

```bash
npm run dev
```

That command:

- starts the FastAPI backend on `http://127.0.0.1:8000`
- waits for the backend health check
- launches the Electron desktop window

### Option B: Run only the backend and browser UI

Useful if you want to test the local web interface directly.

```bash
source .venv/bin/activate
python3 -m app.server --reload --host 127.0.0.1 --port 8000
```

Then open:

- [http://127.0.0.1:8000](http://127.0.0.1:8000)
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

## Using The App

1. Launch the app.
2. Open `Settings`.
3. Paste your OpenAI API key.
4. Choose a model such as `gpt-4.1-mini`.
5. Create a new thread.
6. Send a prompt like:

```text
Compare 3 coral restoration startups by cost, scalability, and technology.
```

During the run, you should see:

- your user message appear immediately
- live progress events in the thread timeline
- a saved assistant response when the run completes
- logs available in the `Logs` modal

## Environment Configuration

Main runtime values come from `.env`.

```env
APP_DATA_DIR=.app_data
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=45
MAX_CONCURRENT_RESEARCH=4
MAX_REVIEW_LOOPS=2
MIN_SOURCES_PER_COMPANY=2
DEFAULT_COMPANIES=Archireef,Coral Vita,SECORE International
REQUEST_TIMEOUT_SECONDS=15
```

### Important settings

- `OPENAI_API_KEY`: required for live OpenAI-backed runs
- `OPENAI_MODEL`: model used by the LLM-backed agents
- `MAX_CONCURRENT_RESEARCH`: number of parallel research workers
- `APP_DATA_DIR`: location of local SQLite data and app state

## Desktop Build

To build the packaged backend and macOS desktop app:

```bash
npm run build:desktop
```

This produces:

- a bundled backend binary in `dist/ai-agent-backend`
- packaged desktop artifacts in `dist/`
- a macOS `.dmg` installer

## API Endpoints

The app UI uses these local routes:

- `GET /`
- `GET /health`
- `GET /api/meta`
- `GET /api/threads`
- `POST /api/threads`
- `GET /api/threads/{thread_id}`
- `POST /api/threads/{thread_id}/messages`
- `GET /api/threads/{thread_id}/runs/{run_id}`
- `GET /api/settings`
- `POST /api/settings`
- `GET /api/logs`

There is also a direct workflow API:

- `POST /v1/projects/run`
- `GET /v1/projects/{request_id}`

## Tests

Run the full test suite with:

```bash
pytest -q
```

## Current Behavior Notes

- Research workers run concurrently.
- Thread context is summarized and reused across later prompts.
- Analyst, writer, and reviewer are LLM-backed when OpenAI is configured.
- If the OpenAI API is unavailable or quota is exhausted, the app falls back in parts of the workflow where fallback logic exists.
- Local app state is stored in SQLite.
- Desktop packaging currently targets macOS first.

## Roadmap

Some strong next steps for the project:

- streaming token-by-token assistant responses
- richer trace visualization per agent
- stronger source validation and citation mapping
- code signing and notarization for macOS builds
- optional cross-platform packaging

## Why This Project Exists

This project is meant to demonstrate a practical multi-agent application that is:

- locally runnable
- easy to inspect and debug
- portfolio-friendly
- extensible into a more production-oriented agent platform

## License

Add the license you want for your GitHub repository here.
