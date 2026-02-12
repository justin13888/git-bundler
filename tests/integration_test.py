#!/usr/bin/env python3
"""Integration tests for git_bundle.py.

These tests create real Git repos on disk and exercise the full
archive -> unpack -> verify cycle. They require git, git-lfs, and tar.

Run with:  pytest tests/integration_test.py -v
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.resolve()
BUNDLE_SCRIPT = SCRIPT_DIR.parent / "git_bundle.py"
GENERATE_SCRIPT = SCRIPT_DIR / "generate_test_repo.py"
TEST_ROOT = Path("/tmp/git_bundler_integration_test")


@pytest.fixture(scope="session")
def test_repo():
    """Generate a test repository once for the entire test session."""
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    TEST_ROOT.mkdir()

    source_dir = TEST_ROOT / "source"
    subprocess.run(
        [sys.executable, str(GENERATE_SCRIPT), "--out", str(source_dir)],
        check=True,
    )
    yield source_dir
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)


def _run_bundle(*args: str) -> subprocess.CompletedProcess[str]:
    """Helper to run git_bundle.py with given arguments."""
    return subprocess.run(
        [sys.executable, str(BUNDLE_SCRIPT), *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Archive + Unpack roundtrip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("compression", ["gz", "zstd"])
def test_archive_unpack_roundtrip(test_repo, compression, tmp_path):
    """Full archive -> unpack cycle for each compression format."""
    if compression == "zstd" and not shutil.which("zstd"):
        pytest.skip("zstd not installed")

    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    restore_dir = tmp_path / "restore"

    # Archive
    result = _run_bundle("archive", repo_url, "--out", str(archive_dir), "--compress", compression)
    assert result.returncode == 0, f"Archive failed:\n{result.stderr}"

    ext = "tar.zst" if compression == "zstd" else "tar.gz"
    archives = list(archive_dir.glob(f"*.{ext}"))
    assert len(archives) == 1, f"Expected 1 archive, found {len(archives)}"

    # Unpack
    result = _run_bundle("unpack", str(archives[0]), "--dest", str(restore_dir))
    assert result.returncode == 0, (
        f"Unpack failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    restored_repo = restore_dir / "main_repo"
    assert restored_repo.exists(), "Restored repo directory missing"
    assert (restored_repo / ".git").is_dir(), "Not a git repo"
    assert (restored_repo / "main_file.txt").exists(), "main_file.txt missing"


# ---------------------------------------------------------------------------
# Archive with --verify
# ---------------------------------------------------------------------------
def test_archive_with_verify(test_repo, tmp_path):
    """The --verify flag should run verification after archiving."""
    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    result = _run_bundle("archive", repo_url, "--out", str(archive_dir), "--verify")
    assert result.returncode == 0, f"Archive+verify failed:\n{result.stderr}"
    assert "Verification passed" in result.stderr


# ---------------------------------------------------------------------------
# Standalone verify
# ---------------------------------------------------------------------------
def test_standalone_verify(test_repo, tmp_path):
    """The verify subcommand should pass on a valid archive."""
    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    result = _run_bundle("archive", repo_url, "--out", str(archive_dir))
    assert result.returncode == 0
    archive_file = list(archive_dir.glob("*.tar.zst"))[0]

    result = _run_bundle("verify", str(archive_file))
    assert result.returncode == 0, f"Verify failed:\n{result.stderr}"
    assert "Verification passed" in result.stderr


# ---------------------------------------------------------------------------
# Submodule content preservation
# ---------------------------------------------------------------------------
def test_submodule_content_preserved(test_repo, tmp_path):
    """Submodule files should exist after unpack."""
    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    restore_dir = tmp_path / "restore"

    _run_bundle("archive", repo_url, "--out", str(archive_dir))
    archive_file = list(archive_dir.glob("*.tar.zst"))[0]
    _run_bundle("unpack", str(archive_file), "--dest", str(restore_dir))

    sub_file = restore_dir / "main_repo" / "submodule_dir" / "sub_file.txt"
    assert sub_file.exists(), "Submodule file sub_file.txt missing after restore"
    assert sub_file.read_text() == "I am a submodule file."


# ---------------------------------------------------------------------------
# LFS content preservation
# ---------------------------------------------------------------------------
def test_lfs_content_preserved(test_repo, tmp_path):
    """LFS-tracked file should have correct size (1MB) after unpack."""
    if not shutil.which("git-lfs"):
        pytest.skip("git-lfs not installed")

    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    restore_dir = tmp_path / "restore"

    _run_bundle("archive", repo_url, "--out", str(archive_dir))
    archive_file = list(archive_dir.glob("*.tar.zst"))[0]
    _run_bundle("unpack", str(archive_file), "--dest", str(restore_dir))

    lfs_file = restore_dir / "main_repo" / "large_file.bin"
    assert lfs_file.exists(), "LFS file large_file.bin missing"
    assert lfs_file.stat().st_size == 1024 * 1024, f"LFS file size wrong: {lfs_file.stat().st_size}"


# ---------------------------------------------------------------------------
# Branch and tag preservation
# ---------------------------------------------------------------------------
def test_branch_preservation(test_repo, tmp_path):
    """All branches should survive the archive -> unpack roundtrip."""
    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    restore_dir = tmp_path / "restore"

    _run_bundle("archive", repo_url, "--out", str(archive_dir))
    archive_file = list(archive_dir.glob("*.tar.zst"))[0]
    result = _run_bundle("unpack", str(archive_file), "--dest", str(restore_dir))
    assert result.returncode == 0

    restored_repo = restore_dir / "main_repo"
    branch_result = subprocess.run(
        ["git", "branch", "-a"],
        cwd=restored_repo,
        capture_output=True,
        text=True,
    )
    assert "feature/extra" in branch_result.stdout, (
        f"Feature branch not found in:\n{branch_result.stdout}"
    )


def test_tag_preservation(test_repo, tmp_path):
    """Tagged releases should survive the archive -> unpack roundtrip."""
    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    restore_dir = tmp_path / "restore"

    _run_bundle("archive", repo_url, "--out", str(archive_dir))
    archive_file = list(archive_dir.glob("*.tar.zst"))[0]
    result = _run_bundle("unpack", str(archive_file), "--dest", str(restore_dir))
    assert result.returncode == 0

    restored_repo = restore_dir / "main_repo"
    tag_result = subprocess.run(
        ["git", "tag"],
        cwd=restored_repo,
        capture_output=True,
        text=True,
    )
    assert "v1.0.0" in tag_result.stdout, f"Tag v1.0.0 not found in:\n{tag_result.stdout}"


# ---------------------------------------------------------------------------
# Simple repo (no LFS, no submodules)
# ---------------------------------------------------------------------------
def test_simple_repo_roundtrip(tmp_path):
    """Archive/unpack should work for a minimal repo without LFS or submodules."""
    # Create a minimal repo
    simple_repo = tmp_path / "simple"
    simple_repo.mkdir()
    subprocess.run(["git", "init"], cwd=simple_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=simple_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=simple_repo,
        check=True,
        capture_output=True,
    )
    (simple_repo / "hello.txt").write_text("hello world")
    subprocess.run(["git", "add", "."], cwd=simple_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=simple_repo,
        check=True,
        capture_output=True,
    )

    repo_url = f"file://{simple_repo}"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    restore_dir = tmp_path / "restore"

    result = _run_bundle("archive", repo_url, "--out", str(archive_dir))
    assert result.returncode == 0, f"Archive failed:\n{result.stderr}"

    archive_file = list(archive_dir.glob("*.tar.zst"))[0]
    result = _run_bundle("unpack", str(archive_file), "--dest", str(restore_dir))
    assert result.returncode == 0, f"Unpack failed:\n{result.stderr}"

    restored = restore_dir / "simple"
    assert (restored / "hello.txt").read_text() == "hello world"


# ---------------------------------------------------------------------------
# Corrupted archive
# ---------------------------------------------------------------------------
def test_corrupted_archive(test_repo, tmp_path):
    """A truncated archive should fail with a clear error."""
    repo_url = f"file://{test_repo}/main_repo"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    _run_bundle("archive", repo_url, "--out", str(archive_dir))
    archive_file = list(archive_dir.glob("*.tar.zst"))[0]

    # Truncate the archive to corrupt it
    corrupted = tmp_path / "corrupted.tar.gz"
    data = archive_file.read_bytes()
    corrupted.write_bytes(data[: len(data) // 4])

    result = _run_bundle("unpack", str(corrupted))
    assert result.returncode != 0, "Unpacking corrupted archive should fail"


# ---------------------------------------------------------------------------
# Error scenarios
# ---------------------------------------------------------------------------
def test_unpack_nonexistent_archive():
    """Unpacking a nonexistent file should fail with a clear error."""
    result = _run_bundle("unpack", "/tmp/does_not_exist_12345.tar.gz")
    assert result.returncode != 0
    assert "Archive not found" in result.stdout or "Archive not found" in result.stderr
