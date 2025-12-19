# Git Bundler

`git-bundle.py` is a Python script to create complete Git bundle for archival. (This was mostly vibecoded so feel free to validate yourself.)

## Usage

```bash
python git-bundle.py https://github.com/user/repo.git --out ./repo_bundle
```

Notes:

- Ensure that you simply have `git` and `git-lfs` installed on system.
- You have read access to the repository URL.

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
