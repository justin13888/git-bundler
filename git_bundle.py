#!/usr/bin/env python3
"""Git Bundler: Create complete Git archives for archival.

Archives include all branches, tags, LFS objects, and submodules.
Supports gz (default) and zstd compression.
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHIVE_VERSION = "2.0"
MANIFEST_FILENAME = "archive_manifest.json"
BARE_REPO_DIRNAME = "repo.git"

SUBMODULES_DIRNAME = "submodules"
CompressionType = Literal["gz", "zstd"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GitBundlerError(Exception):
    """Raised for any fatal error during archive/unpack/verify."""


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def run_command(
    cmd: list[str],
    cwd: Path,
    *,
    capture_output: bool = True,
    ignore_errors: bool = False,
    verbose: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return the CompletedProcess result.

    Args:
        cmd: Command and arguments to execute.
        cwd: Working directory for the command.
        capture_output: If True, capture stdout/stderr via PIPE.
        ignore_errors: If True, return result even on non-zero exit.
            If False (default), raise GitBundlerError on failure.
        verbose: If True, log the command at DEBUG level.

    Returns:
        The subprocess.CompletedProcess result.

    Raises:
        GitBundlerError: If the command fails and ignore_errors is False.
    """
    if verbose:
        logger.debug("   [CMD] %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True,
    )
    if result.returncode != 0 and not ignore_errors:
        detail = f"\n   Error details:\n{result.stderr}" if result.stderr else ""
        raise GitBundlerError(f"Command failed: {' '.join(cmd)}{detail}")
    return result


def check_dependency(tool: str) -> None:
    """Verify that a command-line tool is available on PATH.

    Raises:
        GitBundlerError: If the tool is not found.
    """
    if not shutil.which(tool):
        raise GitBundlerError(f"'{tool}' is not installed or not in PATH.")


def parse_git_config(config_output: str, section: str, key: str) -> dict[str, str]:
    """Parse ``git config --list`` output, extracting matching entries.

    Looks for lines of the form ``<section>.<name>.<key>=<value>`` and
    returns a mapping of ``{name: value}``.

    Example::

        parse_git_config(output, "submodule", "url")
        # extracts submodule.<name>.url entries -> {name: url}

    Args:
        config_output: Raw output from ``git config --list``.
        section: Section prefix to match (e.g. ``"submodule"``).
        key: Key suffix to match (e.g. ``"url"``).

    Returns:
        Dict mapping the middle name component to the value.
    """
    result: dict[str, str] = {}
    for line in config_output.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        parts = k.split(".")
        if len(parts) >= 3 and parts[0] == section and parts[2] == key:
            result[parts[1]] = v
    return result


# ---------------------------------------------------------------------------
# GitArchiver
# ---------------------------------------------------------------------------


class GitArchiver:
    """Creates a complete, single-file tarball archive of a Git repository.

    The archive includes the bare mirror clone, all LFS objects, recursively
    cloned submodules, and an ``archive_manifest.json`` with metadata.

    Args:
        source_url: Git remote URL or local path to archive.
        output_dir: Directory to write the archive tarball into.
        verbose: Enable verbose command logging.
    """

    def __init__(self, source_url: str, output_dir: Path, verbose: bool = True) -> None:
        self.source_url = source_url
        self.output_dir = Path(output_dir).resolve()
        self.verbose = verbose
        self.repo_name = self._extract_repo_name(source_url)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        check_dependency("git")
        check_dependency("git-lfs")
        check_dependency("tar")

    @staticmethod
    def _extract_repo_name(url: str) -> str:
        """Derive a human-readable repo name from a URL or path.

        Handles HTTPS URLs, local paths, bare names, and strips ``.git`` suffixes
        and trailing slashes.

        Examples:
            ``https://github.com/user/repo.git`` -> ``repo``
            ``git@github.com:user/repo.git`` -> ``repo``
            ``/home/user/repo.git`` -> ``repo``
        """
        # Handle SSH-style URLs (git@host:user/repo.git)
        if ":" in url and not url.startswith(("http://", "https://", "/")):
            path = url.split(":")[-1]
        else:
            path = urlparse(url).path
        name = path.rstrip("/").split("/")[-1]
        return name.removesuffix(".git")

    @staticmethod
    def _resolve_relative_url(parent_url: str, sub_url: str) -> str:
        """Resolve a potentially relative submodule URL against the parent.

        If ``sub_url`` starts with ``./`` or ``../``, it is resolved relative
        to ``parent_url`` using ``urllib.parse.urljoin``. Otherwise it is
        returned unchanged.
        """
        if sub_url.startswith(("./", "../")):
            if not parent_url.endswith("/"):
                parent_url += "/"
            return urljoin(parent_url, sub_url)
        return sub_url

    def archive(self, compression: CompressionType = "zstd") -> str:
        """Run the full archive workflow.

        Steps:
            1. ``git clone --mirror`` the repository.
            2. ``git lfs fetch --all`` to pull down LFS objects.
            3. Detect and mirror all submodules from HEAD's ``.gitmodules``.
            4. Write ``archive_manifest.json`` with source URL and timestamp.
            5. Create a compressed tarball (gz or zstd).

        Args:
            compression: ``"gz"`` or ``"zstd"`` (default).

        Returns:
            Absolute path to the created archive file.
        """
        if compression == "zstd":
            check_dependency("zstd")

        logger.info("Starting archive for: %s", self.source_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            logger.debug("Working in temporary directory: %s", temp_path)

            # 1. Clone Mirror
            logger.info("Step 1/4: Cloning bare repository (mirror)...")
            repo_dir = temp_path / BARE_REPO_DIRNAME
            run_command(
                ["git", "clone", "--mirror", self.source_url, str(repo_dir)],
                cwd=temp_path,
                verbose=self.verbose,
            )

            # 2. LFS Fetch
            logger.info("Step 2/4: Fetching LFS objects...")
            self._handle_lfs(repo_dir)

            # 3. Submodules
            logger.info("Step 3/4: Archiving submodules...")
            self._handle_submodules(repo_dir, temp_path)

            # 4. Manifest
            self._write_manifest(temp_path)

            # 5. Compress
            logger.info("Step 4/4: Compressing archive (%s)...", compression)
            archive_ext = "tar.zst" if compression == "zstd" else "tar.gz"
            output_file = self.output_dir / f"{self.repo_name}_{self.timestamp}.{archive_ext}"
            self._create_tarball(temp_path, output_file, compression)

            logger.info("Archive created: %s", output_file)
            return str(output_file)

    def _handle_lfs(self, repo_path: Path) -> None:
        """Fetch all LFS objects for the given bare repo.

        Runs ``git lfs fetch --all`` with errors suppressed, since repos
        without LFS will return a non-zero exit code.
        """
        logger.debug("Running 'git lfs fetch --all'...")
        run_command(
            ["git", "lfs", "fetch", "--all"],
            cwd=repo_path,
            ignore_errors=True,
            verbose=self.verbose,
        )

    def _handle_submodules(self, bare_repo_path: Path, temp_root: Path) -> None:
        """Detect and mirror submodules listed in HEAD's ``.gitmodules``.

        Reads ``.gitmodules`` from the bare repo's HEAD commit, parses it
        with ``git config``, and clones each submodule as a bare mirror
        into a ``submodules/`` directory alongside the main repo.

        Relative submodule URLs are resolved against the parent repo's
        source URL.
        """
        result = run_command(
            ["git", "show", "HEAD:.gitmodules"],
            cwd=bare_repo_path,
            ignore_errors=True,
            verbose=False,
        )
        if result.returncode != 0:
            return

        # Write blob to temp file so git config can parse it
        modules_file = temp_root / ".gitmodules_tmp"
        modules_file.write_text(result.stdout)

        result_cfg = run_command(
            ["git", "config", "-f", str(modules_file), "--list"],
            cwd=temp_root,
            verbose=False,
        )
        submodules = parse_git_config(result_cfg.stdout, "submodule", "url")

        if not submodules:
            return

        logger.info("Found %d submodule(s). Archiving...", len(submodules))
        sub_dir = temp_root / SUBMODULES_DIRNAME
        sub_dir.mkdir(exist_ok=True)

        for name, url in submodules.items():
            full_url = self._resolve_relative_url(self.source_url, url)
            sub_path = sub_dir / f"{name}.git"
            logger.info("  Cloning submodule: %s -> %s", name, sub_path.name)
            run_command(
                ["git", "clone", "--mirror", full_url, str(sub_path)],
                cwd=temp_root,
                verbose=self.verbose,
            )
            self._handle_lfs(sub_path)

    def _write_manifest(self, temp_path: Path) -> None:
        """Write ``archive_manifest.json`` with archive metadata.

        The manifest includes the source URL, timestamp, repository name,
        and archive format version.
        """
        manifest = {
            "source_url": self.source_url,
            "archived_at": self.timestamp,
            "repo_name": self.repo_name,
            "version": ARCHIVE_VERSION,
        }
        manifest_path = temp_path / MANIFEST_FILENAME
        manifest_path.write_text(json.dumps(manifest, indent=4))

    def _create_tarball(
        self, source_dir: Path, output_file: Path, compression: CompressionType
    ) -> None:
        """Create a compressed tarball from the source directory.

        Args:
            source_dir: Directory whose contents to archive.
            output_file: Path for the resulting tarball.
            compression: ``"gz"`` or ``"zstd"``.
        """
        if compression == "zstd":
            cmd = [
                "tar",
                "--use-compress-program=zstd",
                "-cf",
                str(output_file),
                "-C",
                str(source_dir),
                ".",
            ]
        else:
            cmd = ["tar", "-czf", str(output_file), "-C", str(source_dir), "."]
        run_command(cmd, cwd=source_dir, verbose=self.verbose)


# ---------------------------------------------------------------------------
# GitUnpacker
# ---------------------------------------------------------------------------


class GitUnpacker:
    """Restores a fully functional Git repository from a bundler archive.

    Extracts the tarball, clones from the bare mirror, restores the original
    remote URL, and re-attaches submodules from their archived mirrors.

    Args:
        archive_path: Path to the archive tarball.
        dest_dir: Destination directory for the restored repo.
            Defaults to a directory named after the archive in the CWD.
        verbose: Enable verbose command logging.
    """

    def __init__(
        self,
        archive_path: Path,
        dest_dir: Path | None = None,
        verbose: bool = True,
    ) -> None:
        self.archive_path = Path(archive_path).resolve()
        self.verbose = verbose
        if dest_dir:
            self.output_dir = Path(dest_dir).resolve()
        else:
            self.output_dir = Path.cwd() / self.archive_path.stem.split(".")[0]

    def unpack(self) -> Path:
        """Extract and restore the repository from the archive.

        Steps:
            1. Extract the tarball to a temporary directory.
            2. Read ``archive_manifest.json`` for metadata.
            3. Clone from the bare mirror to create a working repo.
            4. Reset the ``origin`` remote to the original source URL.
            5. Restore submodules from their archived mirrors.

        Returns:
            Path to the restored working repository.

        Raises:
            GitBundlerError: If the archive is missing, malformed,
                or lacks required components.
        """
        logger.info("Unpacking archive: %s", self.archive_path)

        if not self.archive_path.exists():
            raise GitBundlerError(f"Archive not found at {self.archive_path}")

        # 1. Extract Tarball
        temp_extract_dir = self.output_dir / ".tmp_extract"
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        temp_extract_dir.mkdir(parents=True)

        logger.info("Extracting tarball...")
        run_command(
            ["tar", "-xf", str(self.archive_path), "-C", str(temp_extract_dir)],
            cwd=self.output_dir,
            verbose=self.verbose,
        )

        try:
            # Read manifest
            manifest_path = temp_extract_dir / MANIFEST_FILENAME
            if not manifest_path.exists():
                raise GitBundlerError("Invalid archive (missing manifest).")

            manifest: dict[str, str] = json.loads(manifest_path.read_text())

            repo_name = manifest.get("repo_name", "restored_repo")
            final_repo_path = self.output_dir / repo_name
            bare_repo_source = temp_extract_dir / BARE_REPO_DIRNAME

            if not bare_repo_source.exists():
                raise GitBundlerError(f"Invalid archive (missing {BARE_REPO_DIRNAME}).")

            # 2. Clone from Mirror
            logger.info("Restoring repository to: %s", final_repo_path)
            run_command(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "clone",
                    str(bare_repo_source),
                    str(final_repo_path),
                ],
                cwd=self.output_dir,
                verbose=self.verbose,
            )

            # Fix origin remote to point to original URL
            original_url = manifest.get("source_url")
            if original_url:
                run_command(
                    ["git", "remote", "set-url", "origin", original_url],
                    cwd=final_repo_path,
                    verbose=False,
                )

            # 3. Restore Submodules
            self._restore_submodules(final_repo_path, temp_extract_dir)

            logger.info("Restore complete at: %s", final_repo_path)
            return final_repo_path

        finally:
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

    def _restore_submodules(self, repo_path: Path, extract_dir: Path) -> None:
        """Re-attach submodules from archived local mirrors.

        Reads ``.gitmodules`` from the restored repo, initializes submodules,
        then for each submodule that has an archived mirror in the
        ``submodules/`` directory, rewrites its URL to the local mirror
        and runs ``git submodule update``.
        """
        result = run_command(
            ["git", "config", "--file", ".gitmodules", "--list"],
            cwd=repo_path,
            verbose=False,
            ignore_errors=True,
        )
        if result.returncode != 0:
            return

        submodules_map = parse_git_config(result.stdout, "submodule", "path")
        submodules_source_dir = extract_dir / SUBMODULES_DIRNAME

        if not submodules_source_dir.exists() or not submodules_map:
            return

        logger.info("Restoring submodules...")
        run_command(["git", "submodule", "init"], cwd=repo_path, verbose=False)

        for name, _path in submodules_map.items():
            sub_git_source = submodules_source_dir / f"{name}.git"
            if not sub_git_source.exists():
                continue

            logger.info("  Linking submodule '%s' to local mirror...", name)
            run_command(
                [
                    "git",
                    "config",
                    f"submodule.{name}.url",
                    str(sub_git_source.resolve()),
                ],
                cwd=repo_path,
                verbose=False,
            )
            run_command(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "update",
                    name,
                ],
                cwd=repo_path,
                verbose=self.verbose,
            )


# ---------------------------------------------------------------------------
# GitVerifier
# ---------------------------------------------------------------------------


class GitVerifier:
    """Verifies the integrity of a bundler archive.

    Unpacks to a temporary directory and runs ``git fsck``,
    ``git lfs fsck`` (if LFS objects present), and
    ``git submodule status`` (if submodules present).
    """

    @staticmethod
    def verify(archive_path: Path, verbose: bool = True) -> None:
        """Verify archive integrity.

        Args:
            archive_path: Path to the archive tarball to verify.
            verbose: Enable verbose command logging.

        Raises:
            GitBundlerError: If any integrity check fails.
        """
        logger.info("Verifying archive: %s", archive_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            unpacker = GitUnpacker(archive_path, dest_dir=temp_path, verbose=verbose)
            repo_path = unpacker.unpack()

            logger.info("Running integrity checks...")

            logger.info("  Running 'git fsck'...")
            run_command(["git", "fsck", "--full"], cwd=repo_path, verbose=verbose)

            if (repo_path / ".git" / "lfs").exists():
                logger.info("  Running 'git lfs fsck'...")
                run_command(["git", "lfs", "fsck"], cwd=repo_path, verbose=verbose)

            if (repo_path / ".gitmodules").exists():
                logger.info("  Checking submodule status...")
                run_command(
                    ["git", "submodule", "status", "--recursive"],
                    cwd=repo_path,
                    verbose=verbose,
                )

            logger.info("Verification passed! Archive contains a valid repository.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse arguments and dispatch to the appropriate command."""
    parser = argparse.ArgumentParser(
        description="Git Bundler: Archival, Restoration, and Verification Tool"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG-level) output",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Archive
    parser_archive = subparsers.add_parser("archive", help="Create a complete Git archive")
    parser_archive.add_argument("url", help="Remote URL of the Git repository")
    parser_archive.add_argument("--out", default=".", help="Output directory")
    parser_archive.add_argument(
        "--compress",
        default="zstd",
        choices=["gz", "zstd"],
        help="Compression format (gz or zstd)",
    )
    parser_archive.add_argument(
        "--verify", action="store_true", help="Verify the archive after creation"
    )

    # Unpack
    parser_unpack = subparsers.add_parser("unpack", help="Restore a Git repository from an archive")
    parser_unpack.add_argument("archive_file", help="Path to the archive file")
    parser_unpack.add_argument("--dest", help="Destination directory (default: current dir)")

    # Verify
    parser_verify = subparsers.add_parser("verify", help="Verify the integrity of an archive")
    parser_verify.add_argument("archive_file", help="Path to the archive file")

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s")

    try:
        if args.command == "archive":
            archiver = GitArchiver(args.url, args.out, verbose=args.verbose)
            archive_path = archiver.archive(compression=args.compress)
            if args.verify:
                GitVerifier.verify(Path(archive_path), verbose=args.verbose)

        elif args.command == "unpack":
            unpacker = GitUnpacker(args.archive_file, args.dest, verbose=args.verbose)
            unpacker.unpack()

        elif args.command == "verify":
            GitVerifier.verify(Path(args.archive_file), verbose=args.verbose)

    except GitBundlerError as e:
        logger.error("Error: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("Operation cancelled by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
