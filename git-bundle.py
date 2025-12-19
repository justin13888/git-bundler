#!/usr/bin/env python3
import argparse
import subprocess
import shutil
import sys
import json
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urljoin


class GitBundler:
    def __init__(self, source_url, output_dir, verbose=True):
        self.source_url = source_url
        self.base_output_dir = Path(output_dir).resolve()
        self.verbose = verbose
        self.repo_name = self._extract_repo_name(source_url)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Structure: output_dir/repo_name_timestamp/
        self.work_dir = self.base_output_dir / f"{self.repo_name}_{self.timestamp}"
        self.bare_repo_path = self.work_dir / "bare_repo.git"
        self.artifacts_dir = self.work_dir / "artifacts"

        # Tools check
        self._check_dependency("git")
        self._check_dependency("git-lfs")

    def _extract_repo_name(self, url):
        path = urlparse(url).path
        name = path.split("/")[-1]
        return name[:-4] if name.endswith(".git") else name

    def _check_dependency(self, tool):
        if not shutil.which(tool):
            print(f"❌ Critical Error: '{tool}' is not installed or not in PATH.")
            sys.exit(1)

    def _run(self, cmd, cwd=None, capture_output=True, ignore_errors=False):
        """Runs a shell command with robust error handling and logging."""
        cwd = cwd or self.work_dir
        if self.verbose:
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

    def _resolve_relative_url(self, parent_url, sub_url):
        """Handles relative submodule URLs (e.g., ../sub.git)."""
        if sub_url.startswith("./") or sub_url.startswith("../"):
            # Ensure parent ends with slash for correct resolution
            if not parent_url.endswith("/"):
                parent_url += "/"
            return urljoin(parent_url, sub_url)
        return sub_url

    def archive(self):
        print(f"📦 Starting Archive for: {self.source_url}")
        print(f"📂 Working Directory: {self.work_dir}")

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 1. Create Bare Mirror
        print("\n🔹 Step 1: Cloning bare repository (Mirror)...")
        self._run(
            ["git", "clone", "--mirror", self.source_url, str(self.bare_repo_path)],
            cwd=self.work_dir,
        )

        # 2. Handle LFS
        print("\n🔹 Step 2: Checking for Git LFS objects...")
        lfs_found = self._handle_lfs(self.bare_repo_path, self.artifacts_dir)

        # 3. Handle Submodules (Recursive)
        print("\n🔹 Step 3: Checking for Submodules (HEAD only)...")
        self._handle_submodules(self.bare_repo_path, self.artifacts_dir)

        # 4. Create Main Bundle
        print("\n🔹 Step 4: Creating Git Bundle...")
        bundle_path = self.artifacts_dir / f"{self.repo_name}.bundle"
        self._run(
            ["git", "bundle", "create", str(bundle_path), "--all"],
            cwd=self.bare_repo_path,
        )

        # 5. Verify Bundle
        print("\n🔹 Step 5: Verifying Bundle Integrity...")
        verify = self._run(
            ["git", "bundle", "verify", str(bundle_path)], cwd=self.bare_repo_path
        )
        print(
            f"   ✅ Bundle Verification:\n{verify.stdout.splitlines()[0]}"
        )  # Print first line (The bundle contains X refs)

        # 6. Final Cleanup & Instructions
        print(f"\n✨ Archive Complete! Artifacts stored in: {self.artifacts_dir}")
        self._write_manifest(lfs_found)

        # Optional: Zip it
        zip_path = shutil.make_archive(self.work_dir, "zip", self.work_dir)
        print(f"🤐 Compressed archive created at: {zip_path}")

    def _handle_lfs(self, repo_path, output_path):
        """Fetches LFS objects and copies them to the artifact folder."""
        # Check if LFS is even used
        # We try to fetch. If the repo doesn't use LFS, this usually returns quickly or warns.
        # --all ensures we get objects for ALL branches/tags.
        print("   Running 'git lfs fetch --all' (this may take time)...")
        _ = self._run(
            ["git", "lfs", "fetch", "--all"], cwd=repo_path, ignore_errors=True
        )

        lfs_objects_dir = repo_path / "lfs" / "objects"
        if lfs_objects_dir.exists() and any(lfs_objects_dir.iterdir()):
            dest = output_path / "lfs-objects"
            print(f"   ⚠️  LFS Objects found! Backing them up to {dest}...")
            shutil.copytree(lfs_objects_dir, dest, dirs_exist_ok=True)
            return True
        else:
            print("   ℹ️  No LFS objects found (or fetch failed/empty).")
            return False

    def _handle_submodules(self, bare_repo_path, artifact_dir):
        """
        Parses .gitmodules from HEAD of the bare repo and bundles them.
        LIMITATION: Only checks HEAD. Submodules only present in other branches are ignored.
        """
        # Read .gitmodules from HEAD
        try:
            # git config -f <(git show HEAD:.gitmodules) --list
            # Using --blob for safer reading
            cmd = ["git", "config", "--blob", "HEAD:.gitmodules", "--list"]
            result = self._run(cmd, cwd=bare_repo_path, ignore_errors=True)

            if result.returncode != 0:
                print("   ℹ️  No .gitmodules found in HEAD.")
                return

            # Parse config output: submodule.name.url=...
            submodules = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    parts = key.split(".")
                    if (
                        len(parts) >= 3
                        and parts[0] == "submodule"
                        and parts[2] == "url"
                    ):
                        sub_name = parts[1]
                        submodules[sub_name] = value

            if not submodules:
                return

            print(
                f"   ⚠️  Found {len(submodules)} submodules in HEAD. Archiving them..."
            )

            sub_artifact_dir = artifact_dir / "submodules"
            sub_artifact_dir.mkdir(exist_ok=True)

            for name, url in submodules.items():
                print(f"      -> Processing submodule '{name}'...")

                # Resolve relative URLs
                full_url = self._resolve_relative_url(self.source_url, url)

                # Recursively archive submodule
                # Note: We do a simplified mirror+bundle here to avoid infinite recursion complexity in this script class
                sub_bare_path = self.work_dir / "sub_temp" / name
                self._run(["git", "clone", "--mirror", full_url, str(sub_bare_path)])

                sub_bundle_path = sub_artifact_dir / f"{name}.bundle"
                self._run(
                    ["git", "bundle", "create", str(sub_bundle_path), "--all"],
                    cwd=sub_bare_path,
                )

                # We do NOT recursively fetch LFS for submodules in this iteration to keep script depth manageable,
                # but you could call _handle_lfs(sub_bare_path, sub_artifact_dir / name + "_lfs") here.

                print(f"         Bundle created: {sub_bundle_path.name}")

        except Exception as e:
            print(f"   ⚠️  Warning: Error processing submodules: {e}")

    def _write_manifest(self, lfs_found):
        """Writes a JSON file with metadata about the archive."""
        manifest = {
            "source_url": self.source_url,
            "archived_at": self.timestamp,
            "contains_lfs": lfs_found,
            "notes": "To restore: 'git clone repo.bundle'. If LFS exists, restore .git/lfs/objects from lfs-objects/ folder.",
        }
        with open(self.artifacts_dir / "archive_manifest.json", "w") as f:
            json.dump(manifest, f, indent=4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a forensic-grade Git archive (Bundle + LFS + Submodules)."
    )
    parser.add_argument("url", help="The remote URL of the Git repository")
    parser.add_argument(
        "--out", default=".", help="Output directory (default: current dir)"
    )

    args = parser.parse_args()

    archiver = GitBundler(args.url, args.out)
    archiver.archive()
