"""System prompts and templates for the repo-pilot agent."""

from __future__ import annotations

from repo_pilot.detector import DetectionResult
from repo_pilot.profiles.base import EcosystemProfile


SYSTEM_PROMPT = """\
You are repo-pilot, an expert assistant for the following repositories:

{repo_list}

Your primary purpose is to help users install, run, and build these projects. You also \
know the codebase, its flags, options, configuration, and documentation.

Prioritize answers in this order:
1. How to install and run the project
2. How to build the project
3. Configuration options, flags, and environment variables
4. Code structure and architecture
5. Contributing and development workflow

When multiple repos are loaded, clarify which repo a file or command belongs to.

{detection_context}

{profile_context}

## Guidelines
- Give concrete, copy-pastable commands when possible.
- If you're unsure, say so rather than guessing.
- Reference specific files and line numbers when discussing code.
- If the user's question can be answered from the provided context, answer directly.
- If you need more information from the repo, say what files you'd want to look at.
"""


def build_detection_context(detection: DetectionResult, label: str = "") -> str:
    """Format detection results into context for the system prompt."""
    lines = [f"## Detected Repository Information{label}"]

    if detection.primary_language:
        lines.append(f"- **Primary language**: {detection.primary_language}")
    if detection.languages:
        lines.append(f"- **Languages**: {', '.join(detection.languages)}")

    for bs in detection.build_systems:
        lines.append(f"\n### Build System: {bs.name}")
        lines.append(f"- Config file: `{bs.file.name}`")
        if bs.install_hint:
            lines.append(f"- Install: `{bs.install_hint}`")
        if bs.build_hint:
            lines.append(f"- Build: `{bs.build_hint}`")
        if bs.run_hint:
            lines.append(f"- Run: `{bs.run_hint}`")
        if bs.test_hint:
            lines.append(f"- Test: `{bs.test_hint}`")

    for cs in detection.containers:
        lines.append(f"\n### Container: `{cs.file.name}`")
        if cs.base_image:
            lines.append(f"- Base image: `{cs.base_image}`")
        if cs.build_cmd:
            lines.append(f"- Build: `{cs.build_cmd}`")
        if cs.run_cmd:
            lines.append(f"- Run: `{cs.run_cmd}`")

    if detection.has_ci:
        lines.append("\n- **CI/CD**: Detected")

    return "\n".join(lines)


def build_profile_context(profiles: list[EcosystemProfile]) -> str:
    """Format ecosystem profile tips into context."""
    if not profiles:
        return ""

    lines = ["## Ecosystem Tips"]
    for profile in profiles:
        lines.append(f"\n### {profile.name}")
        for tip in profile.tips:
            lines.append(f"- {tip}")
        if profile.install_methods:
            lines.append(f"- Common install methods: {', '.join(f'`{m}`' for m in profile.install_methods)}")

    return "\n".join(lines)


def build_system_prompt(
    repo_infos: list[tuple[str, DetectionResult]],
    profiles: list[EcosystemProfile],
) -> str:
    """Assemble the full system prompt.

    Args:
        repo_infos: List of (repo_path, detection_result) tuples.
        profiles: Combined ecosystem profiles from all repos.
    """
    repo_list = "\n".join(f"- `{path}` (primary)" if i == 0 else f"- `{path}`"
                          for i, (path, _) in enumerate(repo_infos))

    detection_parts = []
    for path, detection in repo_infos:
        label = f" ({path.rsplit('/', 1)[-1]})" if len(repo_infos) > 1 else ""
        detection_parts.append(build_detection_context(detection, label=label))

    return SYSTEM_PROMPT.format(
        repo_list=repo_list,
        detection_context="\n\n".join(detection_parts),
        profile_context=build_profile_context(profiles),
    )


def format_file_context(files_by_repo: dict[str, dict[str, str]]) -> str:
    """Format file contents into a context block.

    Args:
        files_by_repo: Mapping of repo name to {relative_path: content}.
    """
    parts = ["\n## Repository File Contents\n"]
    for repo_name, files in files_by_repo.items():
        if len(files_by_repo) > 1:
            parts.append(f"### Repo: `{repo_name}`\n")
        for path, content in files.items():
            prefix = f"{repo_name}/" if len(files_by_repo) > 1 else ""
            parts.append(f"#### `{prefix}{path}`\n```\n{content}\n```\n")
    return "\n".join(parts)


RAG_CONTEXT_HEADER = """
## Retrieved Context (from RAG index)

The following content was retrieved from the repository index based on your question:

"""


def format_rag_context(chunks: list[dict]) -> str:
    """Format RAG retrieval results into a context block."""
    if not chunks:
        return ""
    parts = [RAG_CONTEXT_HEADER]
    for i, chunk in enumerate(chunks, 1):
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "")
        score = chunk.get("score", 0)
        parts.append(f"**[{i}]** Source: `{source}` (relevance: {score:.2f})\n{text}\n")
    return "\n".join(parts)
