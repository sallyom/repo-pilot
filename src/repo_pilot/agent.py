"""LLM conversation agent with context assembly and RAG integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import structlog
from pydantic_settings import BaseSettings

from repo_pilot.detector import DetectionResult, detect
from repo_pilot.indexer import has_index, read_direct_context, should_use_rag
from repo_pilot.local_retriever import LocalRetriever, get_baked_retriever
from repo_pilot.profiles.base import EcosystemProfile, detect_profiles
from repo_pilot.prompts import (
    build_system_prompt,
    format_file_context,
    format_rag_context,
)
from repo_pilot.retriever import query_rag
from repo_pilot.scanner import ScanResult, scan_repo

log = structlog.get_logger()


class LLMSettings(BaseSettings):
    """LLM provider configuration, read from environment."""

    model_config = {"env_prefix": "REPO_PILOT_"}

    llm_provider: str = "auto"  # "auto", "anthropic", or "openai"
    llm_base_url: str = "https://api.anthropic.com"
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str = ""
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

    def resolved_provider(self) -> str:
        """Determine provider from explicit setting or API key hints."""
        if self.llm_provider != "auto":
            return self.llm_provider
        if "anthropic" in self.llm_base_url:
            return "anthropic"
        if self.llm_api_key.startswith("sk-ant-"):
            return "anthropic"
        return "openai"


@dataclass
class Message:
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class RepoInfo:
    """Scan and detection results for a single repository."""

    path: Path
    name: str
    scan: ScanResult
    detection: DetectionResult
    profiles: list[EcosystemProfile]
    rag_recommended: bool = False
    use_rag: bool = False


@dataclass
class Agent:
    """Conversational agent that answers questions about one or more repositories."""

    repo_path: Path
    additional_repos: list[Path] = field(default_factory=list)
    repos: list[RepoInfo] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    direct_context: dict[str, dict[str, str]] = field(default_factory=dict)
    settings: LLMSettings = field(default_factory=LLMSettings)
    _local_retriever: LocalRetriever | None = None
    _baked: bool = False
    _initialized: bool = False

    @property
    def all_repo_paths(self) -> list[Path]:
        return [self.repo_path] + self.additional_repos

    @property
    def any_rag_active(self) -> bool:
        return any(r.use_rag for r in self.repos)

    @property
    def any_rag_recommended(self) -> bool:
        return any(r.rag_recommended for r in self.repos)

    # Backward-compat aliases used by CLI
    @property
    def scan(self) -> ScanResult | None:
        return self.repos[0].scan if self.repos else None

    @property
    def use_rag(self) -> bool:
        return self.any_rag_active

    @use_rag.setter
    def use_rag(self, value: bool) -> None:
        if self.repos:
            self.repos[0].use_rag = value

    def initialize(self) -> None:
        """Scan all repos and set up context."""
        if self._initialized:
            return

        # Check for baked content (pre-indexed in container image)
        baked = get_baked_retriever()
        if baked is not None:
            self._local_retriever = baked
            self._baked = True
            log.info("using_baked_content")

        for repo_path in self.all_repo_paths:
            scan = scan_repo(repo_path)
            detection = detect(scan)
            profiles = detect_profiles(detection)
            name = repo_path.name

            info = RepoInfo(
                path=repo_path,
                name=name,
                scan=scan,
                detection=detection,
                profiles=profiles,
            )

            # Decide tier per repo
            info.rag_recommended = should_use_rag(scan)
            info.use_rag = info.rag_recommended and has_index(repo_path)

            if not info.use_rag:
                self.direct_context[name] = read_direct_context(scan)
                if info.rag_recommended:
                    log.info("rag_recommended_but_no_index", repo=name,
                             files=len(self.direct_context[name]),
                             hint=f"Run 'repo-pilot index {repo_path}' for better results")
                else:
                    log.info("using_direct_context", repo=name,
                             files=len(self.direct_context[name]))
            else:
                log.info("using_rag_context", repo=name)

            self.repos.append(info)

        # Combine profiles (deduplicated)
        all_profiles: list[EcosystemProfile] = []
        seen_names: set[str] = set()
        for info in self.repos:
            for p in info.profiles:
                if p.name not in seen_names:
                    seen_names.add(p.name)
                    all_profiles.append(p)

        # Build system prompt
        repo_infos = [(str(info.path), info.detection) for info in self.repos]
        system_prompt = build_system_prompt(repo_infos, all_profiles)

        # Append direct file context
        if self.direct_context:
            system_prompt += "\n" + format_file_context(self.direct_context)

        self.messages = [Message(role="system", content=system_prompt)]
        self._initialized = True

    async def ask(self, question: str) -> str:
        """Ask a question about the repositories.

        Assembles context (direct or RAG), sends to LLM, returns response.
        """
        self.initialize()

        # Retrieve relevant chunks via local index or remote RAG
        rag_context = ""
        if self._local_retriever is not None:
            chunks = self._local_retriever.search(question)
            if chunks:
                rag_context = format_rag_context(chunks)
        elif self.any_rag_active:
            chunks = await query_rag(question)
            if chunks:
                rag_context = format_rag_context(chunks)

        user_content = question
        if rag_context:
            user_content = f"{rag_context}\n\n---\n\n**Question:** {question}"

        self.messages.append(Message(role="user", content=user_content))

        # Call LLM
        response = await self._call_llm()
        self.messages.append(Message(role="assistant", content=response))

        return response

    async def _call_llm(self) -> str:
        """Send conversation to the LLM and return the response."""
        provider = self.settings.resolved_provider()

        if provider == "anthropic":
            return await self._call_anthropic()
        else:
            return await self._call_openai_compat()

    async def _call_anthropic(self) -> str:
        """Call the Anthropic Messages API."""
        # Separate system message from conversation
        system_content = ""
        api_messages = []
        for m in self.messages:
            if m.role == "system":
                system_content = m.content
            else:
                api_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": self.settings.llm_model,
            "max_tokens": self.settings.llm_max_tokens,
            "temperature": self.settings.llm_temperature,
            "messages": api_messages,
        }
        if system_content:
            payload["system"] = system_content

        headers = {
            "x-api-key": self.settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.settings.llm_base_url}/v1/messages",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                # Anthropic returns content as a list of blocks
                return "".join(
                    block["text"] for block in data["content"]
                    if block["type"] == "text"
                )

        except httpx.ConnectError:
            return self._connection_error_msg()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            log.error("anthropic_api_error", status=e.response.status_code, body=body)
            return f"Anthropic API error ({e.response.status_code}): {body}"
        except Exception as e:
            log.error("llm_call_failed", error=str(e))
            return f"LLM request failed: {e}"

    async def _call_openai_compat(self) -> str:
        """Call an OpenAI-compatible API (Ollama, vLLM, OpenAI, etc.)."""
        payload = {
            "model": self.settings.llm_model,
            "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream": False,
        }
        headers = {}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

        except httpx.ConnectError:
            return self._connection_error_msg()
        except Exception as e:
            log.error("llm_call_failed", error=str(e))
            return f"LLM request failed: {e}"

    def _connection_error_msg(self) -> str:
        return (
            f"Could not connect to LLM at {self.settings.llm_base_url}.\n\n"
            "Configure with environment variables:\n"
            "  REPO_PILOT_LLM_PROVIDER=anthropic  (or openai)\n"
            "  REPO_PILOT_LLM_API_KEY=your-key\n"
            "  REPO_PILOT_LLM_MODEL=claude-sonnet-4-6\n"
        )

    def summary(self) -> str:
        """Return a summary of what was detected about all repos."""
        self.initialize()
        lines = []

        for info in self.repos:
            if len(self.repos) > 1:
                lines.append(f"--- {info.name} ---")
            lines.append(f"Repository: {info.path}")
            if info.detection.primary_language:
                lines.append(f"Language:   {info.detection.primary_language}")
            for bs in info.detection.build_systems:
                lines.append(f"Build:      {bs.name} ({bs.file.name})")
                if bs.install_hint:
                    lines.append(f"  Install:  {bs.install_hint}")
                if bs.run_hint:
                    lines.append(f"  Run:      {bs.run_hint}")
            for cs in info.detection.containers:
                lines.append(f"Container:  {cs.file.name}")
                if cs.base_image:
                    lines.append(f"  Image:    {cs.base_image}")
            lines.append(f"Files:      {info.scan.total_files} total, "
                         f"{len(info.scan.all_relevant_files)} indexed")
            if self._baked:
                lines.append("Context:    Baked (local BM25 index)")
            elif info.use_rag:
                lines.append("Context:    RAG (Tier 2)")
            elif info.rag_recommended:
                lines.append("Context:    Direct (Tier 1) — RAG recommended, run 'repo-pilot index'")
            else:
                lines.append("Context:    Direct (Tier 1)")

            if len(self.repos) > 1:
                lines.append("")

        return "\n".join(lines)
