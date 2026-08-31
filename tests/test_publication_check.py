"""Regressões do gate que protege a superfície pública do repositório."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path


def _load_publication_module():
    script_path = Path(__file__).parent.parent / "scripts" / "check_publication.py"
    spec = importlib.util.spec_from_file_location("vault_search_publication_check", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publication = _load_publication_module()


def _write_sdist(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_repository_inventory_rejects_tracked_local_payloads(tmp_path: Path):
    tracked = [
        Path("config.yaml"),
        Path("data/index.json"),
        Path("vaults/private-note.md"),
        Path("dist/package.whl"),
        Path(".github/ISSUE_TEMPLATE/config.yml"),
        Path("vaults/.gitkeep"),
        Path("config.example.yaml"),
    ]

    findings = publication.check_repository_paths(tmp_path, tracked)

    assert {finding.code for finding in findings} == {
        "TRACKED_BUILD_ARTIFACT",
        "TRACKED_LOCAL_CONFIG",
        "TRACKED_LOCAL_DATA",
    }


def test_distribution_check_requires_built_artifacts_when_requested(tmp_path: Path):
    findings = publication.check_distribution_archives(tmp_path, require_dist=True)

    assert [finding.code for finding in findings] == ["DIST_MISSING"]


def test_distribution_check_rejects_local_payload_member(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "example-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example/__init__.py", "")
        archive.writestr("data/private.json", "{}")

    findings = publication.check_distribution_archives(tmp_path, require_dist=True)

    assert any(finding.code == "DIST_LOCAL_PAYLOAD" for finding in findings)


def test_distribution_check_rejects_local_path_inside_text_member(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "example-0.1.0-py3-none-any.whl"
    local_path = "/" + "Users/example/Documents/private-vault"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example/__init__.py", f'VAULT = "{local_path}"\n')

    findings = publication.check_distribution_archives(tmp_path)

    assert any(finding.code == "DIST_LOCAL_PATH" for finding in findings)


def test_distribution_check_rejects_symbolic_link_member(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "example-0.1.0-py3-none-any.whl"
    link = zipfile.ZipInfo("example/link.py")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(link, "target.py")

    findings = publication.check_distribution_archives(tmp_path)

    assert any(finding.code == "DIST_UNSAFE_MEMBER" for finding in findings)


def test_distribution_check_accepts_regular_wheel_and_sdist(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "example-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example/__init__.py", "")
        archive.writestr("example-0.1.0.dist-info/METADATA", "Name: example\n")

    sdist = dist / "example-0.1.0.tar.gz"
    _write_sdist(
        sdist,
        {
            "example-0.1.0/pyproject.toml": b"[project]\nname = 'example'\n",
            "example-0.1.0/src/example/__init__.py": b"",
        },
    )

    assert publication.check_distribution_archives(tmp_path, require_dist=True) == []


def test_markdown_check_rejects_undefined_reference(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("Leia [o guia][missing].\n", encoding="utf-8")

    findings = publication.check_markdown_links(tmp_path, [readme])

    assert any(finding.code == "BROKEN_REFERENCE" for finding in findings)


def test_markdown_check_rejects_missing_reference_target(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "Leia [o guia][guide].\n\n[guide]: docs/missing.md\n",
        encoding="utf-8",
    )

    findings = publication.check_markdown_links(tmp_path, [readme])

    assert any(finding.code == "BROKEN_LINK" for finding in findings)
