# Release process

Releases are derived from Git tags. Package versions come from strict `vX.Y.Z`
tags and are never edited manually in source files.

## Automated path

1. CI completes successfully for a commit on `main`.
2. The release workflow validates repository, branch, source workflow, SHA, and
   the OIDC workflow identity used by attestations.
3. Conventional Commits since the latest release determine the next SemVer
   increment.
4. Two isolated jobs prepare the same annotated tag locally without publishing
   it, then build the wheel and sdist independently.
5. Digests, package versions, and archive contents must match the
   reproducibility policy.
6. Only after those gates pass does the workflow publish or recover the remote
   annotated tag on the validated commit.
7. Checksums, manifest, source-bound attestations, and release notes are attached
   to a GitHub Release.

If `main` advances before an older CI run finishes, that stale release run
stops before creating a remote tag. The next successful CI run evaluates the
complete current commit range.

After a release passes that identity gate, later commits on `main` do not
invalidate the in-flight release. Tag publication still requires the validated
source commit to remain an ancestor of `main`, which lets an interrupted run
recover without switching to a different commit.

No PyPI publication occurs in the current release contract.

## Pre-release gates

### Code and contracts

- [ ] All commits since the prior tag follow Conventional Commits.
- [ ] Breaking MCP changes are explicit in the changelog.
- [ ] New configuration has a safe default and canonical example.
- [ ] Index migration or rebuild behavior has a recovery path.

### Security and privacy

- [ ] `scripts/check_publication.py` succeeds.
- [ ] The diff contains no vault, index, log, local configuration, or personal path.
- [ ] Reachable history contains no personal author or committer email.
- [ ] Dependencies and pinned actions have been reviewed.
- [ ] New trust boundaries appear in the threat model.
- [ ] Coordinated private reports are ready for disclosure.

### Validation

- [ ] Ruff check and format pass.
- [ ] Package-wide mypy passes.
- [ ] Non-model tests and the coverage threshold pass.
- [ ] Applicable slow tests record environment and result.
- [ ] `uv.lock` matches the declared build backend and dependencies.
- [ ] `uv build` succeeds from a clean checkout.
- [ ] `scripts/check_publication.py --require-dist` validates wheel and sdist.
- [ ] Release-specific unit and workflow-contract tests pass.

### Documentation

- [ ] README works from a clean clone.
- [ ] Tool and resource counts match decorators.
- [ ] Configuration references match Pydantic models.
- [ ] Changelog separates added, changed, fixed, and security work.
- [ ] Numeric benchmarks include protocol, manifest, and raw observations.

## Remote repository settings

Before the first release:

- [ ] Protect `main` with required CI checks.
- [ ] Protect `v*` tags against update and deletion while allowing workflow creation.
- [ ] Enable immutable releases.
- [ ] Keep workflow permissions read-only by default and elevate only the release job.

## Verification after publication

- [ ] Tag points to the CI-validated commit.
- [ ] Release is public and no longer a draft.
- [ ] Wheel and sdist versions match the tag.
- [ ] Published SHA-256 checksums match downloaded assets.
- [ ] Attestations verify against the public repository and workflow identity.
- [ ] A clean install can import the package and print the release version.
- [ ] Rollback and support policy for the release are clear.
