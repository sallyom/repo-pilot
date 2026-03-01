"""CLI entry point for repo-pilot."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown

from repo_pilot import __version__

app = typer.Typer(
    name="repo-pilot",
    help="AI-powered repo assistant — knows how to install, run, and build any project.",
    no_args_is_help=False,
)
console = Console()


def _resolve_repo(repo_path: str) -> Path:
    """Resolve and validate a repo path."""
    path = Path(repo_path).resolve()
    if not path.is_dir():
        console.print(f"[red]Not a directory: {path}[/red]")
        raise typer.Exit(1)
    return path


def _resolve_additional(also: Optional[list[str]]) -> list[Path]:
    """Resolve and validate additional repo paths."""
    if not also:
        return []
    paths = []
    for p in also:
        path = Path(p).resolve()
        if not path.is_dir():
            console.print(f"[red]Not a directory: {path}[/red]")
            raise typer.Exit(1)
        paths.append(path)
    return paths


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    repo_path: str = typer.Option(
        ".",
        "--repo", "-r",
        help="Path to the primary repository (defaults to current directory).",
    ),
    also: Optional[list[str]] = typer.Option(
        None,
        "--also", "-a",
        help="Additional repositories to include (can be repeated).",
    ),
    version: bool = typer.Option(
        False, "--version", "-v",
        help="Show version and exit.",
    ),
) -> None:
    """Interactive assistant for the repository.

    Run without a subcommand to enter interactive mode.
    Use --also to include dependent repositories.
    """
    if version:
        console.print(f"repo-pilot {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    _interactive(_resolve_repo(repo_path), _resolve_additional(also))


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask about the repository."),
    repo_path: str = typer.Option(".", "--repo", "-r", help="Path to the repository."),
    also: Optional[list[str]] = typer.Option(
        None, "--also", "-a", help="Additional repositories to include.",
    ),
    chat: bool = typer.Option(
        False, "--chat", "-c",
        help="Continue to interactive mode after answering the first question.",
    ),
) -> None:
    """Ask a one-shot question about the repository."""
    path = _resolve_repo(repo_path)

    from repo_pilot.agent import Agent

    agent = Agent(repo_path=path, additional_repos=_resolve_additional(also))

    with console.status("Thinking..."):
        response = asyncio.run(agent.ask(question))

    console.print()
    console.print(Markdown(response))

    if chat:
        console.print()
        _interactive_loop(agent)


@app.command()
def scan(
    repo_path: str = typer.Argument(".", help="Path to the repository."),
    also: Optional[list[str]] = typer.Option(
        None, "--also", "-a", help="Additional repositories to include.",
    ),
) -> None:
    """Scan a repository and show what was detected."""
    path = _resolve_repo(repo_path)

    from repo_pilot.agent import Agent

    agent = Agent(repo_path=path, additional_repos=_resolve_additional(also))
    console.print(Panel(agent.summary(), title="repo-pilot scan", border_style="blue"))


@app.command()
def index(
    repo_path: str = typer.Argument(".", help="Path to the repository."),
    skip_context: bool = typer.Option(
        True, "--skip-context/--with-context",
        help="Skip LLM contextual chunk enrichment (faster).",
    ),
) -> None:
    """Build a RAG index for the repository using docs2db."""
    path = _resolve_repo(repo_path)

    from repo_pilot.indexer import build_index
    from repo_pilot.scanner import scan_repo

    scan_result = scan_repo(path)

    with console.status("Building RAG index..."):
        dump = build_index(scan_result, skip_context=skip_context)

    if dump:
        console.print(f"[green]Index built: {dump}[/green]")
        console.print("Restore with: [bold]repo-pilot restore[/bold]")
    else:
        console.print("[red]Index build failed. Is docs2db installed?[/red]")
        console.print("Install with: [bold]pip install docs2db[/bold]")
        raise typer.Exit(1)


@app.command()
def restore(
    repo_path: str = typer.Argument(".", help="Path to the repository."),
) -> None:
    """Restore the RAG database from a previously built index."""
    path = _resolve_repo(repo_path)
    dump_file = path / ".repo-pilot" / "ragdb_dump.sql"

    if not dump_file.exists():
        console.print("[red]No index found. Run 'repo-pilot index' first.[/red]")
        raise typer.Exit(1)

    from repo_pilot.retriever import restore_database, start_database

    with console.status("Starting database..."):
        if not start_database():
            console.print("[yellow]Could not start database container. Trying restore anyway...[/yellow]")

    with console.status("Restoring database..."):
        if restore_database(dump_file):
            console.print("[green]Database restored and ready.[/green]")
        else:
            console.print("[red]Database restore failed.[/red]")
            raise typer.Exit(1)


@app.command(name="_bake_internal", hidden=True)
def bake_internal(
    repo_paths: list[str] = typer.Argument(..., help="Paths to repos mounted in the container."),
) -> None:
    """Internal: scan, chunk, and write baked content to /baked. Runs inside a container."""
    import tempfile

    from repo_pilot.indexer import prepare_content_to
    from repo_pilot.local_retriever import chunk_repo_content, save_chunks
    from repo_pilot.scanner import scan_repo

    baked_dir = Path("/baked")
    baked_dir.mkdir(parents=True, exist_ok=True)

    all_chunks = []
    for repo_path_str in repo_paths:
        path = Path(repo_path_str)
        if not path.is_dir():
            console.print(f"[red]Not a directory: {path}[/red]")
            raise typer.Exit(1)

        console.print(f"Scanning {path.name}...")
        scan_result = scan_repo(path)
        console.print(f"  {len(scan_result.all_relevant_files)} relevant files")

        console.print("Preparing content...")
        work_dir = Path(tempfile.mkdtemp(prefix=f"rp-{path.name}-"))
        content_dir = prepare_content_to(scan_result, work_dir)

        console.print("Chunking...")
        chunks = chunk_repo_content(content_dir)
        all_chunks.extend(chunks)
        console.print(f"  {len(chunks)} chunks from {path.name}")

    save_chunks(all_chunks, baked_dir)
    console.print(f"[green]Baked {len(all_chunks)} chunks to {baked_dir}[/green]")


def _interactive(repo_path: Path, additional_repos: list[Path] | None = None) -> None:
    """Run the interactive conversation loop (with setup)."""
    from repo_pilot.agent import Agent

    console.print(
        Panel(
            "[bold]repo-pilot[/bold] — your AI repo assistant\n"
            "Type your question, or:\n"
            "  [dim]/scan[/dim]    — show repo detection summary\n"
            "  [dim]/index[/dim]   — build RAG index\n"
            "  [dim]/quit[/dim]    — exit",
            border_style="blue",
        )
    )

    agent = Agent(repo_path=repo_path, additional_repos=additional_repos or [])

    with console.status("Scanning repository..."):
        agent.initialize()

    console.print(f"[dim]{agent.summary()}[/dim]\n")
    _interactive_loop(agent)


def _interactive_loop(agent) -> None:
    """Run the interactive question loop with an already-initialized agent."""
    while True:
        try:
            question = Prompt.ask("[bold blue]>[/bold blue]")
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        question = question.strip()
        if not question:
            continue

        if question.lower() in ("/quit", "/exit", "/q", "quit", "exit"):
            console.print("Bye!")
            break

        if question.lower() in ("/scan", "/info"):
            console.print(Panel(agent.summary(), title="scan", border_style="blue"))
            continue

        if question.lower() in ("/index", "/build-index"):
            from repo_pilot.indexer import build_index

            with console.status("Building RAG index..."):
                dump = build_index(agent.scan, skip_context=True)
            if dump:
                console.print(f"[green]Index built: {dump}[/green]")
                agent.use_rag = True
            else:
                console.print("[red]Index build failed.[/red]")
            continue

        with console.status("Thinking..."):
            response = asyncio.run(agent.ask(question))

        console.print()
        console.print(Markdown(response))
        console.print()
