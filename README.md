# AI Agent Desktop

A local-first desktop application for running a multi-agent coding workflow on your own machine.

It combines an Electron desktop shell, a FastAPI backend, SQLite persistence, and a coordinated team of AI agents that inspect a repository, plan implementation work, propose code changes, review those proposals, and prepare validation commands from a single prompt.

## Overview

`AI Agent Desktop` is designed to feel like a local coding copilot:

- create threads
- delete threads safely with confirmation
- send implementation prompts in a chat-style interface
- watch grouped agent progress live in the timeline
- inspect logs for each run
- keep all thread history and settings stored locally

The only external dependency is your model provider. In the current version, that means an OpenAI API key if you want live LLM-backed behavior.

## What The Product Does

When you send a prompt, the app runs a centralized multi-agent coding workflow:

1. `Orchestrator` plans the coding run.
2. `Repo Explorer` scans the local repository for relevant files and symbols.
3. `Architect` turns repo findings into disjoint work items.
4. `Code Workers` run in parallel and propose file-level changes.
5. `Reviewer` checks for conflicts, missing coverage, and risky assumptions.
6. `Validator` prepares suggested verification commands.
7. `Finalizer` returns a structured coding response back into the thread.

The app also carries thread memory into later runs using:

- a rolling thread summary
- recent turns from the conversation

That makes follow-up prompts feel more like ongoing implementation work instead of isolated one-off requests.

## Features

- Local desktop app powered by Electron
- FastAPI backend embedded behind the desktop shell
- Persistent local storage with SQLite
- Thread list and chat-style workspace
- Short thread titles derived from the first prompt
- Delete-thread flow with confirmation
- Settings modal for OpenAI API key and model selection
- Logs modal for debugging agent activity
- Live run activity card in the thread timeline
- In-thread error rendering for failed runs
- Repository-aware exploration of local files
- Parallel code workers with disjoint write scopes
- Thread memory via summary plus recent messages
- Packaged macOS desktop build support

## Architecture

```text
Electron App
  -> FastAPI backend
    -> Local SQLite store
    -> Multi-agent coding workflow runner
      -> Orchestrator
      -> Repo Explorer
      -> Architect
      -> Parallel Code Workers
      -> Reviewer
      -> Validator
      -> Finalizer
```

## Tech Stack

- `Electron` for the desktop shell
- `FastAPI` for the local backend
- `SQLite` for threads, messages, runs, logs, and settings
- `OpenAI Responses API` for LLM-backed agents
- `ripgrep` for repository search
- `PyInstaller` for bundling the backend into a standalone executable
- `electron-builder` for macOS app packaging

## Repository Structure

```text
app/
  agents/        Agent roles and coding workflow behavior
  api/           FastAPI routes
  local/         SQLite-backed local persistence
  schemas/       Shared workflow and API models
  tools/         OpenAI, repo search, thread-memory helpers
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
Add JWT auth to the FastAPI app, propose the file changes, review for bugs, and include tests to run.
```

During the run, you should see:

- your user message appear immediately
- a single activity card showing the agent team and each stage state
- a saved assistant response when the run completes
- logs available in the `Logs` modal

Thread behavior:

- new thread titles are shortened automatically based on the first prompt
- the sidebar thread list and the chat timeline scroll independently
- `Delete Thread` removes the selected thread from the database and UI after confirmation
- active threads cannot be deleted while a run is still in progress

## Environment Configuration

Main runtime values come from `.env`.

```env
APP_NAME=Multi-Agent Coding Copilot
APP_DATA_DIR=.app_data
WORKSPACE_DIR=.
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=45
MAX_CONCURRENT_RESEARCH=4
MAX_REVIEW_LOOPS=2
REQUEST_TIMEOUT_SECONDS=15
```

### Important settings

- `OPENAI_API_KEY`: required for live OpenAI-backed runs
- `OPENAI_MODEL`: model used by the LLM-backed agents
- `WORKSPACE_DIR`: local repository or workspace to inspect
- `MAX_CONCURRENT_RESEARCH`: current concurrency knob for parallel work items
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

## Click To Launch

After building the desktop app, you can launch it like a normal macOS application:

- open `dist/mac-arm64/AI Agent Desktop.app`
- or install from the generated DMG in `dist/AI Agent Desktop-0.2.0-arm64.dmg`

When launched this way:

- Electron starts the local backend automatically
- the app waits for the backend health check
- the desktop window opens only after the backend is ready

When you close the app window:

- the Electron app quits
- the local backend is terminated cleanly
- the app does not keep running in the background

If you want to launch the Electron shell directly during development without `npm run dev`, you can also use:

```bash
npm start
```

## API Endpoints

The app UI uses these local routes:

- `GET /`
- `GET /health`
- `GET /api/meta`
- `GET /api/threads`
- `POST /api/threads`
- `DELETE /api/threads/{thread_id}`
- `GET /api/threads/{thread_id}`
- `POST /api/threads/{thread_id}/messages`
- `GET /api/threads/{thread_id}/runs/{run_id}`
- `GET /api/settings`
- `POST /api/settings`
- `GET /api/logs`

## Tests

Run the full test suite with:

```bash
pytest -q
```

## Current Behavior Notes

- Repo exploration is local and repository-aware.
- Thread context is summarized and reused across later prompts.
- Architect, code workers, and reviewer are LLM-backed when OpenAI is configured.
- Code workers run in parallel when they have disjoint scopes.
- The chat timeline shows grouped per-run agent activity rather than raw log spam.
- Thread deletion is persisted in SQLite and blocked while a run is active.
- The workflow proposes code changes and validation commands; it does not automatically apply patches to your repo yet.
- Local app state is stored in SQLite.
- Desktop packaging currently targets macOS first.

## Roadmap

Some strong next steps for the project:

- actual patch application with guarded approval
- terminal-backed test execution from the desktop workflow
- richer trace visualization per agent
- stronger file ownership and merge-conflict prevention
- code signing and notarization for macOS builds
- optional cross-platform packaging

## Why This Project Exists

This project is meant to demonstrate a practical multi-agent coding application that is:

- locally runnable
- easy to inspect and debug
- portfolio-friendly
- extensible into a more production-oriented agent platform

## License

Add the license you want for your GitHub repository here.
