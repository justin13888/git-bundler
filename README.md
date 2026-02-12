# Git Bundler

`git_bundle.py` is a Python script to create complete Git archives for archival, including all branches, tags, LFS objects, and submodules. (This was mostly vibecoded so feel free to validate yourself.)

## Usage

**Prerequisites:** Python ≥ 3.11, `git`, `git-lfs`, and `tar` must be on your system PATH. For `zstd` compression, `zstd` is also required.

### 1. Archive a Repository

Creates a complete, single-file tarball archive of a git repository — all refs, LFS objects, and submodules included.

```bash
# Basic usage (produces .tar.gz)
python git_bundle.py archive https://github.com/user/repo.git

# Specify output directory, compression, and verify
python git_bundle.py archive https://github.com/user/repo.git --out ./backups --compress zstd --verify

# Verbose output (shows all git commands)
python git_bundle.py -v archive https://github.com/user/repo.git
```

Note: You need read access to the repository URL.

### 2. Unpack an Archive

Restores a fully functional git repository from a bundle archive.

```bash
python git_bundle.py unpack repo_20251218.tar.gz --dest ./my_projects
```

Note: The archive includes an `archive_manifest.json` with metadata and the original source URL.

### 3. Verify an Archive

Checks the integrity of an existing archive by unpacking to a temp directory and running `git fsck`.

```bash
python git_bundle.py verify repo_20251218.tar.gz
```

## Features

- **Git LFS Support**: Automatically detects and backs up all LFS objects across all refs.
- **Recursive Submodules**: Parses `.gitmodules` (from HEAD) to identify and mirror active submodules. Handles relative submodule URLs.
- **Full Reference Mirroring**: Uses `git clone --mirror` to capture all branches, tags, and refs.
- **Compression Options**: Supports `gz` (default) and `zstd` compression.
- **Verification**: Runs `git fsck --full`, `git lfs fsck`, and submodule status checks.
- **Manifest Generation**: Creates `archive_manifest.json` with source URL, timestamp, and version info.

## Development

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync --group dev

# Run all tests
uv run pytest -v

# Run with coverage
uv run pytest -v --cov=git_bundle --cov-report=term-missing

# Lint
uv run ruff check .

# Type check
uv run ty check git_bundle.py
```
