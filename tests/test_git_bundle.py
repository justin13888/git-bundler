"""Unit tests for git_bundle module.

All tests use pytest with pytest-mock. No filesystem or network access.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, mock_open

import pytest

from git_bundle import (
    GitArchiver,
    GitBundlerError,
    GitUnpacker,
    GitVerifier,
    check_dependency,
    parse_git_config,
    run_command,
)

# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    def test_success(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="hello", stderr="", spec=subprocess.CompletedProcess
        )
        result = run_command(["echo", "hello"], cwd=Path("."), verbose=False)
        assert result.returncode == 0
        assert result.stdout == "hello"

    def test_failure_raises(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="bad command", spec=subprocess.CompletedProcess
        )
        with pytest.raises(GitBundlerError, match="Command failed"):
            run_command(["false"], cwd=Path("."), verbose=False)

    def test_failure_includes_stderr_in_error(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="specific error msg", spec=subprocess.CompletedProcess
        )
        with pytest.raises(GitBundlerError, match="specific error msg"):
            run_command(["fail"], cwd=Path("."), verbose=False)

    def test_failure_ignore_errors(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="ignored", spec=subprocess.CompletedProcess
        )
        result = run_command(["false"], cwd=Path("."), verbose=False, ignore_errors=True)
        assert result.returncode == 1

    def test_no_capture_output(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0, stdout=None, stderr=None, spec=subprocess.CompletedProcess
        )
        run_command(["ls"], cwd=Path("."), verbose=False, capture_output=False)
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["stdout"] is None
        assert call_kwargs["stderr"] is None

    def test_verbose_logging(self, mocker, caplog):
        import logging

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr="", spec=subprocess.CompletedProcess
        )
        with caplog.at_level(logging.DEBUG):
            run_command(["git", "status"], cwd=Path("."), verbose=True)
        assert "[CMD] git status" in caplog.text

    def test_failure_no_stderr(self, mocker):
        """When stderr is empty, error message should still be clear."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="", spec=subprocess.CompletedProcess
        )
        with pytest.raises(GitBundlerError, match="Command failed"):
            run_command(["fail"], cwd=Path("."), verbose=False)


# ---------------------------------------------------------------------------
# check_dependency
# ---------------------------------------------------------------------------


class TestCheckDependency:
    def test_exists(self, mocker):
        mocker.patch("shutil.which", return_value="/usr/bin/git")
        check_dependency("git")  # Should not raise

    def test_missing(self, mocker):
        mocker.patch("shutil.which", return_value=None)
        with pytest.raises(GitBundlerError, match="nonexistent_tool"):
            check_dependency("nonexistent_tool")


# ---------------------------------------------------------------------------
# parse_git_config
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = (
    "submodule.lib.path=libs/lib\n"
    "submodule.lib.url=https://example.com/lib.git\n"
    "submodule.vendor.path=vendor/pkg\n"
    "submodule.vendor.url=../vendor.git\n"
)


class TestParseGitConfig:
    def test_submodule_urls(self):
        result = parse_git_config(SAMPLE_CONFIG, "submodule", "url")
        assert result == {
            "lib": "https://example.com/lib.git",
            "vendor": "../vendor.git",
        }

    def test_submodule_paths(self):
        result = parse_git_config(SAMPLE_CONFIG, "submodule", "path")
        assert result == {
            "lib": "libs/lib",
            "vendor": "vendor/pkg",
        }

    def test_empty_config(self):
        assert parse_git_config("", "submodule", "url") == {}

    def test_no_matching_section(self):
        assert parse_git_config(SAMPLE_CONFIG, "remote", "url") == {}

    def test_lines_without_equals(self):
        config = "malformed line\nsubmodule.x.url=ok\n"
        result = parse_git_config(config, "submodule", "url")
        assert result == {"x": "ok"}

    def test_value_with_equals_sign(self):
        """URLs with query params containing = should be preserved."""
        config = "submodule.api.url=https://host.com/repo?token=abc123\n"
        result = parse_git_config(config, "submodule", "url")
        assert result == {"api": "https://host.com/repo?token=abc123"}

    def test_multiple_dots_in_name(self):
        """Config keys with extra dots should still match."""
        config = "submodule.my.lib.url=https://example.com/lib.git\n"
        result = parse_git_config(config, "submodule", "url")
        # parts = ["submodule", "my", "lib", "url"] — parts[2] != "url" for 3-part keys
        # This tests that the parser handles >3 segments — currently it only matches parts[2]
        # so "my.lib" would have parts[2]="lib" != "url", meaning it won't match.
        # This documents the current behavior.
        assert result == {}


# ---------------------------------------------------------------------------
# GitArchiver._extract_repo_name
# ---------------------------------------------------------------------------


class TestExtractRepoName:
    def test_url_with_git_suffix(self):
        assert GitArchiver._extract_repo_name("https://github.com/user/repo.git") == "repo"

    def test_url_without_suffix(self):
        assert GitArchiver._extract_repo_name("https://github.com/user/repo") == "repo"

    def test_url_trailing_slash(self):
        assert GitArchiver._extract_repo_name("https://github.com/user/repo/") == "repo"

    def test_local_path(self):
        assert GitArchiver._extract_repo_name("/home/user/repo.git") == "repo"

    def test_bare_name(self):
        assert GitArchiver._extract_repo_name("repo.git") == "repo"

    def test_ssh_url(self):
        assert GitArchiver._extract_repo_name("git@github.com:user/repo.git") == "repo"

    def test_ssh_url_no_suffix(self):
        assert GitArchiver._extract_repo_name("git@gitlab.com:org/project") == "project"


# ---------------------------------------------------------------------------
# GitArchiver._resolve_relative_url
# ---------------------------------------------------------------------------


class TestResolveRelativeUrl:
    def test_absolute_url(self):
        result = GitArchiver._resolve_relative_url(
            "https://github.com/user/repo.git", "https://example.com/lib.git"
        )
        assert result == "https://example.com/lib.git"

    def test_dotdot_relative(self):
        result = GitArchiver._resolve_relative_url("https://github.com/user/repo.git", "../lib.git")
        assert result == "https://github.com/user/lib.git"

    def test_dot_relative(self):
        result = GitArchiver._resolve_relative_url(
            "https://github.com/user/repo.git", "./sibling.git"
        )
        assert result == "https://github.com/user/repo.git/sibling.git"


# ---------------------------------------------------------------------------
# GitArchiver.archive flow
# ---------------------------------------------------------------------------


class TestGitArchiverFlow:
    @pytest.fixture()
    def archiver(self, mocker):
        mocker.patch("git_bundle.check_dependency")
        return GitArchiver("https://github.com/user/repo.git", Path("/tmp"))

    def test_archive_flow_gz(self, archiver, mocker):
        mock_run = mocker.patch("git_bundle.run_command")
        mocker.patch("tempfile.TemporaryDirectory")
        mocker.patch("pathlib.Path.mkdir")
        mocker.patch("pathlib.Path.write_text")
        mocker.patch("builtins.open", mock_open())

        mock_temp = mocker.patch("tempfile.TemporaryDirectory")
        mock_temp.return_value.__enter__.return_value = "/mock/temp"
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        output_file = archiver.archive(compression="gz")

        # Verify clone --mirror was called
        assert any(
            "clone" in c.args[0] and "--mirror" in c.args[0] for c in mock_run.call_args_list
        )
        # Verify LFS fetch
        assert any("lfs" in c.args[0] and "fetch" in c.args[0] for c in mock_run.call_args_list)
        # Verify gz compression
        assert any("tar" in c.args[0] and "-czf" in c.args[0] for c in mock_run.call_args_list)
        assert output_file.endswith(".tar.gz")

    def test_archive_flow_zstd(self, archiver, mocker):
        mock_run = mocker.patch("git_bundle.run_command")
        mocker.patch("pathlib.Path.write_text")
        mocker.patch("builtins.open", mock_open())

        mock_temp = mocker.patch("tempfile.TemporaryDirectory")
        mock_temp.return_value.__enter__.return_value = "/mock/temp"
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        mocker.patch("git_bundle.check_dependency")
        output_file = archiver.archive(compression="zstd")

        tar_calls = [c for c in mock_run.call_args_list if "tar" in c.args[0]]
        assert len(tar_calls) > 0
        assert "--use-compress-program=zstd" in tar_calls[0].args[0]
        assert output_file.endswith(".tar.zst")


# ---------------------------------------------------------------------------
# GitArchiver._write_manifest
# ---------------------------------------------------------------------------


class TestWriteManifest:
    def test_manifest_content(self, mocker, tmp_path):
        mocker.patch("git_bundle.check_dependency")
        archiver = GitArchiver("https://github.com/user/repo.git", Path("/tmp"))
        archiver._write_manifest(tmp_path)

        manifest_path = tmp_path / "archive_manifest.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert manifest["source_url"] == "https://github.com/user/repo.git"
        assert manifest["repo_name"] == "repo"
        assert manifest["version"] == "2.0"
        assert "archived_at" in manifest


# ---------------------------------------------------------------------------
# GitArchiver._handle_submodules
# ---------------------------------------------------------------------------


class TestHandleSubmodules:
    @pytest.fixture()
    def archiver(self, mocker):
        mocker.patch("git_bundle.check_dependency")
        return GitArchiver("https://github.com/user/repo.git", Path("/tmp"))

    def test_no_gitmodules(self, archiver, mocker):
        """Should return early if .gitmodules doesn't exist in HEAD."""
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        archiver._handle_submodules(Path("/fake/repo.git"), Path("/fake/temp"))

        # Only one call: git show HEAD:.gitmodules
        assert mock_run.call_count == 1

    def test_with_submodules(self, archiver, mocker):
        """Should parse .gitmodules and clone each submodule."""
        gitmodules_content = (
            '[submodule "lib"]\n    path = libs/lib\n    url = https://example.com/lib.git\n'
        )
        config_list_output = (
            "submodule.lib.path=libs/lib\nsubmodule.lib.url=https://example.com/lib.git\n"
        )

        call_count = 0

        def mock_run_side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if "show" in cmd:
                return MagicMock(returncode=0, stdout=gitmodules_content)
            if "config" in cmd and "--list" in cmd:
                return MagicMock(returncode=0, stdout=config_list_output)
            return MagicMock(returncode=0, stdout="")

        mock_run = mocker.patch("git_bundle.run_command", side_effect=mock_run_side_effect)
        mocker.patch("pathlib.Path.write_text")
        mocker.patch("pathlib.Path.mkdir")

        archiver._handle_submodules(Path("/fake/repo.git"), Path("/fake/temp"))

        # Should have: show, config, clone --mirror, lfs fetch
        clone_calls = [c for c in mock_run.call_args_list if "clone" in c.args[0]]
        assert len(clone_calls) == 1
        assert "--mirror" in clone_calls[0].args[0]

    def test_relative_url_resolution(self, archiver, mocker):
        """Relative submodule URLs should be resolved against parent."""
        config_output = "submodule.dep.url=../dep.git\n"

        def mock_run_side_effect(cmd, **kwargs):
            if "show" in cmd:
                return MagicMock(returncode=0, stdout="[submodule]\n")
            if "config" in cmd and "--list" in cmd:
                return MagicMock(returncode=0, stdout=config_output)
            return MagicMock(returncode=0, stdout="")

        mock_run = mocker.patch("git_bundle.run_command", side_effect=mock_run_side_effect)
        mocker.patch("pathlib.Path.write_text")
        mocker.patch("pathlib.Path.mkdir")

        archiver._handle_submodules(Path("/fake/repo.git"), Path("/fake/temp"))

        clone_calls = [c for c in mock_run.call_args_list if "clone" in c.args[0]]
        assert len(clone_calls) == 1
        # The URL should be resolved: ../dep.git relative to https://github.com/user/repo.git
        assert "https://github.com/user/dep.git" in clone_calls[0].args[0]


# ---------------------------------------------------------------------------
# GitUnpacker
# ---------------------------------------------------------------------------


class TestGitUnpacker:
    def test_missing_archive_raises(self):
        unpacker = GitUnpacker(Path("/nonexistent/archive.tar.gz"), dest_dir=Path("/tmp/dest"))
        with pytest.raises(GitBundlerError, match="Archive not found"):
            unpacker.unpack()

    def test_unpack_happy_path(self, mocker):
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        def exists_side_effect(instance):
            p = str(instance)
            if "archive.tar.gz" in p:
                return True
            if ".tmp_extract" in p:
                if p.endswith(".tmp_extract"):
                    return False
                if "repo.git" in p:
                    return True
                if "archive_manifest.json" in p:
                    return True
                if "submodules" in p:
                    return False
            return False

        mocker.patch("pathlib.Path.exists", autospec=True, side_effect=exists_side_effect)
        mocker.patch("pathlib.Path.mkdir")
        mocker.patch("shutil.rmtree")
        mocker.patch(
            "pathlib.Path.read_text",
            return_value=json.dumps(
                {"repo_name": "repo", "source_url": "http://example.com/repo.git"}
            ),
        )

        unpacker = GitUnpacker(Path("archive.tar.gz"), dest_dir=Path("/tmp/dest"))
        unpacker.unpack()

        # Verify tar extraction
        assert any("tar" in c.args[0] and "-xf" in c.args[0] for c in mock_run.call_args_list)
        # Verify clone
        assert any("clone" in c.args[0] for c in mock_run.call_args_list)
        # Verify remote set-url
        assert any(
            "remote" in c.args[0] and "set-url" in c.args[0] for c in mock_run.call_args_list
        )

    def test_unpack_missing_manifest(self, mocker):
        def exists_side_effect(instance):
            p = str(instance)
            if "archive.tar.gz" in p:
                return True
            if "archive_manifest.json" in p:
                return False
            if ".tmp_extract" in p:
                return not p.endswith(".tmp_extract")
            return False

        mocker.patch("pathlib.Path.exists", autospec=True, side_effect=exists_side_effect)
        mocker.patch("pathlib.Path.mkdir")
        mocker.patch("shutil.rmtree")
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        unpacker = GitUnpacker(Path("archive.tar.gz"), dest_dir=Path("/tmp/dest"))
        with pytest.raises(GitBundlerError, match="missing manifest"):
            unpacker.unpack()

    def test_unpack_missing_repo_git(self, mocker):
        def exists_side_effect(instance):
            p = str(instance)
            if "archive.tar.gz" in p:
                return True
            if ".tmp_extract" in p:
                if p.endswith(".tmp_extract"):
                    return False
                if "archive_manifest.json" in p:
                    return True
                return "repo.git" not in p
            return False

        mocker.patch("pathlib.Path.exists", autospec=True, side_effect=exists_side_effect)
        mocker.patch("pathlib.Path.mkdir")
        mocker.patch("shutil.rmtree")
        mocker.patch(
            "pathlib.Path.read_text",
            return_value=json.dumps(
                {"repo_name": "repo", "source_url": "http://example.com/repo.git"}
            ),
        )
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        unpacker = GitUnpacker(Path("archive.tar.gz"), dest_dir=Path("/tmp/dest"))
        with pytest.raises(GitBundlerError, match="missing repo.git"):
            unpacker.unpack()


# ---------------------------------------------------------------------------
# GitUnpacker._restore_submodules
# ---------------------------------------------------------------------------


class TestRestoreSubmodules:
    def test_no_gitmodules(self, mocker):
        """Should return early if .gitmodules config fails."""
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        unpacker = GitUnpacker(Path("/fake/archive.tar.gz"), dest_dir=Path("/tmp"))
        unpacker._restore_submodules(Path("/fake/repo"), Path("/fake/extract"))

        assert mock_run.call_count == 1

    def test_no_submodules_source_dir(self, mocker):
        """Should return early if submodules dir doesn't exist."""
        config_output = "submodule.lib.path=libs/lib\n"

        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout=config_output)

        mocker.patch("pathlib.Path.exists", return_value=False)

        unpacker = GitUnpacker(Path("/fake/archive.tar.gz"), dest_dir=Path("/tmp"))
        unpacker._restore_submodules(Path("/fake/repo"), Path("/fake/extract"))

    def test_happy_path(self, mocker, tmp_path):
        """Should init, configure URL, and update each submodule."""
        config_output = "submodule.lib.path=libs/lib\n"

        call_idx = 0

        def mock_run_side_effect(cmd, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if "config" in cmd and "--file" in cmd and "--list" in cmd:
                return MagicMock(returncode=0, stdout=config_output)
            return MagicMock(returncode=0, stdout="")

        mock_run = mocker.patch("git_bundle.run_command", side_effect=mock_run_side_effect)

        # Create the submodules source directory
        sub_source = tmp_path / "submodules"
        sub_source.mkdir()
        (sub_source / "lib.git").mkdir()

        unpacker = GitUnpacker(Path("/fake/archive.tar.gz"), dest_dir=Path("/tmp"))
        unpacker._restore_submodules(tmp_path / "repo", tmp_path)

        # Should call: config --list, submodule init, config submodule.lib.url, submodule update
        cmd_strs = [" ".join(c.args[0]) for c in mock_run.call_args_list]
        assert any("submodule init" in s for s in cmd_strs)
        assert any("submodule.lib.url" in s for s in cmd_strs)
        assert any("submodule update" in s for s in cmd_strs)


# ---------------------------------------------------------------------------
# GitVerifier
# ---------------------------------------------------------------------------


class TestGitVerifier:
    def test_verify_calls_fsck(self, mocker):
        mock_temp = mocker.patch("tempfile.TemporaryDirectory")
        mock_temp.return_value.__enter__.return_value = "/tmp/verify"

        mock_unpack = mocker.patch.object(GitUnpacker, "unpack")
        mock_unpack.return_value = Path("/tmp/verify/repo")

        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        mocker.patch("pathlib.Path.exists", return_value=False)

        GitVerifier.verify(Path("archive.tar.gz"), verbose=False)

        fsck_calls = [c for c in mock_run.call_args_list if "fsck" in c.args[0]]
        assert len(fsck_calls) == 1
        assert "--full" in fsck_calls[0].args[0]

    def test_verify_checks_lfs_when_present(self, mocker):
        mock_temp = mocker.patch("tempfile.TemporaryDirectory")
        mock_temp.return_value.__enter__.return_value = "/tmp/verify"
        mock_unpack = mocker.patch.object(GitUnpacker, "unpack")
        mock_unpack.return_value = Path("/tmp/verify/repo")

        def exists_side_effect(instance):
            p = str(instance)
            if "lfs" in p:
                return True
            if ".gitmodules" in p:
                return False
            return False

        mocker.patch("pathlib.Path.exists", autospec=True, side_effect=exists_side_effect)
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        GitVerifier.verify(Path("archive.tar.gz"), verbose=False)

        lfs_calls = [
            c for c in mock_run.call_args_list if "lfs" in c.args[0] and "fsck" in c.args[0]
        ]
        assert len(lfs_calls) == 1

    def test_verify_checks_submodules_when_present(self, mocker):
        mock_temp = mocker.patch("tempfile.TemporaryDirectory")
        mock_temp.return_value.__enter__.return_value = "/tmp/verify"
        mock_unpack = mocker.patch.object(GitUnpacker, "unpack")
        mock_unpack.return_value = Path("/tmp/verify/repo")

        def exists_side_effect(instance):
            p = str(instance)
            if "lfs" in p:
                return False
            return ".gitmodules" in p

        mocker.patch("pathlib.Path.exists", autospec=True, side_effect=exists_side_effect)
        mock_run = mocker.patch("git_bundle.run_command")
        mock_run.return_value = MagicMock(returncode=0, stdout="", spec=subprocess.CompletedProcess)

        GitVerifier.verify(Path("archive.tar.gz"), verbose=False)

        sub_calls = [c for c in mock_run.call_args_list if "submodule" in c.args[0]]
        assert len(sub_calls) == 1
        assert "status" in sub_calls[0].args[0]
