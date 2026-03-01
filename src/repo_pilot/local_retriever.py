"""Lightweight local retriever using BM25 over chunk files.

Includes built-in chunking (no docs2db needed) and BM25 search.
Used by baked container images for self-contained retrieval.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# Chunking parameters
MAX_CHUNK_CHARS = 1500
OVERLAP_CHARS = 200


@dataclass
class Chunk:
    """A text chunk with metadata."""

    text: str
    source: str
    score: float = 0.0


def chunk_file(content: str, source: str) -> list[Chunk]:
    """Split a file's content into chunks at paragraph boundaries."""
    paragraphs = re.split(r'\n\s*\n', content)
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if current_len + len(para) > MAX_CHUNK_CHARS and current_parts:
            chunks.append(Chunk(text="\n\n".join(current_parts), source=source))
            # Keep last part for overlap
            overlap = current_parts[-1] if len(current_parts[-1]) <= OVERLAP_CHARS else ""
            current_parts = [overlap] if overlap else []
            current_len = len(overlap)

        current_parts.append(para)
        current_len += len(para)

    if current_parts:
        text = "\n\n".join(current_parts)
        if text.strip():
            chunks.append(Chunk(text=text, source=source))

    return chunks


def chunk_repo_content(prepared_dir: Path) -> list[Chunk]:
    """Chunk all prepared markdown files in a directory."""
    chunks: list[Chunk] = []
    for md_file in sorted(prepared_dir.rglob("*.md")):
        try:
            content = md_file.read_text(errors="replace")
            source = str(md_file.relative_to(prepared_dir))
            file_chunks = chunk_file(content, source)
            chunks.extend(file_chunks)
        except OSError:
            continue

    # Also handle .html files
    for html_file in sorted(prepared_dir.rglob("*.html")):
        try:
            content = html_file.read_text(errors="replace")
            source = str(html_file.relative_to(prepared_dir))
            chunks.extend(chunk_file(content, source))
        except OSError:
            continue

    log.info("chunked_content", total_chunks=len(chunks))
    return chunks


def save_chunks(chunks: list[Chunk], output_dir: Path) -> None:
    """Save chunks to a JSON file for later retrieval."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data = [{"text": c.text, "source": c.source} for c in chunks]
    (output_dir / "chunks.json").write_text(json.dumps(data, indent=2))
    log.info("chunks_saved", count=len(data), path=str(output_dir / "chunks.json"))


class BM25:
    """Okapi BM25 ranking — same algorithm as PostgreSQL full-text search."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: list[Chunk] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_freqs: dict[str, int] = {}
        self.avgdl: float = 0.0
        self.N: int = 0

    def index(self, chunks: list[Chunk]) -> None:
        """Build the BM25 index from chunks."""
        self.documents = chunks
        self.doc_tokens = [_tokenize(c.text) for c in chunks]
        self.N = len(chunks)

        if self.N == 0:
            return

        self.avgdl = sum(len(d) for d in self.doc_tokens) / self.N
        self.doc_freqs = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1

        log.info("bm25_indexed", chunks=self.N, vocab=len(self.doc_freqs))

    def search(self, query: str, top_k: int = 10) -> list[Chunk]:
        """Search for the most relevant chunks."""
        if not self.documents:
            return []

        query_tokens = _tokenize(query)
        scored: list[tuple[float, int]] = []

        for i, doc_tokens in enumerate(self.doc_tokens):
            tf = Counter(doc_tokens)
            score = 0.0
            dl = len(doc_tokens)

            for term in query_tokens:
                if term not in tf:
                    continue
                df = self.doc_freqs.get(term, 0)
                idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)
                term_freq = tf[term]
                tf_norm = (term_freq * (self.k1 + 1)) / (
                    term_freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                )
                score += idf * tf_norm

            if score > 0:
                scored.append((score, i))

        scored.sort(reverse=True)

        results = []
        for score, idx in scored[:top_k]:
            chunk = self.documents[idx]
            results.append(Chunk(text=chunk.text, source=chunk.source, score=score))
        return results


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return [t for t in re.split(r'[^a-z0-9_]+', text.lower()) if len(t) > 1]


def load_chunks(content_dir: Path) -> list[Chunk]:
    """Load chunks from a directory containing chunks.json files."""
    chunks: list[Chunk] = []

    for chunks_file in content_dir.rglob("chunks.json"):
        try:
            data = json.loads(chunks_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("chunk_load_failed", file=str(chunks_file), error=str(e))
            continue

        chunk_list = data if isinstance(data, list) else data.get("chunks", [])

        for entry in chunk_list:
            text = _extract_chunk_text(entry)
            if text:
                source = entry.get("source", "") if isinstance(entry, dict) else ""
                if not source:
                    source = str(chunks_file.parent.relative_to(content_dir))
                chunks.append(Chunk(text=text, source=source))

    log.info("chunks_loaded", total=len(chunks), content_dir=str(content_dir))
    return chunks


def _extract_chunk_text(entry: Any) -> str:
    """Extract text from a chunk entry, handling various formats."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return (
            entry.get("contextual_text")
            or entry.get("text")
            or entry.get("content")
            or entry.get("chunk_text")
            or ""
        )
    return ""


@dataclass
class LocalRetriever:
    """BM25-based retriever over local chunk files."""

    content_dirs: list[Path] = field(default_factory=list)
    _bm25: BM25 | None = None

    def initialize(self) -> None:
        """Load chunks and build BM25 index."""
        all_chunks: list[Chunk] = []
        for content_dir in self.content_dirs:
            if content_dir.exists():
                all_chunks.extend(load_chunks(content_dir))

        if all_chunks:
            self._bm25 = BM25()
            self._bm25.index(all_chunks)
        else:
            log.warning("no_chunks_found", dirs=[str(d) for d in self.content_dirs])

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Search for relevant chunks."""
        if self._bm25 is None:
            self.initialize()

        if self._bm25 is None or self._bm25.N == 0:
            return []

        results = self._bm25.search(query, top_k=top_k)
        return [
            {"text": r.text, "source": r.source, "score": r.score}
            for r in results
        ]


# Well-known paths for baked content
BAKED_CONTENT_ENV = "REPO_PILOT_BAKED"
BAKED_CONTENT_DEFAULT = "/baked"


def get_baked_retriever() -> LocalRetriever | None:
    """Return a retriever for baked content if available."""
    import os
    baked = os.environ.get(BAKED_CONTENT_ENV, BAKED_CONTENT_DEFAULT)
    baked_path = Path(baked)

    if baked_path.exists() and any(baked_path.rglob("chunks.json")):
        log.info("baked_content_detected", path=str(baked_path))
        retriever = LocalRetriever(content_dirs=[baked_path])
        retriever.initialize()
        return retriever

    return None
