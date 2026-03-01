"""Base ecosystem profile and profile detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repo_pilot.detector import DetectionResult


@dataclass
class EcosystemProfile:
    """Knowledge about a specific ecosystem's conventions.

    Profiles tell the agent where to look for CLI flags, config options,
    install procedures, and other ecosystem-specific patterns.
    """

    name: str
    description: str

    # Patterns to search for CLI flag/option definitions in source code
    flag_patterns: list[str] = field(default_factory=list)

    # Files that typically contain important config or entry points
    important_files: list[str] = field(default_factory=list)

    # Common install methods beyond what the build system provides
    install_methods: list[str] = field(default_factory=list)

    # Tips for the LLM about this ecosystem
    tips: list[str] = field(default_factory=list)


# --- Built-in profiles ---

GO_PROFILE = EcosystemProfile(
    name="Go",
    description="Go modules project",
    flag_patterns=[
        r'flag\.\w+\(',           # flag.String(, flag.Int(, etc.
        r'pflag\.\w+\(',          # spf13/pflag
        r'cobra\.Command\{',     # cobra CLI framework
        r'cli\.Command\{',       # urfave/cli
        r'kong\.Parse\(',         # alecthomas/kong
    ],
    important_files=["go.mod", "go.sum", "main.go", "cmd/*/main.go", "Makefile"],
    install_methods=["go install ./...", "go build -o <binary> ."],
    tips=[
        "Check cmd/ directory for CLI entry points",
        "Go projects often have multiple binaries in cmd/<name>/",
        "Makefile targets often wrap go build with ldflags",
    ],
)

PYTHON_PROFILE = EcosystemProfile(
    name="Python",
    description="Python project",
    flag_patterns=[
        r'argparse\.ArgumentParser',
        r'\.add_argument\(',
        r'@click\.\w+\(',         # click decorators
        r'@app\.command',         # typer
        r'typer\.Option\(',
        r'typer\.Argument\(',
    ],
    important_files=[
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "Pipfile", "tox.ini", "noxfile.py",
    ],
    install_methods=["pip install .", "pip install -e .", "uv pip install .", "pipx install ."],
    tips=[
        "Check [project.scripts] in pyproject.toml for CLI entry points",
        "Look for __main__.py for python -m invocation",
        "requirements.txt may have pinned versions vs pyproject.toml ranges",
    ],
)

RUST_PROFILE = EcosystemProfile(
    name="Rust",
    description="Rust/Cargo project",
    flag_patterns=[
        r'#\[arg\(',             # clap derive
        r'#\[command\(',
        r'Arg::new\(',           # clap builder
        r'Command::new\(',
        r'structopt',
    ],
    important_files=["Cargo.toml", "Cargo.lock", "src/main.rs", "src/lib.rs", "build.rs"],
    install_methods=["cargo install --path .", "cargo install <crate-name>"],
    tips=[
        "Check Cargo.toml [[bin]] sections for multiple binaries",
        "Features in Cargo.toml can enable/disable functionality",
        "Build profiles (dev/release) affect binary location in target/",
    ],
)

NODE_PROFILE = EcosystemProfile(
    name="Node.js",
    description="Node.js / JavaScript / TypeScript project",
    flag_patterns=[
        r'commander\.\w+\(',    # commander.js
        r'\.option\(',
        r'yargs\.',
        r'meow\(',
        r'\.command\(',
    ],
    important_files=[
        "package.json", "tsconfig.json", ".nvmrc", ".node-version",
        "webpack.config.*", "vite.config.*", "next.config.*",
    ],
    install_methods=["npm install", "pnpm install", "yarn install", "bun install"],
    tips=[
        "Check package.json 'scripts' for all available commands",
        "bin field in package.json defines installable CLI commands",
        ".nvmrc or .node-version specify required Node.js version",
    ],
)

CONTAINER_PROFILE = EcosystemProfile(
    name="Container",
    description="Containerized application",
    flag_patterns=[],
    important_files=[
        "Dockerfile", "Containerfile", "docker-compose.yml", "compose.yml",
        ".dockerignore", "skaffold.yaml", "Tiltfile",
    ],
    install_methods=["docker build -t <image> .", "docker compose up"],
    tips=[
        "Multi-stage builds: check all FROM stages for build dependencies",
        "ENTRYPOINT vs CMD affects how args are passed",
        "docker-compose.yml may define required environment variables",
        "Check .dockerignore to understand what's excluded from builds",
    ],
)

C_CPP_PROFILE = EcosystemProfile(
    name="C/C++",
    description="C or C++ project",
    flag_patterns=[
        r'getopt\(',
        r'getopt_long\(',
        r'option\s+long_options',
        r'argp_parse\(',
        r'boost::program_options',
    ],
    important_files=[
        "CMakeLists.txt", "Makefile", "configure", "configure.ac",
        "meson.build", "conanfile.txt", "vcpkg.json",
    ],
    install_methods=["make && make install", "cmake --build build --target install"],
    tips=[
        "Check configure --help for available build options",
        "CMake cache variables (-D flags) control build configuration",
        "Look for man pages in man/ or doc/ directories",
    ],
)

_LANG_TO_PROFILE = {
    "Go": GO_PROFILE,
    "Python": PYTHON_PROFILE,
    "Rust": RUST_PROFILE,
    "JavaScript": NODE_PROFILE,
    "TypeScript": NODE_PROFILE,
    "C": C_CPP_PROFILE,
    "C++": C_CPP_PROFILE,
    "C/C++": C_CPP_PROFILE,
}


def detect_profiles(detection: DetectionResult) -> list[EcosystemProfile]:
    """Return matching ecosystem profiles based on detection results."""
    profiles: list[EcosystemProfile] = []
    seen = set()

    for lang in detection.languages:
        profile = _LANG_TO_PROFILE.get(lang)
        if profile and profile.name not in seen:
            seen.add(profile.name)
            profiles.append(profile)

    if detection.containers and CONTAINER_PROFILE.name not in seen:
        profiles.append(CONTAINER_PROFILE)

    return profiles
