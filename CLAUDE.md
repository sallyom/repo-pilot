# repo-pilot

AI-powered CLI assistant that knows how to install, run, and build any GitHub repository.

## Architecture

- **Two-tier context**: Tier 1 (direct file reading for small repos) auto-escalates to Tier 2 (RAG via docs2db) for large repos
- **Scanner** detects build system, docs, CLI framework, container files
- **Indexer** wraps docs2db to create RAG databases from repo content
- **Retriever** wraps docs2db-api to query indexed content
- **Agent** manages LLM conversation with assembled context
- **Profiles** provide ecosystem-specific knowledge (Go, Python, Rust, Node, etc.)

## Tech Stack

- Python 3.12+, uv for package management
- typer for CLI, rich for terminal output
- docs2db + docs2db-api for RAG (optional dependency)
- httpx for LLM API calls (OpenAI-compatible)
- tiktoken for token counting

## Key Design Decisions

- RAG dependencies are optional — tool works in Tier 1 mode without them
- LLM provider is OpenAI-compatible (works with Ollama, OpenAI, Anthropic via proxy)
- Source code is converted to markdown before RAG ingestion
- Repo index cached in `.repo-pilot/` directory within target repo

## Commands

- `repo-pilot [path]` — interactive assistant for the repo
- `repo-pilot index [path]` — pre-build RAG index
- `repo-pilot ask [path] "question"` — one-shot question
