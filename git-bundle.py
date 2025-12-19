#!/usr/bin/env python3
import argparse
import subprocess
import shutil
import sys
import json
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urljoin
from typing import List, Optional, Dict, Any, Union


class Utils:
    @staticmethod
    def run(
        cmd: List[str],
        cwd: Union[str, Path],
        capture_output: bool = True,
        ignore_errors: bool = False,
        verbose: bool = True,
    ) -> Union[subprocess.CompletedProcess, subprocess.CalledProcessError]:
        if verbose:
            print(f"   [CMD] {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                check=not ignore_errors,
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
                text=True,
            )
            return result
        except subprocess.CalledProcessError as e:
            if not ignore_errors:
                print(f"\n❌ Command failed: {' '.join(cmd)}")
                if e.stderr:
                    print(f"   Error details:\n{e.stderr}")
                sys.exit(1)
            return e

    @staticmethod
    def check_dependency(tool: str) -> None:
        if not shutil.which(tool):
            print(f"❌ Critical Error: '{tool}' is not installed or not in PATH.")
            sys.exit(1)


class GitArchiver:
    def __init__(
        self, source_url: str, output_dir: Union[str, Path], verbose: bool = True
    ) -> None:
        self.source_url = source_url
        self.output_dir = Path(output_dir).resolve()
        self.verbose = verbose
        self.repo_name = self._extract_repo_name(source_url)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.final_archive_name = self.output_dir / f"{self.repo_name}_{self.timestamp}"

        Utils.check_dependency("git")
        Utils.check_dependency("git-lfs")

    def _extract_repo_name(self, url: str) -> str:
        path = urlparse(url).path
        name = path.split("/")[-1]
        return name[:-4] if name.endswith(".git") else name

    def _resolve_relative_url(self, parent_url: str, sub_url: str) -> str:
        if sub_url.startswith("./") or sub_url.startswith("../"):
            if not parent_url.endswith("/"):
                parent_url += "/"
            return urljoin(parent_url, sub_url)
        return sub_url

    def archive(self) -> str:
        print(f"📦 Starting Archive for: {self.source_url}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            print(f"⚙️  Processing in temporary workspace: {temp_path}")

            bare_repo_path = temp_path / "bare_repo.git"
            artifacts_dir = temp_path / "artifacts"
            artifacts_dir.mkdir()

            print("\n🔹 Step 1: Cloning bare repository (Mirror)...")
            Utils.run(
                ["git", "clone", "--mirror", self.source_url, str(bare_repo_path)],
                cwd=temp_path,
                verbose=self.verbose,
            )

            print("\n🔹 Step 2: Checking for Git LFS objects...")
            lfs_found = self._handle_lfs(bare_repo_path, artifacts_dir)

            print("\n🔹 Step 3: Checking for Submodules (HEAD)...")
            self._handle_submodules(bare_repo_path, artifacts_dir, temp_path)

            print("\n🔹 Step 4: Creating Git Bundle...")
            bundle_path = artifacts_dir / f"{self.repo_name}.bundle"
            Utils.run(
                ["git", "bundle", "create", str(bundle_path), "--all"],
                cwd=bare_repo_path,
                verbose=self.verbose,
            )

            print("\n🔹 Step 5: Verifying Git Bundle File (Pre-zip)...")
            verify = Utils.run(
                ["git", "bundle", "verify", str(bundle_path)],
                cwd=bare_repo_path,
                verbose=self.verbose,
            )
            # verify is Union[CompletedProcess, CalledProcessError]
            # If ignore_errors=False (default), it would have exited on failure.
            # So here verify is CompletedProcess.
            if isinstance(verify, subprocess.CompletedProcess):
                print(f"   ✅ Bundle Verification: {verify.stdout.splitlines()[0]}")

            self._write_manifest(artifacts_dir, lfs_found)

            print("\n🔹 Step 6: Compressing to single file...")
            zip_path = shutil.make_archive(
                str(self.final_archive_name), "zip", artifacts_dir
            )

            print(f"\n✨ SUCCESS! Archive created:\n   -> {zip_path}")
            return zip_path

    def _handle_lfs(self, repo_path: Path, output_path: Path) -> bool:
        print("   Running 'git lfs fetch --all'...")
        Utils.run(
            ["git", "lfs", "fetch", "--all"],
            cwd=repo_path,
            ignore_errors=True,
            verbose=self.verbose,
        )

        lfs_objects_dir = repo_path / "lfs" / "objects"
        if lfs_objects_dir.exists() and any(lfs_objects_dir.iterdir()):
            dest = output_path / "lfs-objects"
            print("   ⚠️  LFS Objects found! Copying to archive...")
            shutil.copytree(lfs_objects_dir, dest, dirs_exist_ok=True)
            return True
        return False

    def _handle_submodules(
        self, bare_repo_path: Path, artifact_dir: Path, temp_root: Path
    ) -> None:
        cmd = ["git", "config", "--blob", "HEAD:.gitmodules", "--list"]
        result = Utils.run(
            cmd, cwd=bare_repo_path, ignore_errors=True, verbose=self.verbose
        )

        if result.returncode != 0:
            return

        submodules: Dict[str, str] = {}
        # result can be CompletedProcess or CalledProcessError
        # Since ignore_errors=True, it returns CalledProcessError on fail.
        # But we checked returncode != 0 above.
        stdout = (
            result.stdout if isinstance(result, subprocess.CompletedProcess) else ""
        )

        for line in stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parts = key.split(".")
                if len(parts) >= 3 and parts[0] == "submodule" and parts[2] == "url":
                    submodules[parts[1]] = value

        if not submodules:
            return

        print(f"   ⚠️  Found {len(submodules)} submodules. Archiving...")
        sub_artifact_dir = artifact_dir / "submodules"
        sub_artifact_dir.mkdir(exist_ok=True)

        for name, url in submodules.items():
            full_url = self._resolve_relative_url(self.source_url, url)
            sub_bare_path = temp_root / f"sub_{name}.git"

            Utils.run(
                ["git", "clone", "--mirror", full_url, str(sub_bare_path)],
                cwd=temp_root,
                verbose=self.verbose,
            )

            sub_bundle_path = sub_artifact_dir / f"{name}.bundle"
            Utils.run(
                ["git", "bundle", "create", str(sub_bundle_path), "--all"],
                cwd=sub_bare_path,
                verbose=self.verbose,
            )
            print(f"      + Bundled submodule: {name}")

    def _write_manifest(self, artifact_dir: Path, lfs_found: bool) -> None:
        manifest = {
            "source_url": self.source_url,
            "archived_at": self.timestamp,
            "repo_name": self.repo_name,
            "contains_lfs": lfs_found,
            "version": "1.0",
        }
        with open(artifact_dir / "archive_manifest.json", "w") as f:
            json.dump(manifest, f, indent=4)


class GitUnpacker:
    def __init__(
        self,
        zip_path: Union[str, Path],
        dest_dir: Optional[Union[str, Path]] = None,
        verbose: bool = True,
    ) -> None:
        self.zip_path: Path = Path(zip_path).resolve()
        self.verbose: bool = verbose
        if dest_dir:
            self.output_dir: Path = Path(dest_dir).resolve()
        else:
            # Default to extracting in current dir with zip name
            self.output_dir: Path = Path.cwd() / self.zip_path.stem

    def unpack(self) -> Path:
        print(f"🔓 Unpacking archive: {self.zip_path}")

        if not self.zip_path.exists():
            print(f"❌ Error: Archive not found at {self.zip_path}")
            sys.exit(1)

        # 1. Unzip
        temp_extract_dir = self.output_dir / ".tmp_extract"
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        temp_extract_dir.mkdir(parents=True)

        try:
            with zipfile.ZipFile(self.zip_path, "r") as zip_ref:
                zip_ref.extractall(temp_extract_dir)

            # Read manifest
            manifest_path = temp_extract_dir / "archive_manifest.json"
            if not manifest_path.exists():
                print("❌ Error: Invalid archive (missing manifest).")
                sys.exit(1)

            with open(manifest_path) as f:
                manifest: Dict[str, Any] = json.load(f)

            repo_name: str = manifest.get("repo_name", "restored_repo")
            final_repo_path = self.output_dir / repo_name

            # 2. Clone from Bundle
            print(f"🔹 Restoring repository to: {final_repo_path}")
            bundle_files = list(temp_extract_dir.glob("*.bundle"))
            if not bundle_files:
                print("❌ Error: No bundle file found in archive.")
                sys.exit(1)
            main_bundle = bundle_files[0]

            # Use --no-checkout to prevent LFS smudge filter from failing (objects aren't there yet)
            Utils.run(
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    str(main_bundle),
                    str(final_repo_path),
                ],
                cwd=self.output_dir,
                verbose=self.verbose,
            )

            # Fix origin remote to point to original URL instead of bundle path
            original_url: Optional[str] = manifest.get("source_url")
            if original_url:
                Utils.run(
                    ["git", "remote", "set-url", "origin", original_url],
                    cwd=final_repo_path,
                    verbose=False,
                )

            # 3. Restore LFS
            if manifest.get("contains_lfs"):
                lfs_src = temp_extract_dir / "lfs-objects"
                lfs_dest = final_repo_path / ".git" / "lfs" / "objects"
                if lfs_src.exists():
                    print("🔹 Restoring LFS objects...")
                    lfs_dest.mkdir(parents=True, exist_ok=True)
                    # Merge directories
                    shutil.copytree(lfs_src, lfs_dest, dirs_exist_ok=True)

            # 4. Checkout HEAD now that LFS objects are present
            print("🔹 Checking out HEAD...")
            Utils.run(
                ["git", "checkout", "HEAD"],
                cwd=final_repo_path,
                verbose=self.verbose,
                ignore_errors=False,
            )

            if manifest.get("contains_lfs"):
                Utils.run(
                    ["git", "lfs", "checkout"],
                    cwd=final_repo_path,
                    verbose=self.verbose,
                )

            # 4. Restore Submodules
            submodules_dir = temp_extract_dir / "submodules"
            if submodules_dir.exists():
                print("🔹 Restoring submodules...")
                # Initialize connection to submodules
                Utils.run(
                    ["git", "submodule", "init"],
                    cwd=final_repo_path,
                    verbose=self.verbose,
                )

                # We need to map submodule names to their paths in .gitmodules
                # Run 'git config --list' to find submodule definitions
                result = Utils.run(
                    ["git", "config", "--file", ".gitmodules", "--list"],
                    cwd=final_repo_path,
                    verbose=False,
                )

                sub_paths: Dict[str, str] = {}  # module_name -> path
                # result can be CompeletedProcess or CalledProcessError
                stdout = (
                    result.stdout
                    if isinstance(result, subprocess.CompletedProcess)
                    else ""
                )
                for line in stdout.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        if k.startswith("submodule.") and k.endswith(".path"):
                            # submodule.<name>.path
                            name = k.split(".")[1]
                            sub_paths[name] = v

                for bundle in submodules_dir.glob("*.bundle"):
                    sub_name = bundle.stem
                    if sub_name in sub_paths:
                        sub_path = sub_paths[sub_name]
                        print(
                            f"   + Restoring submodule '{sub_name}' (at {sub_path})..."
                        )

                        # Point submodule to local bundle
                        Utils.run(
                            [
                                "git",
                                "config",
                                f"submodule.{sub_name}.url",
                                str(bundle.resolve()),
                            ],
                            cwd=final_repo_path,
                            verbose=self.verbose,
                        )
                        # Update (clone)
                        Utils.run(
                            ["git", "submodule", "update", "--no-fetch", sub_name],
                            cwd=final_repo_path,
                            verbose=self.verbose,
                        )
                        # Optionally restore original URL if possible, but keep it pointing to bundle for now to ensure integrity

            print(f"✨ Restore complete at: {final_repo_path}")
            return final_repo_path

        finally:
            # Cleanup temp extract
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)


class GitVerifier:
    @staticmethod
    def verify(zip_path: Union[str, Path], verbose: bool = True) -> None:
        print(f"🔍 Verifying archive: {zip_path}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Use Unpacker to restore into temp
            unpacker = GitUnpacker(zip_path, dest_dir=temp_path, verbose=verbose)
            repo_path = unpacker.unpack()

            print("\n🔹 Running Integrity Checks...")

            # 1. Git Fsck
            print("   Running 'git fsck'...")
            Utils.run(["git", "fsck", "--full"], cwd=repo_path, verbose=verbose)

            # 2. LFS Fsck
            if (repo_path / ".git" / "lfs").exists():
                print("   Running 'git lfs fsck'...")
                Utils.run(["git", "lfs", "fsck"], cwd=repo_path, verbose=verbose)

            # 3. Submodule status
            if (repo_path / ".gitmodules").exists():
                print("   Checking submodule status...")
                Utils.run(
                    ["git", "submodule", "status", "--recursive"],
                    cwd=repo_path,
                    verbose=verbose,
                )

            print("\n✅ Verification Passed! The archive contains a valid repository.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Git Bundler: Archival, Restoration, and Verification Tool"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Archive
    parser_archive = subparsers.add_parser(
        "archive", help="Create a forensic-grade Git archive"
    )
    parser_archive.add_argument("url", help="Remote URL of the Git repository")
    parser_archive.add_argument("--out", default=".", help="Output directory")
    parser_archive.add_argument(
        "--verify",
        action="store_true",
        help="Verify the archive immediately after creation",
    )

    # Unpack
    parser_unpack = subparsers.add_parser(
        "unpack", help="Restore a Git repository from an archive"
    )
    parser_unpack.add_argument("zip_file", help="Path to the .zip archive")
    parser_unpack.add_argument(
        "--dest", help="Destination directory (default: current dir)"
    )

    # Verify
    parser_verify = subparsers.add_parser(
        "verify", help="Verify the integrity of an existing archive"
    )
    parser_verify.add_argument("zip_file", help="Path to the .zip archive")

    args = parser.parse_args()

    try:
        if args.command == "archive":
            archiver = GitArchiver(args.url, args.out)
            zip_path = archiver.archive()
            if args.verify:
                GitVerifier.verify(zip_path)

        elif args.command == "unpack":
            unpacker = GitUnpacker(args.zip_file, args.dest)
            unpacker.unpack()

        elif args.command == "verify":
            GitVerifier.verify(args.zip_file)

    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
