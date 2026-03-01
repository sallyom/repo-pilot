"""Detect build systems, documentation formats, CLI frameworks, and container strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from repo_pilot.scanner import ScanResult

log = structlog.get_logger()


@dataclass
class BuildSystem:
    """A detected build system."""

    name: str  # e.g., "make", "cargo", "go", "npm", "pip"
    file: Path  # The file that triggered detection
    install_hint: str = ""  # e.g., "make install", "cargo install"
    build_hint: str = ""
    run_hint: str = ""
    test_hint: str = ""


@dataclass
class ContainerStrategy:
    """Detected container build strategy."""

    file: Path
    base_image: str = ""
    build_cmd: str = ""
    run_cmd: str = ""


@dataclass
class DetectionResult:
    """Aggregated detection results for a repository."""

    build_systems: list[BuildSystem] = field(default_factory=list)
    containers: list[ContainerStrategy] = field(default_factory=list)
    doc_formats: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    cli_framework: str | None = None
    has_ci: bool = False
    primary_language: str | None = None

    @property
    def primary_build_system(self) -> BuildSystem | None:
        return self.build_systems[0] if self.build_systems else None


# Build system detection rules: (filename, BuildSystem factory)
_BUILD_RULES: list[tuple[str, callable]] = [
    ("cargo.toml", lambda p: BuildSystem(
        "cargo", p,
        install_hint="cargo install --path .",
        build_hint="cargo build --release",
        run_hint="cargo run",
        test_hint="cargo test",
    )),
    ("go.mod", lambda p: BuildSystem(
        "go", p,
        install_hint="go install ./...",
        build_hint="go build ./...",
        run_hint="go run .",
        test_hint="go test ./...",
    )),
    ("package.json", lambda p: BuildSystem(
        "npm", p,
        install_hint="npm install",
        build_hint="npm run build",
        run_hint="npm start",
        test_hint="npm test",
    )),
    ("pyproject.toml", lambda p: BuildSystem(
        "pip", p,
        install_hint="pip install .",
        build_hint="python -m build",
        run_hint="python -m <module>",
        test_hint="pytest",
    )),
    ("setup.py", lambda p: BuildSystem(
        "pip", p,
        install_hint="pip install .",
        build_hint="python setup.py build",
        run_hint="python -m <module>",
        test_hint="pytest",
    )),
    ("gemfile", lambda p: BuildSystem(
        "bundler", p,
        install_hint="bundle install",
        build_hint="bundle exec rake build",
        run_hint="bundle exec ruby <script>",
        test_hint="bundle exec rake test",
    )),
    ("build.gradle", lambda p: BuildSystem(
        "gradle", p,
        install_hint="./gradlew install",
        build_hint="./gradlew build",
        run_hint="./gradlew run",
        test_hint="./gradlew test",
    )),
    ("pom.xml", lambda p: BuildSystem(
        "maven", p,
        install_hint="mvn install",
        build_hint="mvn package",
        run_hint="mvn exec:java",
        test_hint="mvn test",
    )),
    ("cmakelists.txt", lambda p: BuildSystem(
        "cmake", p,
        install_hint="cmake --build build --target install",
        build_hint="cmake -B build && cmake --build build",
        run_hint="./build/<binary>",
        test_hint="ctest --test-dir build",
    )),
    ("makefile", lambda p: BuildSystem(
        "make", p,
        install_hint="make install",
        build_hint="make",
        run_hint="make run",
        test_hint="make test",
    )),
    ("justfile", lambda p: BuildSystem(
        "just", p,
        install_hint="just install",
        build_hint="just build",
        run_hint="just run",
        test_hint="just test",
    )),
    ("taskfile.yml", lambda p: BuildSystem(
        "task", p,
        install_hint="task install",
        build_hint="task build",
        run_hint="task run",
        test_hint="task test",
    )),
    ("flake.nix", lambda p: BuildSystem(
        "nix", p,
        install_hint="nix profile install",
        build_hint="nix build",
        run_hint="nix run",
        test_hint="nix flake check",
    )),
]


def detect(scan: ScanResult) -> DetectionResult:
    """Run all detectors against a scan result."""
    result = DetectionResult()

    _detect_build_systems(scan, result)
    _detect_containers(scan, result)
    _detect_languages(scan, result)
    _detect_doc_formats(scan, result)
    _detect_ci(scan, result)
    _refine_build_hints(scan, result)

    log.info(
        "detection_complete",
        build_systems=[b.name for b in result.build_systems],
        languages=result.languages,
        primary_language=result.primary_language,
        containers=len(result.containers),
    )
    return result


def _detect_build_systems(scan: ScanResult, result: DetectionResult) -> None:
    """Detect build systems from key files."""
    seen = set()
    for bf in scan.build_files + scan.key_files:
        name = bf.name.lower()
        for rule_name, factory in _BUILD_RULES:
            if name == rule_name and rule_name not in seen:
                seen.add(rule_name)
                result.build_systems.append(factory(bf))


def _detect_containers(scan: ScanResult, result: DetectionResult) -> None:
    """Parse Dockerfiles for base image and commands."""
    for cf in scan.container_files:
        strategy = ContainerStrategy(file=cf)
        strategy.build_cmd = f"docker build -f {cf.name} -t <image> ."
        strategy.run_cmd = "docker run <image>"
        try:
            content = cf.read_text(errors="replace")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("FROM ") and not strategy.base_image:
                    strategy.base_image = stripped.split(None, 1)[1].split(" AS ")[0].strip()
        except OSError:
            pass
        result.containers.append(strategy)


def _detect_languages(scan: ScanResult, result: DetectionResult) -> None:
    """Detect languages from source file extensions."""
    ext_to_lang = {
        ".py": "Python", ".go": "Go", ".rs": "Rust", ".js": "JavaScript",
        ".ts": "TypeScript", ".rb": "Ruby", ".java": "Java", ".c": "C",
        ".cpp": "C++", ".h": "C/C++", ".sh": "Shell", ".bash": "Shell",
    }
    lang_counts: dict[str, int] = {}
    for sf in scan.source_files:
        lang = ext_to_lang.get(sf.suffix.lower())
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    result.languages = sorted(lang_counts, key=lambda l: lang_counts[l], reverse=True)
    if result.languages:
        result.primary_language = result.languages[0]


def _detect_doc_formats(scan: ScanResult, result: DetectionResult) -> None:
    """Detect documentation formats from doc files."""
    formats = set()
    for df in scan.doc_files + scan.key_files:
        ext = df.suffix.lower()
        fmt_map = {".md": "Markdown", ".rst": "reStructuredText", ".adoc": "AsciiDoc",
                   ".html": "HTML", ".txt": "Plain text", ".pdf": "PDF"}
        fmt = fmt_map.get(ext)
        if fmt:
            formats.add(fmt)
    result.doc_formats = sorted(formats)


def _detect_ci(scan: ScanResult, result: DetectionResult) -> None:
    """Check for CI/CD configuration."""
    result.has_ci = len(scan.ci_files) > 0


def _refine_build_hints(scan: ScanResult, result: DetectionResult) -> None:
    """Refine build hints by reading file contents (e.g., npm scripts, Makefile targets)."""
    for bs in result.build_systems:
        if bs.name == "npm":
            _refine_npm(bs)
        elif bs.name == "pip":
            _refine_python(bs)


def _refine_npm(bs: BuildSystem) -> None:
    """Check package.json for actual script names."""
    try:
        import json
        data = json.loads(bs.file.read_text())
        scripts = data.get("scripts", {})

        # Detect package manager from lockfiles
        parent = bs.file.parent
        if (parent / "pnpm-lock.yaml").exists():
            pm = "pnpm"
        elif (parent / "yarn.lock").exists():
            pm = "yarn"
        elif (parent / "bun.lockb").exists() or (parent / "bun.lock").exists():
            pm = "bun"
        else:
            pm = "npm"

        bs.name = pm
        bs.install_hint = f"{pm} install"
        if "build" in scripts:
            bs.build_hint = f"{pm} run build"
        if "start" in scripts:
            bs.run_hint = f"{pm} start" if pm == "npm" else f"{pm} run start"
        if "dev" in scripts:
            bs.run_hint = f"{pm} run dev"
        if "test" in scripts:
            bs.test_hint = f"{pm} test" if pm == "npm" else f"{pm} run test"

        # Check for bin entries (installable CLI)
        if "bin" in data:
            name = data.get("name", "")
            bs.install_hint = f"{pm} install -g {name}" if name else bs.install_hint
    except Exception:
        pass


def _refine_python(bs: BuildSystem) -> None:
    """Check pyproject.toml for project scripts, uv/pip preference."""
    parent = bs.file.parent
    if (parent / "uv.lock").exists():
        bs.install_hint = "uv pip install ."
        bs.run_hint = "uv run <command>"
    elif (parent / "poetry.lock").exists():
        bs.install_hint = "poetry install"
        bs.run_hint = "poetry run <command>"
        bs.name = "poetry"

    try:
        content = bs.file.read_text()
        if "[project.scripts]" in content:
            bs.run_hint = "<script-name>  (see pyproject.toml [project.scripts])"
    except OSError:
        pass
