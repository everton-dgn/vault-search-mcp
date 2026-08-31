"""Focused tests for the tag-driven release pipeline."""

from __future__ import annotations

import importlib.util
import io
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


def _load_release_module():
    script_path = Path(__file__).parent.parent / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("vault_search_release", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release_module()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _init_repository(path: Path) -> None:
    _git(path.parent, "init", "--initial-branch=main", str(path))
    _git(path, "config", "user.name", "Release Test")
    _git(path, "config", "user.email", "release-test@vault-search.invalid")


def _commit(repo: Path, subject: str, body: str | None = None) -> str:
    tracked = repo / "tracked.txt"
    previous = tracked.read_text(encoding="utf-8") if tracked.exists() else ""
    tracked.write_text(f"{previous}{subject}\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    command = ["commit", "--message", subject]
    if body is not None:
        command.extend(("--message", body))
    _git(repo, *command)
    return _git(repo, "rev-parse", "HEAD")


def _write_artifacts(directory: Path, version: str, *, variant: bytes = b"") -> None:
    directory.mkdir()
    metadata = f"Metadata-Version: 2.4\nName: vault-search-mcp\nVersion: {version}\n\n"
    wheel = directory / f"vault_search_mcp-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"vault_search_mcp-{version}.dist-info/METADATA", metadata)
        archive.writestr("vault_search/__init__.py", variant)

    root = f"vault_search_mcp-{version}"
    sdist = directory / f"vault_search_mcp-{version}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        content = metadata.encode()
        info = tarfile.TarInfo(f"{root}/PKG-INFO")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
        payload = tarfile.TarInfo(f"{root}/src/vault_search/__init__.py")
        payload.size = len(variant)
        archive.addfile(payload, io.BytesIO(variant))


@pytest.mark.parametrize(
    "tag",
    ["v0.1.0", "v1.0.0", "v12.345.678"],
)
def test_strict_release_tags_are_accepted(tag: str):
    version = release.parse_tag(tag)

    assert version is not None
    assert version.tag == tag


@pytest.mark.parametrize(
    "tag",
    ["1.2.3", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2", "v1.2.3-rc.1", "release-v1.2.3"],
)
def test_non_release_tags_are_rejected(tag: str):
    assert release.parse_tag(tag) is None


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        ("feat(search): add graph ranking", "", "minor"),
        ("fix: preserve an exact match", "", "patch"),
        ("perf(index): reuse parsed metadata", "", "patch"),
        ("feat!: replace the query contract", "", "major"),
        ("chore: update tooling", "BREAKING CHANGE: requires Python 3.14", "major"),
        ("docs: clarify local operation", "", "none"),
        ("unstructured commit", "", "none"),
    ],
)
def test_conventional_commits_map_to_semver(subject: str, body: str, expected: str):
    assert release.classify_commit(subject, body) == expected


def test_first_feature_release_starts_at_v0_1_0(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    source_commit = _commit(repo, "feat: publish the MCP server")

    plan = release.plan_release(repo, source_commit)

    assert plan.release is True
    assert plan.tag == "v0.1.0"
    assert plan.version == "0.1.0"
    assert plan.bump == "minor"
    assert plan.recovery is False


def test_highest_conventional_change_wins_since_latest_tag(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    first = _commit(repo, "feat: initial feature")
    _git(repo, "tag", "--annotate", "--message", "Release v1.2.3", "v1.2.3", first)
    _commit(repo, "fix: correct an edge case")
    source_commit = _commit(repo, "feat(parser): accept another format")

    plan = release.plan_release(repo, source_commit)

    assert plan.tag == "v1.3.0"
    assert plan.previous_tag == "v1.2.3"
    assert plan.bump == "minor"
    assert plan.commit_count == 2


def test_existing_head_tag_enters_recovery_mode(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    _git(repo, "tag", "--annotate", "--message", "Release v0.1.0", "v0.1.0")

    plan = release.plan_release(repo, source_commit)

    assert plan.release is True
    assert plan.tag == "v0.1.0"
    assert plan.recovery is True
    assert plan.bump == "none"


def test_tagged_release_can_be_recovered_after_main_advances(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    _git(repo, "tag", "--annotate", "--message", "Release v0.1.0", "v0.1.0")
    _commit(repo, "fix: advance main after the interrupted release")

    plan = release.plan_release(repo, source_commit)

    assert plan.release is True
    assert plan.tag == "v0.1.0"
    assert plan.source_commit == source_commit
    assert plan.recovery is True


def test_release_planning_rejects_a_divergent_semver_tag(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    unrelated_commit = _git(repo, "commit-tree", tree, "-m", "feat: unrelated history")
    _git(
        repo,
        "tag",
        "--annotate",
        "--message",
        "Release v9.0.0",
        "v9.0.0",
        unrelated_commit,
    )

    with pytest.raises(release.ReleaseError, match="outside the source commit history"):
        release.plan_release(repo, source_commit)


def test_release_planning_rejects_a_lightweight_semver_tag(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    _git(repo, "tag", "v0.1.0")

    with pytest.raises(release.ReleaseError, match="not annotated"):
        release.plan_release(repo, source_commit)


def test_non_releasing_commits_do_not_create_a_tag(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repository(repo)
    source_commit = _commit(repo, "docs: add an operations note")

    plan = release.plan_release(repo, source_commit)

    assert plan.release is False
    assert plan.tag is None
    assert plan.bump == "none"


def test_workflow_context_rejects_a_fork():
    with pytest.raises(release.ReleaseError, match="source repository"):
        release.validate_workflow_context(
            repository="example/vault-search-mcp",
            event_repository="example/vault-search-mcp",
            head_repository="fork/vault-search-mcp",
            source_workflow="CI",
            expected_workflow="CI",
            source_event="push",
            conclusion="success",
            head_branch="main",
            default_branch="main",
            source_commit="a" * 40,
            release_run_sha="a" * 40,
        )


def test_workflow_context_rejects_a_release_identity_for_another_commit():
    with pytest.raises(release.ReleaseError, match="identity does not match"):
        release.validate_workflow_context(
            repository="example/vault-search-mcp",
            event_repository="example/vault-search-mcp",
            head_repository="example/vault-search-mcp",
            source_workflow="CI",
            expected_workflow="CI",
            source_event="push",
            conclusion="success",
            head_branch="main",
            default_branch="main",
            source_commit="a" * 40,
            release_run_sha="b" * 40,
        )


def test_api_run_record_must_match_the_triggering_event():
    source_commit = "a" * 40
    record = {
        "id": 123,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "head_branch": "main",
        "head_sha": source_commit,
        "repository": {"full_name": "example/vault-search-mcp"},
        "head_repository": {"full_name": "example/vault-search-mcp"},
    }

    release.validate_run_record(
        record,
        run_id=123,
        repository="example/vault-search-mcp",
        source_workflow="CI",
        expected_workflow_path=".github/workflows/ci.yml",
        source_event="push",
        conclusion="success",
        head_branch="main",
        source_commit=source_commit,
    )

    record["head_sha"] = "b" * 40
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.validate_run_record(
            record,
            run_id=123,
            repository="example/vault-search-mcp",
            source_workflow="CI",
            expected_workflow_path=".github/workflows/ci.yml",
            source_event="push",
            conclusion="success",
            head_branch="main",
            source_commit=source_commit,
        )


def test_checkout_validation_allows_recovery_after_main_advances(tmp_path: Path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main")
    _commit(repo, "fix: advance the trusted branch")
    _git(repo, "push", "origin", "main")
    _git(repo, "remote", "set-url", "origin", "https://github.com/example/vault-search-mcp.git")

    release.validate_checkout(
        repo,
        repository="example/vault-search-mcp",
        remote="origin",
        default_branch="main",
        source_commit=source_commit,
        expected_checkout="default-branch",
    )

    _git(repo, "checkout", "--detach", source_commit)
    release.validate_checkout(
        repo,
        repository="example/vault-search-mcp",
        remote="origin",
        default_branch="main",
        source_commit=source_commit,
        expected_checkout="source-commit",
    )


def test_ensure_annotated_tag_is_idempotent(tmp_path: Path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main")

    release.ensure_annotated_tag(repo, remote="origin", tag="v0.1.0", source_commit=source_commit)
    release.ensure_annotated_tag(repo, remote="origin", tag="v0.1.0", source_commit=source_commit)

    assert _git(repo, "cat-file", "-t", "v0.1.0") == "tag"
    assert _git(repo, "rev-list", "-n", "1", "v0.1.0") == source_commit


def test_prepare_tag_is_local_annotated_and_idempotent(tmp_path: Path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", str(remote))
    _init_repository(repo)
    source_commit = _commit(repo, "feat: initial feature")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "--set-upstream", "origin", "main")

    release.ensure_local_annotated_tag(repo, tag="v0.1.0", source_commit=source_commit)
    release.ensure_local_annotated_tag(repo, tag="v0.1.0", source_commit=source_commit)

    assert _git(repo, "cat-file", "-t", "v0.1.0") == "tag"
    assert _git(repo, "rev-list", "-n", "1", "v0.1.0") == source_commit
    assert _git(repo, "ls-remote", "--tags", "origin", "refs/tags/v0.1.0") == ""


def test_finalize_release_requires_reproducible_artifacts(tmp_path: Path):
    primary = tmp_path / "primary"
    rebuilt = tmp_path / "rebuilt"
    _write_artifacts(primary, "1.2.3")
    _write_artifacts(rebuilt, "1.2.3", variant=b"different")

    with pytest.raises(release.ReleaseError, match="reproduce"):
        release.finalize_release(
            primary=primary,
            rebuilt=rebuilt,
            output=tmp_path / "release",
            version="1.2.3",
            tag="v1.2.3",
            source_commit="a" * 40,
        )


def test_finalize_and_verify_complete_release_bundle(tmp_path: Path):
    primary = tmp_path / "primary"
    rebuilt = tmp_path / "rebuilt"
    output = tmp_path / "release"
    _write_artifacts(primary, "1.2.3")
    rebuilt.mkdir()
    for artifact in primary.iterdir():
        shutil.copyfile(artifact, rebuilt / artifact.name)

    release.finalize_release(
        primary=primary,
        rebuilt=rebuilt,
        output=output,
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )
    release.verify_release(
        output,
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )

    assert {path.name for path in output.iterdir()} == {
        "vault_search_mcp-1.2.3-py3-none-any.whl",
        "vault_search_mcp-1.2.3.tar.gz",
        "SHA256SUMS",
        "release-manifest.json",
    }

    existing = tmp_path / "existing-draft"
    existing.mkdir()
    existing_asset = "vault_search_mcp-1.2.3-py3-none-any.whl"
    shutil.copyfile(output / existing_asset, existing / existing_asset)

    missing = release.reconcile_draft_assets(
        output,
        existing,
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )

    assert existing_asset not in missing
    assert set(missing) == {path.name for path in output.iterdir()} - {existing_asset}


def test_select_build_candidate_requires_identical_run_attempts(tmp_path: Path):
    candidates = tmp_path / "build-candidates"
    first = candidates / "release-primary-v1.2.3-1"
    second = candidates / "release-primary-v1.2.3-2"
    candidates.mkdir()
    _write_artifacts(first, "1.2.3")
    _write_artifacts(second, "1.2.3")

    selected = tmp_path / "selected-build"
    release.select_build_candidate(
        candidates,
        selected,
        prefix="release-primary-v1.2.3-",
        version="1.2.3",
    )

    assert release.inspect_build(selected, "1.2.3") == release.inspect_build(first, "1.2.3")

    divergent = tmp_path / "divergent-candidates"
    divergent.mkdir()
    _write_artifacts(divergent / "release-primary-v1.2.3-1", "1.2.3")
    _write_artifacts(
        divergent / "release-primary-v1.2.3-2",
        "1.2.3",
        variant=b"different",
    )
    with pytest.raises(release.ReleaseError, match="differ between workflow run attempts"):
        release.select_build_candidate(
            divergent,
            tmp_path / "rejected-build",
            prefix="release-primary-v1.2.3-",
            version="1.2.3",
        )


def test_select_build_candidate_accepts_single_download_layout(tmp_path: Path):
    candidates = tmp_path / "single-build-candidate"
    _write_artifacts(candidates, "1.2.3")

    selected = tmp_path / "selected-single-build"
    release.select_build_candidate(
        candidates,
        selected,
        prefix="release-primary-v1.2.3-",
        version="1.2.3",
    )

    assert release.inspect_build(selected, "1.2.3") == release.inspect_build(candidates, "1.2.3")


def test_select_release_candidate_verifies_every_run_attempt(tmp_path: Path):
    primary = tmp_path / "primary"
    rebuilt = tmp_path / "rebuilt"
    bundle = tmp_path / "bundle"
    _write_artifacts(primary, "1.2.3")
    rebuilt.mkdir()
    for artifact in primary.iterdir():
        shutil.copyfile(artifact, rebuilt / artifact.name)
    release.finalize_release(
        primary=primary,
        rebuilt=rebuilt,
        output=bundle,
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )

    candidates = tmp_path / "release-candidates"
    candidates.mkdir()
    for attempt in (1, 2):
        candidate = candidates / f"release-bundle-v1.2.3-{attempt}"
        candidate.mkdir()
        for artifact in bundle.iterdir():
            shutil.copyfile(artifact, candidate / artifact.name)

    selected = tmp_path / "selected-release"
    release.select_release_candidate(
        candidates,
        selected,
        prefix="release-bundle-v1.2.3-",
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )

    release.compare_releases(
        bundle,
        selected,
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )

    direct_candidate = tmp_path / "single-release-candidate"
    direct_candidate.mkdir()
    for artifact in bundle.iterdir():
        shutil.copyfile(artifact, direct_candidate / artifact.name)
    selected_direct = tmp_path / "selected-single-release"
    release.select_release_candidate(
        direct_candidate,
        selected_direct,
        prefix="release-bundle-v1.2.3-",
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )
    release.compare_releases(
        bundle,
        selected_direct,
        version="1.2.3",
        tag="v1.2.3",
        source_commit="a" * 40,
    )


def test_release_workflow_has_pinned_actions_and_no_manual_or_pypi_trigger():
    workflow = (
        Path(__file__).parent.parent / ".github" / "workflows" / "auto-release.yml"
    ).read_text(encoding="utf-8")
    action_refs = re.findall(r"^\s*uses:\s*[^@\s]+@([^\s]+)", workflow, re.MULTILINE)

    assert "workflow_run:" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "permissions: {}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "pypi" not in workflow.lower()
    assert "ref: ${{ github.event.workflow_run.head_sha }}" not in workflow
    assert "ref: refs/heads/main" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "RELEASE_RUN_SHA: ${{ github.sha }}" in workflow
    assert '--release-run-sha "${RELEASE_RUN_SHA}"' in workflow
    assert "ATTESTATION_SOURCE_SHA: ${{ needs.plan.outputs.source_commit }}" in workflow
    assert "ref: refs/tags/${{ needs.plan.outputs.tag }}" in workflow
    assert "--json assets,isDraft,isPrerelease" in workflow
    assert "--prerelease=false" in workflow
    assert workflow.count("github.run_attempt") == 3
    assert "overwrite:" not in workflow
    assert "pattern: release-primary-${{ needs.plan.outputs.tag }}-*" in workflow
    assert "pattern: release-rebuild-${{ needs.plan.outputs.tag }}-*" in workflow
    assert workflow.count("pattern: release-bundle-${{ needs.plan.outputs.tag }}-*") == 2
    assert "scripts/release.py select-build" in workflow
    assert workflow.count("scripts/release.py select-release") == 2
    assert workflow.count("scripts/release.py prepare-tag") == 2
    assert workflow.index("\n  assemble:") < workflow.index("\n  tag:")
    assert workflow.index("\n  tag:") < workflow.index("\n  attest:")
    build_section = workflow[workflow.index("\n  build:") : workflow.index("\n  rebuild:")]
    rebuild_section = workflow[workflow.index("\n  rebuild:") : workflow.index("\n  assemble:")]
    tag_section = workflow[workflow.index("\n  tag:") : workflow.index("\n  attest:")]
    attest_section = workflow[workflow.index("\n  attest:") : workflow.index("\n  publish:")]
    assert "needs: plan" in build_section
    assert "needs: plan" in rebuild_section
    assert "- tag" not in build_section
    assert "- tag" not in rebuild_section
    assert "- assemble" in tag_section
    assert "- tag" in attest_section
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in action_refs)
