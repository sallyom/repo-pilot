"""Index repository content into a RAG database using docs2db.

This module converts repo files into a form docs2db can ingest, then runs
the docs2db pipeline to create a searchable vector database.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import structlog

from repo_pilot.scanner import ScanResult

log = structlog.get_logger()

# Token budget: if total relevant content is under this, use Tier 1 (direct context)
TIER1_TOKEN_THRESHOLD = 80_000

# Approximate chars per token
CHARS_PER_TOKEN = 4

INDEX_DIR = ".repo-pilot"
CONTENT_DIR = "docs2db_content"
MANIFEST_FILE = "manifest.json"


def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count."""
    return len(text) // CHARS_PER_TOKEN


def should_use_rag(scan: ScanResult) -> bool:
    """Determine whether this repo needs RAG (Tier 2) or can use direct context (Tier 1)."""
    total_chars = 0
    for f in scan.all_relevant_files:
        try:
            total_chars += f.stat().st_size
        except OSError:
            pass

    estimated_tokens = total_chars // CHARS_PER_TOKEN
    use_rag = estimated_tokens > TIER1_TOKEN_THRESHOLD

    log.info(
        "tier_decision",
        total_files=len(scan.all_relevant_files),
        estimated_tokens=estimated_tokens,
        threshold=TIER1_TOKEN_THRESHOLD,
        tier="rag" if use_rag else "direct",
    )
    return use_rag


def read_direct_context(scan: ScanResult) -> dict[str, str]:
    """Read all relevant files directly for Tier 1 (small repo) context.

    Returns:
        Mapping of relative file path to content string.
    """
    files: dict[str, str] = {}
    for f in scan.all_relevant_files:
        try:
            rel = f.relative_to(scan.repo_path)
            content = f.read_text(errors="replace")
            files[str(rel)] = content
        except (OSError, ValueError):
            continue
    return files


def _source_to_markdown(filepath: Path, content: str) -> str:
    """Convert a source code file to markdown for docs2db ingestion."""
    ext = filepath.suffix.lower()
    lang_map = {
        ".py": "python", ".go": "go", ".rs": "rust", ".js": "javascript",
        ".ts": "typescript", ".rb": "ruby", ".java": "java", ".c": "c",
        ".cpp": "cpp", ".h": "c", ".sh": "bash", ".yaml": "yaml",
        ".yml": "yaml", ".toml": "toml", ".json": "json",
    }
    lang = lang_map.get(ext, "")
    name = filepath.name

    return f"# {name}\n\n```{lang}\n{content}\n```\n"


def prepare_content(scan: ScanResult) -> Path:
    """Prepare repo content as markdown files (writes to repo's .repo-pilot dir)."""
    return prepare_content_to(scan, scan.repo_path / INDEX_DIR / "ingest")


def prepare_content_to(scan: ScanResult, content_dir: Path) -> Path:
    """Prepare repo content as markdown files in the given directory.

    Copies docs directly, converts source files to markdown.
    Returns the path to the prepared content directory.
    """
    content_dir.mkdir(parents=True, exist_ok=True)

    manifest = {}

    for f in scan.all_relevant_files:
        try:
            rel = f.relative_to(scan.repo_path)
            content = f.read_text(errors="replace")
        except (OSError, ValueError):
            continue

        # docs2db handles .md and .html natively
        if f.suffix.lower() in {".md", ".html", ".htm"}:
            dest = content_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            manifest[str(rel)] = {"type": "doc", "format": f.suffix}
        else:
            # Convert everything else to markdown
            md_content = _source_to_markdown(f, content)
            dest = content_dir / f"{rel}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(md_content)
            manifest[str(rel)] = {"type": "source", "format": f.suffix}

    # Write manifest alongside content
    manifest_path = content_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info("content_prepared", files=len(manifest), dest=str(content_dir))

    return content_dir


def build_index(scan: ScanResult, skip_context: bool = True) -> Path | None:
    """Build the RAG index using docs2db pipeline.

    Args:
        scan: Repository scan result.
        skip_context: Skip LLM contextual chunk enrichment (faster).

    Returns:
        Path to the SQL dump file, or None if indexing failed.
    """
    content_dir = prepare_content(scan)
    output_dir = scan.repo_path / INDEX_DIR
    dump_file = output_dir / "ragdb_dump.sql"

    cmd = [
        sys.executable, "-m", "docs2db", "pipeline",
        str(content_dir),
        "--output-file", str(dump_file),
    ]
    if skip_context:
        cmd.append("--skip-context")

    log.info("building_index", cmd=" ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(scan.repo_path),
            timeout=600,
        )
        if result.returncode != 0:
            log.error("index_build_failed", stderr=result.stderr[:500])
            return None

        log.info("index_built", dump=str(dump_file))
        return dump_file

    except FileNotFoundError:
        log.error("docs2db_not_found", hint="Install with: pip install docs2db")
        return None
    except subprocess.TimeoutExpired:
        log.error("index_build_timeout")
        return None


def has_index(repo_path: Path) -> bool:
    """Check if a RAG index already exists for this repo."""
    return (repo_path / INDEX_DIR / "ragdb_dump.sql").exists()
