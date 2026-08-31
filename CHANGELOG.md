# Changelog

This file records changes that affect users and contributors. It follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

History from before public-release preparation is intentionally absent from
this repository. No earlier release date or version has been reconstructed by
assumption.

## [Unreleased]

### Added

- Dynamic package versions derived from strict `vX.Y.Z` Git tags.
- Automated, tag-based GitHub releases with checksums and provenance evidence.
- CI for static analysis, non-model tests, package builds, and publication
  auditing.
- ShellCheck coverage for daemon installers and uninstallers.
- A 65% minimum coverage gate and package-wide type checking.
- Security policy, contribution guide, support guide, and code of conduct.
- Canonical configuration in `config.example.yaml`.
- A benchmark protocol that separates measured results from targets.
- A dedicated visual identity for the repository landing page.
- `vault-search-config`, which validates YAML without printing resolved values
  or local paths.

### Changed

- The runtime, examples, tests, configuration, and documentation now use
  English as the project language.
- README and documentation now follow executable contracts in the code.
- Daemon removal scripts move artifacts to the operating-system trash.
- Documentation reflects the current registry of 43 tools and 6 resources.
- Daemon health distinguishes `ready` from unavailable states with HTTP 503;
  the HTTP shutdown endpoint has been removed.
- `vault://notes` exposes snapshot size, limit, and `has_more`.
- Graph density follows the conventional definition and articulation points use
  Tarjan's algorithm.
- The enrichment queue bounds jobs, paths, history, and result snapshots;
  shutdown stops accepting work and attempts to drain pending jobs.
- CRUD mutations share path locks, bounded timeouts, and revision checks before
  replacing or moving notes.
- Daemon responses are capped at 64 MiB before JSON decoding.
- FTS uses language-neutral tokenization by default; language-specific stemming
  is opt-in.
- External enrichment has no provider-specific model defaults. Model identifiers
  become required only when the feature is enabled.
- The publication gate checks the Git tree plus member names, text content, and
  file types inside wheel and sdist archives.
- BGE-M3 dense embeddings use `SentenceTransformer`. The default lock selects
  CPU PyTorch and keeps CUDA variants an explicit operator choice.

### Fixed

- Release tags are published only after independent builds, reproducibility
  checks, and artifact validation succeed. Attestations are bound to the exact
  commit approved by CI, and privileged publication checks out a fully qualified
  tag ref.
- Daemon startup now exits with a failure when its port is occupied, and
  installers verify both readiness and the managed process ID before reporting
  success. Terminal warmup failures now exit for supervisor retry, installed
  services preserve an explicit configuration allowlist, IPv6 loopback uses the
  correct socket family, and clients bypass proxies and reject redirects.
- Language-neutral FTS now explicitly disables the backend's English stemming
  and stop-word defaults.
- Portuguese stopwords and boolean aliases remain supported as input while the
  public interface and documentation use English.
- Configuration rejects contradictory search, pagination, and navigation limits
  before runtime startup.
- `folder_tree` defaults to `navigation.folder_tree_max_depth`.
- `.git` remains ignored when no YAML file is loaded.
- `IVF_PQ` rejects a `num_sub_vectors` value incompatible with the embedding
  dimension before indexing starts.
- Multiprocess locking tolerates one transient internal-directory replacement
  without weakening symlink validation.

### Removed

- A legacy configuration example that no longer matched the Pydantic schema.
- An unused attack-embedding generator with no runtime corpus or constants.
- Reserved security fields that never enforced a quota, timeout, or truncation.

### Security

- Local configuration, indexes, logs, and vault data are explicitly ignored by
  Git.
- CI uses minimal permissions and actions pinned to commits.
- Configuration, daemon client, and daemon server reject non-loopback hosts.
- Multiprocess locks use opaque files inside the vault on systems with `fcntl`;
  other platforms retain in-process thread coordination.
- Nested trash destinations reject symlinks that escape the vault.
- Startup sanitizes textual `SystemExit` messages.
- Configuration is validated before the MCP runtime or daemon loads.
- Runtime dependencies have been moved to maintained versions, including
  FastMCP 3 and Transformers 5; the default environment no longer includes the
  unpatched transitive packages `diskcache` and `lupa`.
