"""Scan a repository to discover its structure, docs, build files, and CLI definitions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()

# Files that are almost always relevant for understanding a repo
KEY_FILES = [
    "README*",
    "readme*",
    "INSTALL*",
    "CONTRIBUTING*",
    "CHANGELOG*",
    "LICENSE*",
    "Makefile",
    "CMakeLists.txt",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Gemfile",
    "build.gradle",
    "pom.xml",
    "Dockerfile",
    "Containerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "Jenkinsfile",
    ".gitlab-ci.yml",
    "Taskfile.yml",
    "justfile",
    "Tiltfile",
    "skaffold.yaml",
    "Procfile",
    "Brewfile",
    "flake.nix",
    "shell.nix",
    "default.nix",
]

# Directories likely to contain documentation
DOC_DIRS = ["docs", "doc", "documentation", "wiki", "guide", "guides", "manual", "man"]

# File extensions we consider documentation
DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc", ".html", ".htm", ".pdf"}

# File extensions for source code (used for CLI flag extraction)
SOURCE_EXTENSIONS = {
    ".py", ".go", ".rs", ".js", ".ts", ".rb", ".java", ".c", ".cpp", ".h",
    ".sh", ".bash", ".zsh", ".fish",
}

# Max files to include in a scan (prevents runaway on huge repos)
MAX_SCAN_FILES = 5000

# Directories to always skip
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "venv", ".venv", "env", ".env", "vendor",
    "target", "build", "dist", ".next", ".nuxt", "out", "_build",
    ".repo-pilot", "docs2db_content",
}


@dataclass
class ScanResult:
    """Result of scanning a repository."""

    repo_path: Path
    key_files: list[Path] = field(default_factory=list)
    doc_files: list[Path] = field(default_factory=list)
    source_files: list[Path] = field(default_factory=list)
    build_files: list[Path] = field(default_factory=list)
    container_files: list[Path] = field(default_factory=list)
    ci_files: list[Path] = field(default_factory=list)
    total_files: int = 0
    total_size_bytes: int = 0

    @property
    def all_relevant_files(self) -> list[Path]:
        """All files considered relevant, deduplicated and sorted."""
        seen = set()
        result = []
        for f in self.key_files + self.doc_files + self.build_files + self.container_files + self.ci_files:
            if f not in seen:
                seen.add(f)
                result.append(f)
        return sorted(result)

    def content_size_bytes(self) -> int:
        """Total size of all relevant files."""
        total = 0
        for f in self.all_relevant_files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total


def _match_glob(repo_path: Path, pattern: str) -> list[Path]:
    """Match a glob pattern relative to repo root."""
    return sorted(repo_path.glob(pattern))


def scan_repo(repo_path: Path) -> ScanResult:
    """Scan a repository and categorize its files.

    Args:
        repo_path: Path to the repository root.

    Returns:
        ScanResult with categorized file lists.
    """
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Not a directory: {repo_path}")

    result = ScanResult(repo_path=repo_path)
    log.info("scanning_repo", path=str(repo_path))

    # Find key files
    for pattern in KEY_FILES:
        for match in _match_glob(repo_path, pattern):
            if match.is_file():
                result.key_files.append(match)
                _categorize_file(match, result)

    # Walk the repo for docs and source files
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Prune skipped directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        rel_dir = Path(dirpath).relative_to(repo_path)

        for filename in filenames:
            file_count += 1
            if file_count > MAX_SCAN_FILES:
                log.warning("scan_limit_reached", max_files=MAX_SCAN_FILES)
                break

            filepath = Path(dirpath) / filename
            ext = filepath.suffix.lower()

            try:
                size = filepath.stat().st_size
                result.total_size_bytes += size
            except OSError:
                continue

            # Documentation files
            is_in_doc_dir = any(part.lower() in DOC_DIRS for part in rel_dir.parts)
            if ext in DOC_EXTENSIONS and (is_in_doc_dir or rel_dir == Path(".")):
                if filepath not in result.doc_files and filepath not in result.key_files:
                    result.doc_files.append(filepath)

            # Source files (tracked but not all included in context)
            if ext in SOURCE_EXTENSIONS:
                result.source_files.append(filepath)

        if file_count > MAX_SCAN_FILES:
            break

    result.total_files = file_count
    result.doc_files.sort()
    result.source_files.sort()
    log.info(
        "scan_complete",
        key_files=len(result.key_files),
        doc_files=len(result.doc_files),
        source_files=len(result.source_files),
        total_files=result.total_files,
    )
    return result


def _categorize_file(filepath: Path, result: ScanResult) -> None:
    """Add a file to the appropriate category list."""
    name = filepath.name.lower()
    if name in ("dockerfile", "containerfile") or name.startswith("dockerfile.") or name.startswith("containerfile."):
        result.container_files.append(filepath)
    elif name in ("makefile", "cmakelists.txt", "cargo.toml", "go.mod", "package.json",
                   "pyproject.toml", "setup.py", "setup.cfg", "gemfile", "build.gradle",
                   "pom.xml", "taskfile.yml", "justfile", "flake.nix", "shell.nix",
                   "default.nix", "brewfile", "procfile"):
        result.build_files.append(filepath)
    elif name in ("jenkinsfile", ".gitlab-ci.yml") or ".github/workflows" in str(filepath):
        result.ci_files.append(filepath)
