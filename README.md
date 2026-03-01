# repo-pilot

**Ship documentation as a container image.** Tag `repo-pilot:your-project` alongside your releases — any new team member runs it and asks "how do I deploy this?" without reading a single doc. It's LLM-powered `man pages` — shipped as container images.

```bash
# Bake your repo's knowledge into a self-contained image
repo-pilot bake ~/git/my-project
# → repo-pilot:my-project

# Anyone with the image + an API key can ask questions
podman run --rm -it \
  -e REPO_PILOT_LLM_API_KEY=$ANTHROPIC_API_KEY \
  repo-pilot:my-project \
  ask "How do I install this?" --chat
```

Under the hood, repo-pilot scans your repo, detects build systems and languages, chunks content with BM25 indexing, and answers questions using an LLM with retrieved context. No database, no infrastructure — just an OCI image.

## Install

```bash
# Build the base image
podman build -t repo-pilot:latest -f Containerfile .

# Install the CLI script
./install.sh
```

## Usage

### Bake a repo into a self-contained image

```bash
# Bake a single repo
repo-pilot bake ~/git/repo1
# → repo-pilot:repo1

# Bake with dependencies
repo-pilot bake ~/git/repo1 -a ~/git/repo2 -a ~/git/repo3
# → repo-pilot:repo1-also-repo2-also-repo3

# Custom image name
repo-pilot bake ~/git/repo1 --image my-assistant:v1
```

### Use a baked image

```bash
# One-shot question
podman run --rm -it \
  -e REPO_PILOT_LLM_API_KEY=$ANTHROPIC_API_KEY \
  repo-pilot:repo1 \
  ask "How do I install this?"

# Ask a question, then keep chatting (--chat / -c)
podman run --rm -it \
  -e REPO_PILOT_LLM_API_KEY=$ANTHROPIC_API_KEY \
  repo-pilot:repo1 \
  ask "How do I install this?" --chat

# Interactive mode (no initial question)
podman run --rm -it \
  -e REPO_PILOT_LLM_API_KEY=$ANTHROPIC_API_KEY \
  repo-pilot:repo1
```

### Sandbox mode

The base image includes `kubectl`. Mount your kubeconfig and repo to use the container as a disposable workspace — ask the assistant how, then do it, all in one place:

```bash
# Read-only (safe — just ask questions and run kubectl)
podman run --rm -it \
  -e REPO_PILOT_LLM_API_KEY=$ANTHROPIC_API_KEY \
  -v ~/.kube/config:/root/.kube/config:ro \
  -v ~/git/my-project:/repos/my-project:ro \
  repo-pilot:my-project \
  ask "How do I deploy this?" --chat

# Read-write (create files the assistant suggests)
podman run --rm -it \
  -e REPO_PILOT_LLM_API_KEY=$ANTHROPIC_API_KEY \
  -v ~/.kube/config:/root/.kube/config:ro \
  -v ~/git/my-project:/repos/my-project \
  repo-pilot:my-project

# Shell into a running container to follow along hands-on
podman exec -it <container> bash
```

Nothing touches your host unless you explicitly mount it read-write. When you're done, `exit` and it's gone.

### Scan a repo (no bake needed)

```bash
# Scan to see what's detected
repo-pilot scan ~/git/repo1

# Scan multiple repos
repo-pilot scan ~/git/repo1 -a ~/git/repo2 -a ~/git/repo3

# Ask a question (mounts repo on the fly)
repo-pilot ask "How do I build this?" -r ~/git/repo1
```

## How It Works

1. **Scans** the repo to find build files, docs, source code, Dockerfiles, CI configs
2. **Detects** build system (Make, Cargo, Go, npm/pnpm, pip, etc.), languages, and CLI frameworks
3. **Chunks** content into searchable segments with BM25 indexing
4. **Answers questions** using an LLM with retrieved context

### Bake flow

```
repo-pilot bake ~/git/repo1
```

Under the hood:

1. Mounts your repo read-only into a `repo-pilot:latest` container
2. Scans, prepares (converts source to markdown), and chunks the content
3. `podman commit` saves the container with knowledge baked into `/baked`
4. The resulting image is fully self-contained — just add an API key

### Context tiers

- **Baked** — pre-indexed chunks with BM25 retrieval (fastest, best for repeated use)
- **Tier 1** (small repos, <80K tokens) — reads key files directly into LLM context
- **Tier 2** (large repos) — RAG via docs2db/docs2db-api with PostgreSQL + pgvector

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `REPO_PILOT_LLM_PROVIDER` | `auto` | `anthropic`, `openai`, or `auto` (detects from key/URL) |
| `REPO_PILOT_LLM_BASE_URL` | `https://api.anthropic.com` | LLM API endpoint |
| `REPO_PILOT_LLM_MODEL` | `claude-sonnet-4-6` | Model name |
| `REPO_PILOT_LLM_API_KEY` | | API key |
| `REPO_PILOT_LLM_TEMPERATURE` | `0.3` | Response temperature |
| `REPO_PILOT_LLM_MAX_TOKENS` | `4096` | Max response tokens |

Supports Anthropic (native), and any OpenAI-compatible API (Ollama, vLLM, OpenAI, etc.).

## Architecture

```
repo_pilot/
├── cli.py              # Typer CLI — interactive, ask, scan, bake commands
├── scanner.py          # Repo file discovery and categorization
├── detector.py         # Build system, language, and container detection
├── indexer.py          # Content preparation (markdown conversion)
├── local_retriever.py  # Built-in chunking + BM25 search (no external deps)
├── retriever.py        # docs2db-api integration for full RAG querying
├── agent.py            # LLM conversation with context assembly
├── prompts.py          # System prompts and context formatting
└── profiles/           # Ecosystem-specific knowledge (Go, Python, Rust, Node, C/C++)
```

## License

Apache-2.0
