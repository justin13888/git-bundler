# Git Bundler

`git-bundle.py` is a Python script to create complete Git bundle for archival. (This was mostly vibecoded so feel free to validate yourself.)

## Usage

Prerequisite: `git` and `git-lfs` has to be on system path.

### 1. Archive a Repository

Creates a complete, single-file zip archive of a git repository, including all branches, tags, LFS objects, and submodules.

```bash
# Basic usage
python git-bundle.py archive https://github.com/user/repo.git

# Specify output directory and verify immediately
python git-bundle.py archive https://github.com/user/repo.git --out ./backups --verify
```

Note: You need read access to the repository URL.

### 2. Unpack an Archive

Restores a fully functional git repository from a bundle archive.

```bash
python git-bundle.py unpack repo_20251218.zip --dest ./my_projects
```

Note: The archive has a manifest file that also explains how to restore manually so this command is for convenience.

### 3. Verify an Archive

Checks the integrity of an existing archive without unpacking it permanently.

```bash
python git-bundle.py verify repo_20251218.zip
```

## Features/Limitations of Script

- **Git LFS Support**: Automatically detects and backs up LFS objects not stored in the git bundle itself.
- **Recursive Submodules**: Parses `.gitmodules` (from HEAD) to identify and bundle active submodules.
  - *Edge Case*: Handles relative submodule URLs (e.g., `../library.git`) by resolving them against the parent repository URL.
- **Full Reference Mirroring**: Uses `git clone --mirror` to capture all references, including local branches, remote branches, and tags.
- **Bundle Verification**: Automatically runs `git bundle verify` to ensure the generated bundle is valid and intact.
- **Manifest Generation**: Creates a `archive_manifest.json` file with metadata about the archive, including LFS status and source URL.
- **Robust Error Handling**:
  - Checks for necessary tools (`git`, `git-lfs`) before starting.
  - Continues processing if non-critical steps (like submodule fetching) encounter issues, logging warnings instead of crashing.
