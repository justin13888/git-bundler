# Testing Strategy

## Test Suite Overview

| Layer       | File                          | What it tests                                  | # Tests |
| ----------- | ----------------------------- | ---------------------------------------------- | ------- |
| Unit        | `tests/test_git_bundle.py`    | All functions and classes with mocked I/O      | 42      |
| Integration | `tests/integration_test.py`   | Full archive→unpack→verify cycle on real repos | 11      |
| Fixture     | `tests/generate_test_repo.py` | Helper: creates a repo with LFS + submodules   | —       |
| Config      | `tests/conftest.py`           | Path setup for imports                         | —       |

## Unit Tests (`test_git_bundle.py`)

All tests use `pytest` with `pytest-mock`. No filesystem or network access (except `TestWriteManifest` which uses `tmp_path`).

### `run_command`
- Success, failure (raises `GitBundlerError`), failure with `ignore_errors=True`, `capture_output=False`
- Verbose logging verification, failure with empty stderr

### `check_dependency`
- Tool present, tool missing

### `parse_git_config`
- Submodule URLs, paths, empty config, no matching section, malformed lines
- Values containing `=` (URL query params), multiple dots in name

### `GitArchiver`
- `_extract_repo_name()`: HTTPS with `.git`, without suffix, trailing slash, local path, bare name, SSH URLs
- `_resolve_relative_url()`: absolute URL, `../` relative, `./` relative
- `archive()`: gz flow, zstd flow (verifies correct `tar` flags)
- `_write_manifest()`: JSON content verification (source_url, repo_name, version, timestamp)
- `_handle_submodules()`: no `.gitmodules`, with entries (clone + LFS), relative URL resolution

### `GitUnpacker`
- Happy path: tar extraction → clone → remote set-url
- Error paths: missing archive, missing manifest, missing `repo.git`
- `_restore_submodules()`: no `.gitmodules`, no source dir, happy path (init → config → update)

### `GitVerifier`
- Calls `git fsck --full`
- Calls `git lfs fsck` when LFS directory is present
- Calls `git submodule status` when `.gitmodules` present

## Integration Tests (`integration_test.py`)

Uses `pytest` with a session-scoped fixture that generates a test repo (with LFS, submodules, branches, and tags) once.

- `test_archive_unpack_roundtrip[gz]` / `[zstd]`: full cycle for each compression
- `test_archive_with_verify`: `--verify` flag
- `test_standalone_verify`: `verify` subcommand
- `test_submodule_content_preserved`: file content check after restore
- `test_lfs_content_preserved`: 1MB file size check after restore
- `test_branch_preservation`: feature branch survives roundtrip
- `test_tag_preservation`: annotated tag survives roundtrip
- `test_simple_repo_roundtrip`: minimal repo with no LFS/submodules
- `test_corrupted_archive`: truncated archive fails with error
- `test_unpack_nonexistent_archive`: error message check

## Running

```bash
# All tests
uv run pytest -v

# With coverage
uv run pytest -v --cov=git_bundle --cov-report=term-missing

# Unit tests only
uv run pytest tests/test_git_bundle.py -v

# Integration tests only
uv run pytest tests/integration_test.py -v
```
