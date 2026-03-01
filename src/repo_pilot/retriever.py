"""Retrieve context from a RAG database using docs2db-api.

Wraps the docs2db-api UniversalRAGEngine for querying indexed repo content.
Falls back to direct file reading when RAG is unavailable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import structlog

log = structlog.get_logger()


async def query_rag(question: str, max_chunks: int = 10, threshold: float = 0.5) -> list[dict]:
    """Query the RAG database using docs2db-api.

    Uses the CLI interface for simplicity, falling back gracefully if unavailable.

    Returns:
        List of dicts with 'text', 'source', 'score' keys.
    """
    try:
        from docs2db_api.rag.engine import UniversalRAGEngine, RAGConfig
        from docs2db_api.config import DatabaseSettings

        config = RAGConfig(
            similarity_threshold=threshold,
            max_chunks=max_chunks,
            enable_question_refinement=False,
            enable_reranking=True,
        )
        db_config = DatabaseSettings()
        engine = UniversalRAGEngine(config=config, db_config=db_config)
        await engine.start()

        result = await engine.search_documents(question)

        chunks = []
        for doc in result.documents:
            chunks.append({
                "text": doc.get("text", doc.get("content", "")),
                "source": doc.get("source", doc.get("file_path", "unknown")),
                "score": doc.get("score", doc.get("similarity", 0.0)),
            })
        return chunks

    except ImportError:
        log.warning("docs2db_api_not_available", hint="Install with: pip install docs2db-api")
        return []
    except Exception as e:
        log.warning("rag_query_failed", error=str(e))
        return []


def query_rag_cli(question: str, max_chunks: int = 10) -> list[dict]:
    """Query RAG using the docs2db-api CLI (synchronous fallback)."""
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "docs2db_api", "query",
                question,
                "--limit", str(max_chunks),
                "--format", "log",
                "--max-chars", "5000",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning("rag_cli_failed", stderr=result.stderr[:200])
            return []

        # Parse output into chunks (CLI outputs structured text)
        return [{"text": result.stdout, "source": "rag-search", "score": 1.0}]

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def restore_database(dump_file: Path) -> bool:
    """Restore a RAG database from a SQL dump using docs2db-api."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "docs2db_api", "db-restore", str(dump_file)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            log.info("database_restored", dump=str(dump_file))
            return True
        else:
            log.error("db_restore_failed", stderr=result.stderr[:200])
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("db_restore_error", error=str(e))
        return False


def start_database() -> bool:
    """Start the PostgreSQL database container via docs2db-api."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "docs2db_api", "db-start"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
