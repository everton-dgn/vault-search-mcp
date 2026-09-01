#!/usr/bin/env python3
"""Plan, build, and verify deterministic GitHub release artifacts.

The module intentionally uses only the Python standard library so the release
workflow can validate its context before installing project dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from email.parser import Parser
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

PROJECT_NAME = "vault-search-mcp"
NORMALIZED_PROJECT_NAME = "vault_search_mcp"
STRICT_TAG_PATTERN = (
    r"^v(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)$"
)
STRICT_TAG_RE = re.compile(STRICT_TAG_PATTERN)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
CANDIDATE_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*-$")
CONVENTIONAL_HEADER_RE = re.compile(
    r"^(?P<type>[a-z][a-z0-9-]*)(?:\([^()\r\n]+\))?(?P<breaking>!)?: (?P<summary>\S.*)$"
)
BREAKING_FOOTER_RE = re.compile(r"^BREAKING(?: CHANGE|-CHANGE):\s+\S", re.MULTILINE)
WHEEL_RE_TEMPLATE = r"^vault_search_mcp-{version}-py3-none-any\.whl$"
SDIST_RE_TEMPLATE = r"^vault_search_mcp-{version}\.tar\.gz$"

Bump = Literal["none", "patch", "minor", "major"]
_BUMP_RANK: dict[Bump, int] = {"none": 0, "patch": 1, "minor": 2, "major": 3}


class ReleaseError(RuntimeError):
    """Raised when a release invariant is not satisfied."""


@dataclass(frozen=True, order=True)
class Version:
    """A strict SemVer release version without prerelease or build metadata."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self}"

    def bump(self, kind: Bump) -> Version:
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        if kind == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        raise ReleaseError("Cannot create a release without a semantic version bump")


@dataclass(frozen=True)
class ReleasePlan:
    """Serializable release decision emitted to GitHub Actions."""

    release: bool
    source_commit: str
    tag: str | None
    version: str | None
    previous_tag: str | None
    bump: Bump
    recovery: bool
    commit_count: int


@dataclass(frozen=True)
class Artifact:
    """Verified artifact metadata stored in the release manifest."""

    name: str
    sha256: str
    size: int


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        operation = args[0] if args else "command"
        raise ReleaseError(f"git {operation} failed with exit code {result.returncode}")
    return result


def parse_tag(tag: str) -> Version | None:
    """Parse an exact ``vX.Y.Z`` tag, rejecting leading zeros and suffixes."""
    match = STRICT_TAG_RE.fullmatch(tag)
    if match is None:
        return None
    return Version(*(int(match.group(name)) for name in ("major", "minor", "patch")))


def require_tag(tag: str) -> Version:
    version = parse_tag(tag)
    if version is None:
        raise ReleaseError(f"Invalid release tag: {tag!r}; expected vX.Y.Z")
    return version


def require_sha(value: str) -> str:
    normalized = value.lower()
    if SHA_RE.fullmatch(normalized) is None:
        raise ReleaseError("Source commit must be a complete 40-character SHA")
    return normalized


def _max_bump(left: Bump, right: Bump) -> Bump:
    return left if _BUMP_RANK[left] >= _BUMP_RANK[right] else right


def classify_commit(subject: str, body: str = "") -> Bump:
    """Map a Conventional Commit to its SemVer effect."""
    match = CONVENTIONAL_HEADER_RE.fullmatch(subject)
    if (match is not None and match.group("breaking")) or BREAKING_FOOTER_RE.search(body):
        return "major"
    if match is None:
        return "none"
    commit_type = match.group("type")
    if commit_type == "feat":
        return "minor"
    if commit_type in {"fix", "perf", "revert"}:
        return "patch"
    return "none"


def classify_commits(commits: Sequence[tuple[str, str]]) -> Bump:
    bump: Bump = "none"
    for subject, body in commits:
        bump = _max_bump(bump, classify_commit(subject, body))
    return bump


def _tagged_commit(repo: Path, tag: str) -> str:
    result = _run_git(repo, "rev-list", "-n", "1", tag)
    commit = result.stdout.strip().lower()
    return require_sha(commit)


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = _run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode not in {0, 1}:
        raise ReleaseError("Unable to validate Git commit ancestry")
    return result.returncode == 0


def _all_release_tags(repo: Path) -> list[tuple[Version, str, str]]:
    result = _run_git(repo, "tag", "--list")
    tags: list[tuple[Version, str, str]] = []
    for tag in result.stdout.splitlines():
        version = parse_tag(tag)
        if version is not None:
            object_type = _run_git(repo, "cat-file", "-t", f"refs/tags/{tag}").stdout.strip()
            if object_type != "tag":
                raise ReleaseError(f"Release tag {tag} is not annotated")
            tags.append((version, tag, _tagged_commit(repo, tag)))
    return sorted(tags)


def _validate_release_history(repo: Path, tags: Sequence[tuple[Version, str, str]]) -> None:
    for previous, current in zip(tags, tags[1:], strict=False):
        if previous[2] == current[2]:
            raise ReleaseError("Multiple release versions point to the same commit")
        if not _is_ancestor(repo, previous[2], current[2]):
            raise ReleaseError("Release tags do not form one monotonic Git history")


def _commits_in_range(
    repo: Path, source_commit: str, previous_tag: str | None
) -> list[tuple[str, str]]:
    revision = f"{previous_tag}..{source_commit}" if previous_tag else source_commit
    result = _run_git(repo, "rev-list", "--reverse", revision)
    commits: list[tuple[str, str]] = []
    for commit in result.stdout.splitlines():
        require_sha(commit)
        message = _run_git(repo, "show", "-s", "--format=%s%x00%b", commit).stdout
        subject, separator, body = message.partition("\0")
        if separator != "\0":
            raise ReleaseError("Git returned an invalid commit message record")
        commits.append((subject.rstrip("\n"), body.rstrip("\n")))
    return commits


def plan_release(repo: Path, source_commit: str) -> ReleasePlan:
    """Calculate the next release from strict tags and Conventional Commits."""
    source_commit = require_sha(source_commit)
    _run_git(repo, "cat-file", "-e", f"{source_commit}^{{commit}}")

    all_tags = _all_release_tags(repo)
    _validate_release_history(repo, all_tags)
    reachable = [item for item in all_tags if _is_ancestor(repo, item[2], source_commit)]
    head_tags = [item for item in reachable if item[2] == source_commit]
    if len(head_tags) > 1:
        rendered = ", ".join(item[1] for item in head_tags)
        raise ReleaseError(f"Source commit has multiple release tags: {rendered}")

    if head_tags:
        current_version, current_tag, _ = head_tags[0]
        if reachable[-1][0] != current_version:
            raise ReleaseError("Source commit would recover an older release version")
        return ReleasePlan(
            release=True,
            source_commit=source_commit,
            tag=current_tag,
            version=str(current_version),
            previous_tag=reachable[-2][1] if len(reachable) > 1 else None,
            bump="none",
            recovery=True,
            commit_count=0,
        )

    if len(reachable) != len(all_tags):
        raise ReleaseError("A release tag exists outside the source commit history")

    previous_version, previous_tag = (
        (reachable[-1][0], reachable[-1][1]) if reachable else (Version(0, 0, 0), None)
    )
    commits = _commits_in_range(repo, source_commit, previous_tag)
    bump = classify_commits(commits)
    if bump == "none":
        return ReleasePlan(
            release=False,
            source_commit=source_commit,
            tag=None,
            version=None,
            previous_tag=previous_tag,
            bump=bump,
            recovery=False,
            commit_count=len(commits),
        )

    next_version = previous_version.bump(bump)
    tag = next_version.tag
    collision = _run_git(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}", check=False)
    if collision.returncode == 0:
        raise ReleaseError(f"Release tag {tag} already exists outside the source commit")
    if collision.returncode not in {0, 1}:
        raise ReleaseError("Unable to inspect existing release tags")

    return ReleasePlan(
        release=True,
        source_commit=source_commit,
        tag=tag,
        version=str(next_version),
        previous_tag=previous_tag,
        bump=bump,
        recovery=False,
        commit_count=len(commits),
    )


def _github_repository_from_remote(remote_url: str) -> str | None:
    candidate = remote_url.strip()
    https = urlparse(candidate)
    if https.scheme in {"http", "https", "ssh"} and https.hostname == "github.com":
        path = https.path.lstrip("/")
    else:
        ssh_match = re.fullmatch(r"git@github\.com:(?P<path>[^\s]+)", candidate)
        if ssh_match is None:
            return None
        path = ssh_match.group("path")
    if path.endswith(".git"):
        path = path[:-4]
    return path if REPOSITORY_RE.fullmatch(path) else None


def validate_workflow_context(
    *,
    repository: str,
    event_repository: str,
    head_repository: str,
    source_workflow: str,
    expected_workflow: str,
    source_event: str,
    conclusion: str,
    head_branch: str,
    default_branch: str,
    source_commit: str,
    release_run_sha: str,
) -> str:
    """Reject any event that is not a successful same-repository main CI run."""
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise ReleaseError("Invalid GitHub repository identifier")
    if event_repository.casefold() != repository.casefold():
        raise ReleaseError("Workflow event repository does not match the current repository")
    if head_repository.casefold() != repository.casefold():
        raise ReleaseError("CI source repository does not match the current repository")
    if source_workflow != expected_workflow:
        raise ReleaseError("Release was not triggered by the expected CI workflow")
    if source_event != "push":
        raise ReleaseError("Only CI runs triggered by push can create releases")
    if conclusion != "success":
        raise ReleaseError("Only successful CI runs can create releases")
    if BRANCH_RE.fullmatch(default_branch) is None or head_branch != default_branch:
        raise ReleaseError("Only the default branch can create releases")
    source_commit = require_sha(source_commit)
    if require_sha(release_run_sha) != source_commit:
        raise ReleaseError(
            "The release workflow identity does not match the CI-approved source commit"
        )
    return source_commit


def validate_run_record(
    record: object,
    *,
    run_id: int,
    repository: str,
    source_workflow: str,
    expected_workflow_path: str,
    source_event: str,
    conclusion: str,
    head_branch: str,
    source_commit: str,
) -> None:
    """Cross-check the event payload against a fresh GitHub Actions API record."""
    if run_id < 1 or not isinstance(record, dict):
        raise ReleaseError("Invalid GitHub Actions run record")

    def nested_string(key: str, child: str) -> str | None:
        value = record.get(key)
        return (
            value.get(child)
            if isinstance(value, dict) and isinstance(value.get(child), str)
            else None
        )

    expected = {
        "id": run_id,
        "name": source_workflow,
        "path": expected_workflow_path,
        "status": "completed",
        "conclusion": conclusion,
        "event": source_event,
        "head_branch": head_branch,
        "head_sha": require_sha(source_commit),
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise ReleaseError("GitHub Actions API record does not match the triggering event")
    if (
        nested_string("repository", "full_name") is None
        or (nested_string("repository", "full_name") or "").casefold() != repository.casefold()
    ):
        raise ReleaseError("GitHub Actions API repository does not match the triggering event")
    if (
        nested_string("head_repository", "full_name") is None
        or (nested_string("head_repository", "full_name") or "").casefold() != repository.casefold()
    ):
        raise ReleaseError("GitHub Actions API source repository is not trusted")


def validate_checkout(
    repo: Path,
    *,
    repository: str,
    remote: str,
    default_branch: str,
    source_commit: str,
    expected_checkout: Literal["default-branch", "source-commit"] = "source-commit",
) -> None:
    """Bind a validated event SHA to trusted main and the expected checkout."""
    if REMOTE_RE.fullmatch(remote) is None:
        raise ReleaseError("Invalid Git remote name")
    if BRANCH_RE.fullmatch(default_branch) is None:
        raise ReleaseError("Invalid default branch name")
    source_commit = require_sha(source_commit)

    origin_url = _run_git(repo, "remote", "get-url", remote).stdout.strip()
    origin_repository = _github_repository_from_remote(origin_url)
    if origin_repository is None or origin_repository.casefold() != repository.casefold():
        raise ReleaseError("Git remote does not match the GitHub event repository")

    remote_ref = f"refs/remotes/{remote}/{default_branch}"
    remote_head = require_sha(
        _run_git(repo, "rev-parse", f"{remote_ref}^{{commit}}").stdout.strip()
    )
    if not _is_ancestor(repo, source_commit, remote_head):
        raise ReleaseError("Successful CI commit is not reachable from the default branch")

    head = require_sha(_run_git(repo, "rev-parse", "HEAD^{commit}").stdout.strip())
    expected_head = remote_head if expected_checkout == "default-branch" else source_commit
    if head != expected_head:
        raise ReleaseError("Checked-out commit does not match the expected trusted ref")


def _remote_tag(repo: Path, remote: str, tag: str) -> tuple[str | None, str | None]:
    result = _run_git(
        repo,
        "ls-remote",
        "--tags",
        remote,
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    object_sha: str | None = None
    peeled_sha: str | None = None
    for line in result.stdout.splitlines():
        sha, separator, ref = line.partition("\t")
        if not separator:
            raise ReleaseError("Git returned an invalid remote tag record")
        sha = require_sha(sha)
        if ref == f"refs/tags/{tag}":
            object_sha = sha
        elif ref == f"refs/tags/{tag}^{{}}":
            peeled_sha = sha
    return object_sha, peeled_sha


def _validate_local_annotated_tag(repo: Path, tag: str, source_commit: str) -> bool:
    exists = _run_git(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}", check=False)
    if exists.returncode == 1:
        return False
    if exists.returncode != 0:
        raise ReleaseError("Unable to inspect the local release tag")
    object_type = _run_git(repo, "cat-file", "-t", tag).stdout.strip()
    if object_type != "tag":
        raise ReleaseError(f"Existing release tag {tag} is not annotated")
    if _tagged_commit(repo, tag) != source_commit:
        raise ReleaseError(f"Existing release tag {tag} points to another commit")
    return True


def ensure_local_annotated_tag(repo: Path, *, tag: str, source_commit: str) -> None:
    """Create or validate an annotated tag locally without mutating a remote."""
    require_tag(tag)
    source_commit = require_sha(source_commit)
    if _validate_local_annotated_tag(repo, tag, source_commit):
        return
    _run_git(
        repo,
        "-c",
        "user.name=github-actions[bot]",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "tag",
        "--annotate",
        "--message",
        f"Release {tag}",
        tag,
        source_commit,
    )
    _validate_local_annotated_tag(repo, tag, source_commit)


def ensure_annotated_tag(repo: Path, *, remote: str, tag: str, source_commit: str) -> None:
    """Create an annotated release tag or validate an identical remote tag."""
    require_tag(tag)
    source_commit = require_sha(source_commit)
    if REMOTE_RE.fullmatch(remote) is None:
        raise ReleaseError("Invalid Git remote name")

    object_sha, peeled_sha = _remote_tag(repo, remote, tag)
    if object_sha is not None:
        if peeled_sha is None:
            raise ReleaseError(f"Remote release tag {tag} is not annotated")
        if peeled_sha != source_commit:
            raise ReleaseError(f"Remote release tag {tag} points to another commit")
        return
    if peeled_sha is not None:
        raise ReleaseError("Remote returned a peeled tag without its tag object")

    ensure_local_annotated_tag(repo, tag=tag, source_commit=source_commit)

    _run_git(repo, "push", remote, f"refs/tags/{tag}:refs/tags/{tag}")
    _, peeled_sha = _remote_tag(repo, remote, tag)
    if peeled_sha != source_commit:
        raise ReleaseError("Remote release tag verification failed after push")


def _canonical_project_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _artifact_patterns(version: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    parsed = require_tag(f"v{version}")
    escaped = re.escape(str(parsed))
    return (
        re.compile(WHEEL_RE_TEMPLATE.format(version=escaped)),
        re.compile(SDIST_RE_TEMPLATE.format(version=escaped)),
    )


def _parse_package_metadata(text: str, *, expected_version: str) -> None:
    metadata = Parser().parsestr(text)
    if _canonical_project_name(metadata.get("Name", "")) != PROJECT_NAME:
        raise ReleaseError("Artifact package name does not match the release project")
    if metadata.get("Version") != expected_version:
        raise ReleaseError("Artifact package version does not match the release tag")


def _verify_wheel(path: Path, version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise ReleaseError("Wheel must contain exactly one METADATA file")
            metadata = archive.read(metadata_files[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as error:
        raise ReleaseError(f"Invalid wheel artifact: {path.name}") from error
    _parse_package_metadata(metadata, expected_version=version)


def _verify_sdist(path: Path, version: str) -> None:
    expected_root = f"{NORMALIZED_PROJECT_NAME}-{version}"
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ReleaseError("Source archive is empty")
            roots = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
            if roots != {expected_root}:
                raise ReleaseError("Source archive has an unexpected root directory")
            metadata_members = [
                member
                for member in members
                if member.name == f"{expected_root}/PKG-INFO" and member.isfile()
            ]
            if len(metadata_members) != 1:
                raise ReleaseError("Source archive must contain exactly one root PKG-INFO")
            extracted = archive.extractfile(metadata_members[0])
            if extracted is None:
                raise ReleaseError("Unable to read source archive metadata")
            metadata = extracted.read().decode("utf-8")
    except (OSError, UnicodeDecodeError, tarfile.TarError) as error:
        raise ReleaseError(f"Invalid source artifact: {path.name}") from error
    _parse_package_metadata(metadata, expected_version=version)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_build(
    directory: Path, version: str, *, allow_release_metadata: bool = False
) -> dict[str, Artifact]:
    """Validate the expected wheel and sdist, returning stable metadata."""
    wheel_pattern, sdist_pattern = _artifact_patterns(version)
    if not directory.is_dir():
        raise ReleaseError(f"Build directory does not exist: {directory}")

    files = sorted(directory.iterdir(), key=lambda item: item.name)
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ReleaseError("Build directory contains a non-regular artifact")
    wheels = [path for path in files if wheel_pattern.fullmatch(path.name)]
    sdists = [path for path in files if sdist_pattern.fullmatch(path.name)]
    package_files = [*wheels, *sdists]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseError("Build directory must contain one expected wheel and one expected sdist")
    if not allow_release_metadata and len(files) != len(package_files):
        raise ReleaseError("Build directory contains an unexpected file")

    _verify_wheel(wheels[0], version)
    _verify_sdist(sdists[0], version)
    return {
        path.name: Artifact(name=path.name, sha256=_sha256(path), size=path.stat().st_size)
        for path in package_files
    }


def _write_idempotent(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ReleaseError(f"Refusing to replace a different release file: {path.name}")
        return
    path.write_bytes(content)


def finalize_release(
    *,
    primary: Path,
    rebuilt: Path,
    output: Path,
    version: str,
    tag: str,
    source_commit: str,
) -> None:
    """Require byte-for-byte rebuilds and write the final release bundle."""
    parsed_version = require_tag(tag)
    if str(parsed_version) != version:
        raise ReleaseError("Release version and tag disagree")
    source_commit = require_sha(source_commit)
    primary_artifacts = inspect_build(primary, version)
    rebuilt_artifacts = inspect_build(rebuilt, version)
    if primary_artifacts != rebuilt_artifacts:
        raise ReleaseError("Independent rebuild did not reproduce the release artifacts")

    output.mkdir(parents=True, exist_ok=True)
    for name in sorted(primary_artifacts):
        source = primary / name
        target = output / name
        if target.exists():
            if target.is_symlink() or not target.is_file() or _sha256(target) != _sha256(source):
                raise ReleaseError(f"Refusing to replace a different release artifact: {name}")
        else:
            shutil.copyfile(source, target)

    artifacts = [asdict(primary_artifacts[name]) for name in sorted(primary_artifacts)]
    manifest = {
        "artifacts": artifacts,
        "project": PROJECT_NAME,
        "schema_version": 1,
        "source_commit": source_commit,
        "tag": tag,
        "version": version,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    checksums = "".join(
        f"{primary_artifacts[name].sha256} *{name}\n" for name in sorted(primary_artifacts)
    ).encode()
    _write_idempotent(output / "release-manifest.json", manifest_bytes)
    _write_idempotent(output / "SHA256SUMS", checksums)
    verify_release(output, version=version, tag=tag, source_commit=source_commit)


def verify_release(
    directory: Path,
    *,
    version: str,
    tag: str,
    source_commit: str,
) -> None:
    """Verify a complete release bundle after any storage boundary."""
    parsed_version = require_tag(tag)
    if str(parsed_version) != version:
        raise ReleaseError("Release version and tag disagree")
    source_commit = require_sha(source_commit)
    manifest_path = directory / "release-manifest.json"
    checksums_path = directory / "SHA256SUMS"
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, checksums_path)):
        raise ReleaseError("Release manifest or checksum file is missing")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError("Release manifest is not valid UTF-8 JSON") from error
    expected_header = {
        "project": PROJECT_NAME,
        "schema_version": 1,
        "source_commit": source_commit,
        "tag": tag,
        "version": version,
    }
    if not isinstance(manifest, dict) or any(
        manifest.get(key) != value for key, value in expected_header.items()
    ):
        raise ReleaseError("Release manifest identity does not match the expected release")
    if set(manifest) != {*expected_header, "artifacts"}:
        raise ReleaseError("Release manifest contains an unsupported field")

    artifact_records = manifest.get("artifacts")
    if not isinstance(artifact_records, list) or len(artifact_records) != 2:
        raise ReleaseError("Release manifest must describe exactly two package artifacts")
    artifacts: dict[str, Artifact] = {}
    for record in artifact_records:
        if not isinstance(record, dict) or set(record) != {"name", "sha256", "size"}:
            raise ReleaseError("Release manifest contains an invalid artifact record")
        name, digest, size = record["name"], record["sha256"], record["size"]
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
        ):
            raise ReleaseError("Release manifest contains unsafe artifact metadata")
        if name in artifacts:
            raise ReleaseError("Release manifest contains a duplicate artifact")
        artifacts[name] = Artifact(name=name, sha256=digest, size=size)

    inspected = inspect_build(directory, version, allow_release_metadata=True)
    if artifacts != inspected:
        raise ReleaseError("Release artifact digest or size does not match the manifest")

    expected_checksums = "".join(
        f"{artifacts[name].sha256} *{name}\n" for name in sorted(artifacts)
    )
    try:
        actual_checksums = checksums_path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseError("SHA256SUMS must be an ASCII text file") from error
    if actual_checksums != expected_checksums:
        raise ReleaseError("SHA256SUMS does not match the release manifest")

    expected_files = {*artifacts, "release-manifest.json", "SHA256SUMS"}
    actual_files = {path.name for path in directory.iterdir()}
    if actual_files != expected_files:
        raise ReleaseError("Release directory contains an unexpected file")


def compare_releases(
    expected: Path,
    actual: Path,
    *,
    version: str,
    tag: str,
    source_commit: str,
) -> None:
    """Prove that an already-published release is byte-for-byte identical."""
    verify_release(expected, version=version, tag=tag, source_commit=source_commit)
    verify_release(actual, version=version, tag=tag, source_commit=source_commit)
    expected_names = sorted(path.name for path in expected.iterdir())
    for name in expected_names:
        if _sha256(expected / name) != _sha256(actual / name):
            raise ReleaseError(f"Published release asset differs from rebuilt asset: {name}")


def reconcile_draft_assets(
    expected: Path,
    existing: Path,
    *,
    version: str,
    tag: str,
    source_commit: str,
) -> list[str]:
    """Return missing draft assets while rejecting replacements and unexpected files."""
    verify_release(expected, version=version, tag=tag, source_commit=source_commit)
    if not existing.is_dir():
        raise ReleaseError("Existing draft asset directory is missing")
    expected_files = {path.name: path for path in expected.iterdir()}
    existing_files = {path.name: path for path in existing.iterdir()}
    if any(path.is_symlink() or not path.is_file() for path in existing_files.values()):
        raise ReleaseError("Existing draft contains a non-regular asset")
    unexpected = existing_files.keys() - expected_files.keys()
    if unexpected:
        raise ReleaseError("Existing draft contains an unexpected asset")
    for name, path in existing_files.items():
        if _sha256(path) != _sha256(expected_files[name]):
            raise ReleaseError(f"Existing draft asset differs from rebuilt asset: {name}")
    return sorted(expected_files.keys() - existing_files.keys())


def _release_attempt_directories(candidates: Path, prefix: str) -> list[tuple[int, Path]]:
    if CANDIDATE_PREFIX_RE.fullmatch(prefix) is None:
        raise ReleaseError("Invalid release artifact prefix")
    if not candidates.is_dir() or candidates.is_symlink():
        raise ReleaseError("Release artifact candidate directory is missing")
    entries = list(candidates.iterdir())
    if not entries:
        raise ReleaseError("No release artifact candidate was downloaded")
    if all(path.is_file() and not path.is_symlink() for path in entries):
        return [(0, candidates)]
    pattern = re.compile(rf"{re.escape(prefix)}(?P<attempt>[1-9][0-9]*)")
    attempts: list[tuple[int, Path]] = []
    for path in entries:
        match = pattern.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_dir():
            raise ReleaseError("Release artifact candidates contain an unexpected entry")
        attempts.append((int(match.group("attempt")), path))
    return sorted(attempts)


def _copy_candidate(source: Path, output: Path, expected_names: set[str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    actual_names = {path.name for path in output.iterdir()}
    if not actual_names <= expected_names:
        raise ReleaseError("Release artifact output contains an unexpected entry")
    for name in sorted(expected_names):
        source_file = source / name
        target_file = output / name
        if target_file.exists():
            if (
                target_file.is_symlink()
                or not target_file.is_file()
                or _sha256(target_file) != _sha256(source_file)
            ):
                raise ReleaseError(f"Refusing to replace a different release file: {name}")
        else:
            shutil.copyfile(source_file, target_file)


def select_build_candidate(candidates: Path, output: Path, *, prefix: str, version: str) -> None:
    """Select the newest identical build from all available run attempts."""
    attempts = _release_attempt_directories(candidates, prefix)
    baseline: dict[str, Artifact] | None = None
    for _, directory in attempts:
        artifacts = inspect_build(directory, version)
        if baseline is None:
            baseline = artifacts
        elif artifacts != baseline:
            raise ReleaseError("Build artifacts differ between workflow run attempts")
    if baseline is None:  # pragma: no cover - guarded by _release_attempt_directories
        raise ReleaseError("No build artifact candidate was validated")
    _copy_candidate(attempts[-1][1], output, set(baseline))
    if inspect_build(output, version) != baseline:
        raise ReleaseError("Selected build artifact verification failed")


def select_release_candidate(
    candidates: Path,
    output: Path,
    *,
    prefix: str,
    version: str,
    tag: str,
    source_commit: str,
) -> None:
    """Select the newest identical release bundle from all run attempts."""
    attempts = _release_attempt_directories(candidates, prefix)
    baseline: dict[str, Artifact] | None = None
    for _, directory in attempts:
        verify_release(directory, version=version, tag=tag, source_commit=source_commit)
        artifacts = {
            path.name: Artifact(path.name, _sha256(path), path.stat().st_size)
            for path in directory.iterdir()
        }
        if baseline is None:
            baseline = artifacts
        elif artifacts != baseline:
            raise ReleaseError("Release bundles differ between workflow run attempts")
    if baseline is None:  # pragma: no cover - guarded by _release_attempt_directories
        raise ReleaseError("No release bundle candidate was validated")
    _copy_candidate(attempts[-1][1], output, set(baseline))
    verify_release(output, version=version, tag=tag, source_commit=source_commit)


def _write_github_output(path: Path, plan: ReleasePlan) -> None:
    values = {
        "release": str(plan.release).lower(),
        "tag": plan.tag or "",
        "version": plan.version or "",
        "source_commit": plan.source_commit,
        "previous_tag": plan.previous_tag or "",
        "bump": plan.bump,
        "recovery": str(plan.recovery).lower(),
    }
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def _command_plan(args: argparse.Namespace) -> None:
    source_commit = validate_workflow_context(
        repository=args.repository,
        event_repository=args.event_repository,
        head_repository=args.head_repository,
        source_workflow=args.source_workflow,
        expected_workflow=args.expected_workflow,
        source_event=args.source_event,
        conclusion=args.conclusion,
        head_branch=args.head_branch,
        default_branch=args.default_branch,
        source_commit=args.source_commit,
        release_run_sha=args.release_run_sha,
    )
    try:
        raw_record = (
            sys.stdin.read() if args.run_record == "-" else Path(args.run_record).read_text()
        )
        run_record = json.loads(raw_record)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError("Unable to read the GitHub Actions API run record") from error
    validate_run_record(
        run_record,
        run_id=args.run_id,
        repository=args.repository,
        source_workflow=args.source_workflow,
        expected_workflow_path=args.expected_workflow_path,
        source_event=args.source_event,
        conclusion=args.conclusion,
        head_branch=args.head_branch,
        source_commit=source_commit,
    )
    validate_checkout(
        args.repository_root,
        repository=args.repository,
        remote=args.remote,
        default_branch=args.default_branch,
        source_commit=source_commit,
        expected_checkout="default-branch",
    )
    plan = plan_release(args.repository_root, source_commit)
    if args.github_output is not None:
        _write_github_output(args.github_output, plan)
    print(json.dumps(asdict(plan), sort_keys=True))


def _command_ensure_tag(args: argparse.Namespace) -> None:
    validate_checkout(
        args.repository_root,
        repository=args.repository,
        remote=args.remote,
        default_branch=args.default_branch,
        source_commit=args.source_commit,
    )
    ensure_annotated_tag(
        args.repository_root,
        remote=args.remote,
        tag=args.tag,
        source_commit=args.source_commit,
    )
    print(f"Verified annotated tag {args.tag} at {args.source_commit}")


def _command_prepare_tag(args: argparse.Namespace) -> None:
    source_commit = require_sha(args.source_commit)
    checked_out_commit = require_sha(
        _run_git(args.repository_root, "rev-parse", "HEAD^{commit}").stdout.strip()
    )
    if checked_out_commit != source_commit:
        raise ReleaseError("Checked-out commit does not match the local release tag target")
    ensure_local_annotated_tag(
        args.repository_root,
        tag=args.tag,
        source_commit=source_commit,
    )
    print(f"Prepared local annotated tag {args.tag} at {source_commit}")


def _command_verify_tag(args: argparse.Namespace) -> None:
    require_tag(args.tag)
    source_commit = require_sha(args.source_commit)
    if REMOTE_RE.fullmatch(args.remote) is None:
        raise ReleaseError("Invalid Git remote name")
    _, peeled_sha = _remote_tag(args.repository_root, args.remote, args.tag)
    if peeled_sha != source_commit:
        raise ReleaseError("Remote annotated tag does not match the release commit")
    print(f"Verified remote annotated tag {args.tag} at {source_commit}")


def _command_inspect(args: argparse.Namespace) -> None:
    artifacts = inspect_build(args.directory, args.version)
    print(json.dumps({name: asdict(value) for name, value in artifacts.items()}, sort_keys=True))


def _command_finalize(args: argparse.Namespace) -> None:
    finalize_release(
        primary=args.primary,
        rebuilt=args.rebuilt,
        output=args.output,
        version=args.version,
        tag=args.tag,
        source_commit=args.source_commit,
    )
    print(f"Verified reproducible release bundle for {args.tag}")


def _command_verify(args: argparse.Namespace) -> None:
    verify_release(
        args.directory,
        version=args.version,
        tag=args.tag,
        source_commit=args.source_commit,
    )
    print(f"Verified release bundle for {args.tag}")


def _command_compare(args: argparse.Namespace) -> None:
    compare_releases(
        args.expected,
        args.actual,
        version=args.version,
        tag=args.tag,
        source_commit=args.source_commit,
    )
    print(f"Published assets are identical for {args.tag}")


def _command_reconcile(args: argparse.Namespace) -> None:
    missing = reconcile_draft_assets(
        args.expected,
        args.existing,
        version=args.version,
        tag=args.tag,
        source_commit=args.source_commit,
    )
    _write_idempotent(args.missing_file, "".join(f"{name}\n" for name in missing).encode())
    print(f"Verified existing draft assets; {len(missing)} asset(s) remain")


def _command_select_build(args: argparse.Namespace) -> None:
    select_build_candidate(
        args.candidates,
        args.output,
        prefix=args.prefix,
        version=args.version,
    )
    print("Selected identical build artifacts from the latest available run attempt")


def _command_select_release(args: argparse.Namespace) -> None:
    select_release_candidate(
        args.candidates,
        args.output,
        prefix=args.prefix,
        version=args.version,
        tag=args.tag,
        source_commit=args.source_commit,
    )
    print("Selected an identical release bundle from the latest available run attempt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="validate CI context and calculate a release")
    plan.add_argument("--repository", required=True)
    plan.add_argument("--event-repository", required=True)
    plan.add_argument("--head-repository", required=True)
    plan.add_argument("--source-workflow", required=True)
    plan.add_argument("--expected-workflow", default="CI")
    plan.add_argument("--expected-workflow-path", default=".github/workflows/ci.yml")
    plan.add_argument("--source-event", required=True)
    plan.add_argument("--conclusion", required=True)
    plan.add_argument("--head-branch", required=True)
    plan.add_argument("--default-branch", default="main")
    plan.add_argument("--source-commit", required=True)
    plan.add_argument("--release-run-sha", required=True)
    plan.add_argument("--run-id", type=int, required=True)
    plan.add_argument("--run-record", required=True)
    plan.add_argument("--remote", default="origin")
    plan.add_argument("--github-output", type=Path)
    plan.set_defaults(handler=_command_plan)

    prepare_tag = subparsers.add_parser(
        "prepare-tag", help="create or verify an annotated tag without pushing it"
    )
    prepare_tag.add_argument("--tag", required=True)
    prepare_tag.add_argument("--source-commit", required=True)
    prepare_tag.set_defaults(handler=_command_prepare_tag)

    ensure_tag = subparsers.add_parser(
        "ensure-tag", help="create or verify an annotated release tag"
    )
    ensure_tag.add_argument("--remote", default="origin")
    ensure_tag.add_argument("--repository", required=True)
    ensure_tag.add_argument("--default-branch", default="main")
    ensure_tag.add_argument("--tag", required=True)
    ensure_tag.add_argument("--source-commit", required=True)
    ensure_tag.set_defaults(handler=_command_ensure_tag)

    verify_tag = subparsers.add_parser(
        "verify-tag", help="verify an existing annotated tag on the remote"
    )
    verify_tag.add_argument("--remote", default="origin")
    verify_tag.add_argument("--tag", required=True)
    verify_tag.add_argument("--source-commit", required=True)
    verify_tag.set_defaults(handler=_command_verify_tag)

    inspect = subparsers.add_parser("inspect", help="inspect one wheel and source archive")
    inspect.add_argument("--directory", type=Path, required=True)
    inspect.add_argument("--version", required=True)
    inspect.set_defaults(handler=_command_inspect)

    finalize = subparsers.add_parser(
        "finalize", help="compare rebuilds and create a release bundle"
    )
    finalize.add_argument("--primary", type=Path, required=True)
    finalize.add_argument("--rebuilt", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.add_argument("--version", required=True)
    finalize.add_argument("--tag", required=True)
    finalize.add_argument("--source-commit", required=True)
    finalize.set_defaults(handler=_command_finalize)

    verify = subparsers.add_parser("verify", help="verify a complete release bundle")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--version", required=True)
    verify.add_argument("--tag", required=True)
    verify.add_argument("--source-commit", required=True)
    verify.set_defaults(handler=_command_verify)

    compare = subparsers.add_parser(
        "compare", help="compare downloaded release assets with a local bundle"
    )
    compare.add_argument("--expected", type=Path, required=True)
    compare.add_argument("--actual", type=Path, required=True)
    compare.add_argument("--version", required=True)
    compare.add_argument("--tag", required=True)
    compare.add_argument("--source-commit", required=True)
    compare.set_defaults(handler=_command_compare)

    reconcile = subparsers.add_parser(
        "reconcile-draft", help="validate draft assets and list only missing files"
    )
    reconcile.add_argument("--expected", type=Path, required=True)
    reconcile.add_argument("--existing", type=Path, required=True)
    reconcile.add_argument("--missing-file", type=Path, required=True)
    reconcile.add_argument("--version", required=True)
    reconcile.add_argument("--tag", required=True)
    reconcile.add_argument("--source-commit", required=True)
    reconcile.set_defaults(handler=_command_reconcile)

    select_build = subparsers.add_parser(
        "select-build", help="select identical build artifacts across run attempts"
    )
    select_build.add_argument("--candidates", type=Path, required=True)
    select_build.add_argument("--output", type=Path, required=True)
    select_build.add_argument("--prefix", required=True)
    select_build.add_argument("--version", required=True)
    select_build.set_defaults(handler=_command_select_build)

    select_release = subparsers.add_parser(
        "select-release", help="select identical release bundles across run attempts"
    )
    select_release.add_argument("--candidates", type=Path, required=True)
    select_release.add_argument("--output", type=Path, required=True)
    select_release.add_argument("--prefix", required=True)
    select_release.add_argument("--version", required=True)
    select_release.add_argument("--tag", required=True)
    select_release.add_argument("--source-commit", required=True)
    select_release.set_defaults(handler=_command_select_release)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except ReleaseError as error:
        print(f"release error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
