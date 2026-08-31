#!/usr/bin/env python3
"""Validate the public surface without opening sensitive files or local data."""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".editorconfig", ".gitattributes", ".gitignore", "LICENSE", "METADATA", "WHEEL"}
EXCLUDED_DIRS = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "htmlcov",
    "vaults",
}
SENSITIVE_NAMES = {
    ".netrc",
    ".npmrc",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
SENSITIVE_SUFFIXES = {".crt", ".key", ".p12", ".pem", ".pfx"}
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
    "github.com",
    "test.com",
    "users.noreply.github.com",
    "vault-search.invalid",
}
GENERIC_GIT_IDENTITIES = {
    ("vault search mcp maintainers", "noreply@vault-search.invalid"),
}
SYNTHETIC_FIXTURE_MARKER = "publication-check: synthetic-fixture"
ALLOWED_TRACKED_LOCAL_PATHS = {
    Path(".github/ISSUE_TEMPLATE/config.yml"),
    Path("vaults/.gitkeep"),
}
TRACKED_LOCAL_DIRS = {"data", "vaults"}
TRACKED_BUILD_DIRS = {"build", "dist", "htmlcov"}
ARCHIVE_LOCAL_DIRS = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "dist",
    "htmlcov",
    "vaults",
}
MAX_ARCHIVE_TEXT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    code: str
    path: Path
    line: int | None
    detail: str

    def render(self, root: Path) -> str:
        relative = self.path.relative_to(root)
        location = f"{relative}:{self.line}" if self.line else str(relative)
        return f"[{self.code}] {location}: {self.detail}"


CONTENT_PATTERNS = {
    "LOCAL_PATH": re.compile(
        r"(?:/(?:Users|home|root)/|/var/folders/|/Volumes/)[^/\s`\"']+|[A-Za-z]:\\Users\\[^\\\s]+",  # publication-check: synthetic-fixture
    ),
    "PLACEHOLDER_URL": re.compile(
        r"github\.com/(?:user|example-user|your-username)(?:/|\b)|your-username|example-user",  # publication-check: synthetic-fixture
        re.IGNORECASE,
    ),
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS_KEY": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GITHUB_TOKEN": re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}\b"),
    "OPENAI_TOKEN": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "SLACK_TOKEN": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "GOOGLE_API_KEY": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "GITLAB_TOKEN": re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}\b"),
    "STRIPE_SECRET": re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b"),
    "SECRET_ASSIGNMENT": re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*[\"'][0-9A-Za-z_./+=-]{16,}[\"']"
    ),
}
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
MARKDOWN_REFERENCE_DEFINITION = re.compile(
    r"^[ \t]{0,3}\[([^\]\n]+)\]:[ \t]*(?:<([^>\n]+)>|(\S+))",
    re.MULTILINE,
)
MARKDOWN_REFERENCE_LINK = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
DESTRUCTIVE_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:rm|rmdir|unlink|shred)\s+",
    re.MULTILINE,
)


def is_sensitive_path(path: Path) -> bool:
    """Identify prohibited names without reading the file."""
    lowered = path.name.lower()
    return (
        lowered in SENSITIVE_NAMES
        or path.suffix.lower() in SENSITIVE_SUFFIXES
        or lowered.startswith("service_account")
        and path.suffix.lower() == ".json"
        or path.parts[-2:] in ((".ssh", "config"), (".aws", "credentials"), (".kube", "config"))
    )


def walk_public_files(root: Path) -> tuple[list[Path], list[Finding]]:
    """List public text files and report local artifacts without opening them."""
    text_files: list[Path] = []
    findings: list[Finding] = []

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts[:-1]):
            continue
        if path.is_symlink():
            findings.append(
                Finding(
                    "PUBLIC_SYMLINK",
                    path,
                    None,
                    "public symlinks are not allowed; publish a regular file",
                )
            )
            continue

        if (
            path.name
            in {
                ".AppleDouble",
                ".DS_Store",
                ".LSOverride",
                ".Spotlight-V100",
                ".Trashes",
                "__MACOSX",
            }
            or path.name.startswith("._")
            or path.name.endswith(".egg-info")
        ):
            findings.append(Finding("GENERATED_ARTIFACT", path, None, "local artifact present"))
            continue
        if is_sensitive_path(path):
            findings.append(Finding("SENSITIVE_FILE", path, None, "sensitive filename"))
            continue
        if path.is_dir():
            continue
        if relative.as_posix() in {"config.yaml", "config.yml"}:
            findings.append(Finding("LOCAL_CONFIG", path, None, "local configuration at root"))
            continue
        if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            findings.append(Finding("LOCAL_CONFIG", path, None, "local environment file"))
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            text_files.append(path)

    return text_files, findings


def _is_local_config_name(path: Path) -> bool:
    """Identify local configuration by name without opening its contents."""
    lowered = path.name.lower()
    if lowered == ".env" or lowered.startswith(".env.") and lowered != ".env.example":
        return True
    return lowered in {
        "config.yaml",
        "config.yml",
        "config.local.yaml",
        "config.local.yml",
    }


def check_repository_paths(root: Path, tracked_paths: list[Path]) -> list[Finding]:
    """Reject local or generated payloads tracked by Git."""
    findings: list[Finding] = []
    for relative in sorted(tracked_paths):
        if relative in ALLOWED_TRACKED_LOCAL_PATHS:
            continue
        path = root / relative
        if is_sensitive_path(relative):
            findings.append(Finding("SENSITIVE_FILE", path, None, "tracked sensitive file"))
            continue
        if _is_local_config_name(relative):
            findings.append(
                Finding("TRACKED_LOCAL_CONFIG", path, None, "tracked local configuration")
            )
            continue
        first_part = relative.parts[0] if relative.parts else ""
        if first_part in TRACKED_LOCAL_DIRS:
            findings.append(Finding("TRACKED_LOCAL_DATA", path, None, "local data tracked by Git"))
        elif first_part in TRACKED_BUILD_DIRS:
            findings.append(
                Finding("TRACKED_BUILD_ARTIFACT", path, None, "generated artifact tracked by Git")
            )
    return findings


def check_repository_inventory(root: Path) -> list[Finding]:
    """Read the tracked tree without inspecting history or sensitive contents."""
    if not (root / ".git").exists():
        return []
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        return [
            Finding(
                "GIT_INVENTORY",
                root / ".git",
                None,
                f"could not list the tracked tree ({type(error).__name__})",
            )
        ]

    tracked_paths = [
        Path(raw.decode("utf-8", errors="surrogateescape"))
        for raw in completed.stdout.split(b"\0")
        if raw
    ]
    return check_repository_paths(root, tracked_paths)


def _is_allowed_git_identity(name: str, email: str) -> bool:
    """Allow generic identities and no-reply addresses, never personal email."""
    normalized = (name.strip().casefold(), email.strip().casefold())
    normalized_email = normalized[1]
    return (
        normalized in GENERIC_GIT_IDENTITIES
        or normalized_email == "noreply@github.com"
        or normalized_email.endswith("@users.noreply.github.com")
    )


def check_repository_history(root: Path) -> list[Finding]:
    """Reject personal email in authors and committers reachable from HEAD."""
    git_path = root / ".git"
    if not git_path.exists():
        return []

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "HEAD",
                "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x1e",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        return [
            Finding(
                "GIT_HISTORY",
                git_path,
                None,
                f"could not inspect Git history ({type(error).__name__})",
            )
        ]

    findings: list[Finding] = []
    for raw_record in completed.stdout.split(b"\x1e"):
        fields = raw_record.strip(b"\r\n").split(b"\0")
        if fields == [b""]:
            continue
        if len(fields) != 5:
            findings.append(
                Finding(
                    "GIT_HISTORY",
                    git_path,
                    None,
                    "invalid Git identity record",
                )
            )
            continue

        raw_commit, raw_author_name, raw_author_email, raw_committer_name, raw_committer_email = (
            fields
        )
        commit = raw_commit.decode("ascii", errors="replace")[:12]
        identities = (
            (
                "author",
                raw_author_name.decode("utf-8", errors="replace"),
                raw_author_email.decode("utf-8", errors="replace"),
            ),
            (
                "committer",
                raw_committer_name.decode("utf-8", errors="replace"),
                raw_committer_email.decode("utf-8", errors="replace"),
            ),
        )
        for role, name, email in identities:
            if not _is_allowed_git_identity(name, email):
                findings.append(
                    Finding(
                        "GIT_HISTORY_IDENTITY",
                        git_path,
                        None,
                        f"commit {commit}: {role} uses a prohibited public email address",
                    )
                )

    return findings


def _archive_member_is_public(name: str) -> bool:
    """Return whether an archive member name may belong to a public artifact."""
    member = PurePosixPath(name)
    parts = member.parts
    if member.is_absolute() or ".." in parts:
        return False
    relative = Path(*parts) if parts else Path()
    return not (
        is_sensitive_path(relative)
        or _is_local_config_name(relative)
        or any(part in ARCHIVE_LOCAL_DIRS for part in parts)
    )


def _archive_member_findings(archive: Path, names: list[str]) -> list[Finding]:
    """Validate member names without extracting the archive."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            findings.append(
                Finding("DIST_DUPLICATE_MEMBER", archive, None, f"duplicate member: {name}")
            )
            continue
        seen.add(name)

        member = PurePosixPath(name)
        parts = member.parts
        if member.is_absolute() or ".." in parts:
            findings.append(
                Finding("DIST_UNSAFE_PATH", archive, None, f"unsafe archive path: {name}")
            )
            continue
        relative = Path(*parts) if parts else Path()
        if is_sensitive_path(relative) or _is_local_config_name(relative):
            findings.append(
                Finding("DIST_LOCAL_PAYLOAD", archive, None, f"local file in archive: {name}")
            )
            continue
        if any(part in ARCHIVE_LOCAL_DIRS for part in parts):
            findings.append(
                Finding("DIST_LOCAL_PAYLOAD", archive, None, f"local directory in archive: {name}")
            )
    return findings


def _scan_text_content(path: Path, content: str, *, member: str | None = None) -> list[Finding]:
    """Find prohibited public content without reproducing matched values."""
    findings: list[Finding] = []
    prefix = "DIST_" if member is not None else ""
    for line_number, line in enumerate(content.splitlines(), start=1):
        if SYNTHETIC_FIXTURE_MARKER in line:
            continue

        location = f"member {member}:{line_number}: " if member is not None else ""
        finding_line = None if member is not None else line_number
        for code, pattern in CONTENT_PATTERNS.items():
            if pattern.search(line):
                findings.append(
                    Finding(
                        f"{prefix}{code}",
                        path,
                        finding_line,
                        f"{location}prohibited public pattern",
                    )
                )

        for email_match in EMAIL_PATTERN.finditer(line):
            if email_match.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
                findings.append(
                    Finding(
                        f"{prefix}EMAIL",
                        path,
                        finding_line,
                        f"{location}non-synthetic address",
                    )
                )
    return findings


def _scan_archive_text(archive: Path, member: str, data: bytes) -> list[Finding]:
    """Inspect a text member in memory without extracting the artifact."""
    relative = Path(*PurePosixPath(member).parts)
    if relative.suffix.lower() not in TEXT_SUFFIXES and relative.name not in TEXT_NAMES:
        return []
    if len(data) > MAX_ARCHIVE_TEXT_BYTES:
        return [
            Finding(
                "DIST_TEXT_TOO_LARGE",
                archive,
                None,
                f"text member exceeds {MAX_ARCHIVE_TEXT_BYTES} bytes: {member}",
            )
        ]
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return [
            Finding(
                "DIST_ENCODING",
                archive,
                None,
                f"text member is not UTF-8: {member}",
            )
        ]
    return _scan_text_content(archive, content, member=member)


def check_distribution_archives(root: Path, *, require_dist: bool = False) -> list[Finding]:
    """Inspect wheel and sdist members without extracting the packages."""
    dist_dir = root / "dist"
    if not dist_dir.exists():
        return (
            [Finding("DIST_MISSING", dist_dir, None, "dist directory is missing")]
            if require_dist
            else []
        )

    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if not wheels and not sdists:
        return (
            [Finding("DIST_MISSING", dist_dir, None, "wheel and sdist are missing")]
            if require_dist
            else []
        )

    findings: list[Finding] = []
    if require_dist and (not wheels or not sdists):
        missing = "wheel" if not wheels else "sdist"
        findings.append(Finding("DIST_INCOMPLETE", dist_dir, None, f"missing {missing}"))

    for archive in wheels:
        try:
            with zipfile.ZipFile(archive) as package:
                members = package.infolist()
                names = [member.filename for member in members]
                findings.extend(_archive_member_findings(archive, names))
                for member in members:
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        findings.append(
                            Finding(
                                "DIST_UNSAFE_MEMBER",
                                archive,
                                None,
                                f"symlink in archive: {member.filename}",
                            )
                        )
                        continue
                    if member.is_dir() or not _archive_member_is_public(member.filename):
                        continue
                    if member.file_size > MAX_ARCHIVE_TEXT_BYTES:
                        relative = Path(*PurePosixPath(member.filename).parts)
                        if relative.suffix.lower() in TEXT_SUFFIXES or relative.name in TEXT_NAMES:
                            findings.append(
                                Finding(
                                    "DIST_TEXT_TOO_LARGE",
                                    archive,
                                    None,
                                    "text member exceeds "
                                    f"{MAX_ARCHIVE_TEXT_BYTES} bytes: {member.filename}",
                                )
                            )
                        continue
                    findings.extend(
                        _scan_archive_text(archive, member.filename, package.read(member))
                    )
        except (OSError, zipfile.BadZipFile) as error:
            findings.append(
                Finding(
                    "DIST_INVALID",
                    archive,
                    None,
                    f"invalid wheel ({type(error).__name__})",
                )
            )
            continue

    for archive in sdists:
        try:
            with tarfile.open(archive, "r:gz") as package:
                members = package.getmembers()
                names = [member.name for member in members]
                findings.extend(_archive_member_findings(archive, names))
                for member in members:
                    if member.isdir() or not _archive_member_is_public(member.name):
                        continue
                    if not member.isfile():
                        findings.append(
                            Finding(
                                "DIST_UNSAFE_MEMBER",
                                archive,
                                None,
                                f"special member in archive: {member.name}",
                            )
                        )
                        continue
                    if member.size > MAX_ARCHIVE_TEXT_BYTES:
                        relative = Path(*PurePosixPath(member.name).parts)
                        if relative.suffix.lower() in TEXT_SUFFIXES or relative.name in TEXT_NAMES:
                            findings.append(
                                Finding(
                                    "DIST_TEXT_TOO_LARGE",
                                    archive,
                                    None,
                                    "text member exceeds "
                                    f"{MAX_ARCHIVE_TEXT_BYTES} bytes: {member.name}",
                                )
                            )
                        continue
                    extracted = package.extractfile(member)
                    if extracted is None:
                        findings.append(
                            Finding(
                                "DIST_INVALID",
                                archive,
                                None,
                                f"could not read archive member: {member.name}",
                            )
                        )
                        continue
                    findings.extend(_scan_archive_text(archive, member.name, extracted.read()))
        except (OSError, tarfile.TarError) as error:
            findings.append(
                Finding(
                    "DIST_INVALID",
                    archive,
                    None,
                    f"invalid sdist ({type(error).__name__})",
                )
            )
            continue

    return findings


def scan_content(paths: list[Path]) -> list[Finding]:
    """Find personal paths, placeholders, and tokens in public text files."""
    findings: list[Finding] = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("ENCODING", path, None, "public text is not UTF-8"))
            continue

        findings.extend(_scan_text_content(path, content))

        if path.suffix in {".md", ".sh"} and DESTRUCTIVE_COMMAND.search(content):
            findings.append(Finding("DESTRUCTIVE_DELETE", path, None, "permanent deletion command"))

    return findings


def _normalized_reference(label: str) -> str:
    """Apply the case-insensitive normalization used by Markdown references."""
    return " ".join(label.split()).casefold()


def _check_markdown_target(
    root: Path,
    path: Path,
    content: str,
    target: str,
    offset: int,
) -> Finding | None:
    """Validate a local target and preserve the declaration position."""
    target = target.strip().split()[0].strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    target_path = target.split("#", maxsplit=1)[0]
    if not target_path:
        return None
    resolved = (path.parent / target_path).resolve(strict=False)
    if resolved.is_relative_to(root.resolve()) and resolved.exists():
        return None
    line = content.count("\n", 0, offset) + 1
    return Finding("BROKEN_LINK", path, line, f"missing target: {target}")


def check_markdown_links(root: Path, paths: list[Path]) -> list[Finding]:
    """Check inline and reference links, including local targets."""
    findings: list[Finding] = []
    for path in (candidate for candidate in paths if candidate.suffix == ".md"):
        content = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(content):
            finding = _check_markdown_target(root, path, content, match.group(1), match.start())
            if finding is not None:
                findings.append(finding)

        definitions: dict[str, tuple[str, int]] = {}
        for match in MARKDOWN_REFERENCE_DEFINITION.finditer(content):
            label = _normalized_reference(match.group(1))
            target = match.group(2) or match.group(3)
            if label in definitions:
                line = content.count("\n", 0, match.start()) + 1
                findings.append(
                    Finding(
                        "DUPLICATE_REFERENCE",
                        path,
                        line,
                        f"duplicate Markdown reference: {label}",
                    )
                )
                continue
            definitions[label] = (target, match.start())
            finding = _check_markdown_target(root, path, content, target, match.start())
            if finding is not None:
                findings.append(finding)

        for match in MARKDOWN_REFERENCE_LINK.finditer(content):
            label = _normalized_reference(match.group(2) or match.group(1))
            if label in definitions:
                continue
            line = content.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    "BROKEN_REFERENCE",
                    path,
                    line,
                    f"missing Markdown reference: {label}",
                )
            )
    return findings


def discover_mcp(root: Path) -> tuple[list[str], list[str]]:
    """Discover MCP names and URIs directly from production decorators."""
    server_dir = root / "src" / "vault_search" / "server"
    tools: list[str] = []
    resources: list[str] = []
    for path in server_dir.glob("*.py"):
        content = path.read_text(encoding="utf-8")
        tools.extend(
            re.findall(
                r"@mcp\.tool\(\)\s*\n\s*(?:async\s+)?def\s+([A-Za-z_]\w*)",
                content,
            )
        )
        resources.extend(re.findall(r"@mcp\.resource\(\s*[\"']([^\"']+)[\"']", content))
    return sorted(tools), sorted(resources)


def count_mcp(root: Path) -> tuple[int, int]:
    """Count MCP registrations discovered in production code."""
    tools, resources = discover_mcp(root)
    return len(tools), len(resources)


def check_mcp_contract(root: Path) -> list[Finding]:
    """Compare code names, URIs, and counts with the public indexes."""
    tools, resources = discover_mcp(root)
    findings: list[Finding] = []
    if (len(tools), len(resources)) != (43, 6):
        findings.append(
            Finding(
                "MCP_COUNT",
                root / "src" / "vault_search" / "server",
                None,
                f"found {len(tools)} tools and {len(resources)} resources; expected 43 and 6",
            )
        )

    for label, values in (("tool", tools), ("resource", resources)):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            findings.append(
                Finding(
                    "MCP_DUPLICATE",
                    root / "src" / "vault_search" / "server",
                    None,
                    f"duplicate {label}(s): {', '.join(duplicates)}",
                )
            )

    for path in (root / "README.md", root / "docs" / "api" / "tools.md"):
        content = path.read_text(encoding="utf-8")
        if "43 tools" not in content or "6 resources" not in content:
            findings.append(Finding("MCP_DOC_COUNT", path, None, "canonical count not declared"))

    catalog_path = root / "docs" / "api" / "tools.md"
    catalog = catalog_path.read_text(encoding="utf-8")
    for tool_name in tools:
        if f"`{tool_name}`" not in catalog:
            findings.append(
                Finding("MCP_DOC_TOOL", catalog_path, None, f"missing tool: {tool_name}")
            )
    for resource_uri in resources:
        if f"`{resource_uri}`" not in catalog:
            findings.append(
                Finding("MCP_DOC_RESOURCE", catalog_path, None, f"missing resource: {resource_uri}")
            )
    return findings


def _check_public_document_contracts(path: Path, content: str) -> list[Finding]:
    """Reject removed endpoints and internal entry points in one public document."""
    findings: list[Finding] = []
    if "/shutdown" in content:
        findings.append(
            Finding(
                "REMOVED_DAEMON_ENDPOINT",
                path,
                None,
                "the removed /shutdown endpoint must not appear in public documentation",
            )
        )
    if re.search(r"python\s+-m\s+vault_search\.(?:server\.mcp|daemon\.server)", content):
        findings.append(
            Finding(
                "INTERNAL_ENTRYPOINT",
                path,
                None,
                "use python -m vault_search [daemon] in public documentation",
            )
        )
    return findings


def check_public_contracts(root: Path) -> list[Finding]:
    """Keep critical contracts aligned across code, examples, and documentation."""
    findings: list[Finding] = []
    daemon_source = (root / "src" / "vault_search" / "daemon" / "server.py").read_text(
        encoding="utf-8"
    )
    daemon_docs_path = root / "docs" / "daemon-setup.md"
    daemon_docs = daemon_docs_path.read_text(encoding="utf-8")
    runtime_endpoints = set(re.findall(r'self\.path == ["\'](/[^"\']+)["\']', daemon_source))
    documented_endpoints = set(re.findall(r"`(/[^`\s]+)`", daemon_docs))

    for endpoint in sorted(runtime_endpoints - documented_endpoints):
        findings.append(
            Finding("DAEMON_DOC_ENDPOINT", daemon_docs_path, None, f"missing endpoint: {endpoint}")
        )
    for endpoint in sorted(documented_endpoints - runtime_endpoints):
        findings.append(
            Finding(
                "DAEMON_DOC_ENDPOINT",
                daemon_docs_path,
                None,
                f"unregistered endpoint: {endpoint}",
            )
        )

    required_fragments = {
        root / "README.md": (
            "Snapshot of up to 5,000 notes",
            "Remote daemon access is unsupported",
            "--cov-fail-under=65",
            "uv run mypy src/vault_search",
        ),
        root / "CONTRIBUTING.md": (
            "--cov-fail-under=65",
            "uv run mypy src/vault_search",
        ),
        root / "docs" / "api" / "tools-indexing.md": (
            "It does not parse files",
            "does not create an ANN index",
        ),
        root / "docs" / "api" / "tools-navigation.md": (
            '"returned_notes"',
            '"has_more"',
        ),
        root / "docs" / "features" / "link-index.md": (
            "`returned_notes`",
            "`has_more`",
        ),
        root / "docs" / "api" / "tools-resources.md": (
            "first 5,000",
            "Use `list_notes` for pagination or filtering",
            "`returned`",
            "`has_more`",
        ),
        root / "docs" / "api" / "tools-graph.md": (
            "iterative Tarjan",
            '"separated_branches"',
            '"returned_notes"',
        ),
        root / "docs" / "features" / "ai-enrichment.md": (
            "job accepts at most 1,000",
            "`queue_full`",
            "200 jobs",
            "`returned`",
            "`truncated`",
        ),
        root / "docs" / "performance" / "indexing.md": (
            "counts files in the current scan",
            "does not estimate duration",
        ),
        root / "docs" / "development" / "testing.md": (
            "--cov-fail-under=65",
            "uv run mypy src/vault_search",
        ),
        root / "docs" / "config" / "variables.md": (
            "`VAULT_PATH`",
            "`VAULT_SEARCH_DATA_DIR`",
            "`VAULT_SEARCH_DB_DIR` is not recognized",
        ),
        daemon_docs_path: (
            "HTTP 503",
            "TLS, authentication, quotas",
        ),
    }
    for path, fragments in required_fragments.items():
        content = path.read_text(encoding="utf-8")
        for fragment in fragments:
            if fragment not in content:
                findings.append(
                    Finding("PUBLIC_CONTRACT", path, None, f"missing declaration: {fragment}")
                )

    config_example = (root / "config.example.yaml").read_text(encoding="utf-8")
    if "allow_remote:" in config_example:
        findings.append(
            Finding(
                "UNSUPPORTED_CONFIG",
                root / "config.example.yaml",
                None,
                "allow_remote is not part of the public schema",
            )
        )
    if 'host: "127.0.0.1"' not in config_example:
        findings.append(
            Finding(
                "UNSAFE_DAEMON_EXAMPLE",
                root / "config.example.yaml",
                None,
                "the example must keep an explicit loopback host",
            )
        )

    public_docs = [root / "README.md", *(root / "docs").rglob("*.md")]
    for path in public_docs:
        content = path.read_text(encoding="utf-8")
        findings.extend(_check_public_document_contracts(path, content))

    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="project root",
    )
    parser.add_argument(
        "--require-dist",
        action="store_true",
        help="require and inspect at least one wheel and one sdist",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    public_files, findings = walk_public_files(root)
    findings.extend(scan_content(public_files))
    findings.extend(check_markdown_links(root, public_files))
    findings.extend(check_repository_inventory(root))
    findings.extend(check_repository_history(root))
    findings.extend(check_mcp_contract(root))
    findings.extend(check_public_contracts(root))
    findings.extend(check_distribution_archives(root, require_dist=args.require_dist))

    unique_findings = sorted(
        set(findings), key=lambda item: (str(item.path), item.line or 0, item.code)
    )
    if unique_findings:
        for finding in unique_findings:
            print(finding.render(root), file=sys.stderr)
        print(f"publication check: {len(unique_findings)} problem(s)", file=sys.stderr)
        return 1

    tools, resources = count_mcp(root)
    print(
        f"publication check: ok; {len(public_files)} text files; {tools} tools; {resources} resources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
