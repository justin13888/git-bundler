#!/usr/bin/env python3
"""Generate a test Git repository with LFS objects, submodules, branches, and tags.

Used as a fixture for integration tests. Creates:
- A submodule repository with a single file
- A main repository with:
  - A regular file
  - A Git LFS-tracked 1MB binary file
  - The submodule added as a dependency
  - A feature branch with additional content
  - A tagged release
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def run_git(cmd: list[str], cwd: str | Path) -> None:
    """Run a git command, allowing file protocol for local submodules."""
    base_cmd = ["git", "-c", "protocol.file.allow=always"]
    full_cmd = base_cmd + cmd
    try:
        subprocess.run(full_cmd, cwd=cwd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {' '.join(full_cmd)}")
        print(f"  stdout: {e.stdout}")
        print(f"  stderr: {e.stderr}")
        raise


def generate_repo(output_dir: str) -> Path:
    """Generate the complete test repository structure.

    Args:
        output_dir: Root directory for all generated repos.

    Returns:
        Path to the output_dir root (contains main_repo/ and submodule_repo/).
    """
    root = Path(output_dir).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    print(f"Generating test repos in: {root}")

    # 1. Create Submodule Repo
    sub_repo = root / "submodule_repo"
    sub_repo.mkdir()
    run_git(["init"], cwd=sub_repo)
    run_git(["config", "user.email", "test@example.com"], cwd=sub_repo)
    run_git(["config", "user.name", "Test User"], cwd=sub_repo)

    (sub_repo / "sub_file.txt").write_text("I am a submodule file.")
    run_git(["add", "sub_file.txt"], cwd=sub_repo)
    run_git(["commit", "-m", "Initial submodule commit"], cwd=sub_repo)

    # 2. Create Main Repo
    main_repo = root / "main_repo"
    main_repo.mkdir()
    run_git(["init"], cwd=main_repo)
    run_git(["config", "user.email", "test@example.com"], cwd=main_repo)
    run_git(["config", "user.name", "Test User"], cwd=main_repo)

    (main_repo / "main_file.txt").write_text("I am a main repo file.")
    run_git(["add", "main_file.txt"], cwd=main_repo)
    run_git(["commit", "-m", "Initial main commit"], cwd=main_repo)

    # 3. Setup LFS
    if shutil.which("git-lfs"):
        print("Setting up Git LFS...")
        run_git(["lfs", "install"], cwd=main_repo)
        run_git(["lfs", "track", "*.bin"], cwd=main_repo)
        run_git(["add", ".gitattributes"], cwd=main_repo)

        large_file_path = main_repo / "large_file.bin"
        with open(large_file_path, "wb") as f:
            f.write(os.urandom(1024 * 1024))  # 1MB

        run_git(["add", "large_file.bin"], cwd=main_repo)
        run_git(["commit", "-m", "Add LFS file"], cwd=main_repo)
    else:
        print("Warning: git-lfs not found, skipping LFS setup.")

    # 4. Add Submodule
    print("Adding submodule...")
    sub_url = f"file://{sub_repo}"
    run_git(["submodule", "add", sub_url, "submodule_dir"], cwd=main_repo)
    run_git(["commit", "-m", "Add submodule"], cwd=main_repo)

    # 5. Create a feature branch with extra content
    print("Creating feature branch...")
    run_git(["checkout", "-b", "feature/extra"], cwd=main_repo)
    (main_repo / "feature_file.txt").write_text("Feature branch content.")
    run_git(["add", "feature_file.txt"], cwd=main_repo)
    run_git(["commit", "-m", "Add feature file"], cwd=main_repo)
    run_git(["checkout", "master"], cwd=main_repo)

    # 6. Tag a release
    print("Creating tag...")
    run_git(["tag", "-a", "v1.0.0", "-m", "Release v1.0.0"], cwd=main_repo)

    print(f"Generated test repo at: {root}")
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="/tmp/test_git_bundler_repo", help="Output directory for repos"
    )
    args = parser.parse_args()
    generate_repo(args.out)
