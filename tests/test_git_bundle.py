import unittest
import sys
from unittest.mock import MagicMock, patch
from pathlib import Path
import importlib.util


# Helper to import the script since it has a hyphen in the filename
def import_git_bundle():
    file_path = Path(__file__).parent.parent / "git-bundle.py"
    spec = importlib.util.spec_from_file_location("git_bundle", str(file_path))
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["git_bundle"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


git_bundle = import_git_bundle()
Utils = git_bundle.Utils
GitArchiver = git_bundle.GitArchiver
GitUnpacker = git_bundle.GitUnpacker


class TestUtils(unittest.TestCase):
    @patch("subprocess.run")
    def test_run_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="success")
        res = Utils.run(["ls"], cwd=".", verbose=False)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "success")

    @patch("subprocess.run")
    def test_run_failure_exit(self, mock_run):
        # valid command but returns error code
        mock_run.side_effect = git_bundle.subprocess.CalledProcessError(
            1, ["ls"], stderr="error"
        )
        with self.assertRaises(SystemExit) as cm:
            Utils.run(["ls"], cwd=".", ignore_errors=False, verbose=False)
        self.assertEqual(cm.exception.code, 1)

    @patch("subprocess.run")
    def test_run_failure_ignore(self, mock_run):
        mock_run.side_effect = git_bundle.subprocess.CalledProcessError(
            1, ["ls"], stderr="error"
        )
        res = Utils.run(["ls"], cwd=".", ignore_errors=True, verbose=False)
        self.assertIsInstance(res, git_bundle.subprocess.CalledProcessError)

    @patch("shutil.which")
    def test_check_dependency_success(self, mock_which):
        mock_which.return_value = "/usr/bin/git"
        Utils.check_dependency("git")  # Should not raise

    @patch("shutil.which")
    def test_check_dependency_failure(self, mock_which):
        mock_which.return_value = None
        with self.assertRaises(SystemExit):
            Utils.check_dependency("missing_tool")


class TestGitArchiverInternals(unittest.TestCase):
    def setUp(self):
        # Mock dependencies in init to avoid actual checks
        with patch.object(Utils, "check_dependency"):
            self.archiver = GitArchiver("https://github.com/user/repo.git", ".")

    def test_extract_repo_name(self):
        extract = self.archiver._extract_repo_name
        self.assertEqual(extract("https://github.com/user/repo.git"), "repo")
        self.assertEqual(extract("https://github.com/user/repo"), "repo")
        self.assertEqual(extract("/path/to/local/my-repo"), "my-repo")
        self.assertEqual(extract("git@github.com:user/project.git"), "project")

    def test_resolve_relative_url(self):
        resolve = self.archiver._resolve_relative_url
        base = "https://github.com/org/parent.git"

        # Absolute
        self.assertEqual(
            resolve(base, "https://other.com/lib.git"), "https://other.com/lib.git"
        )

        # Relative sibling
        # urljoin of "https://github.com/org/parent.git" and "../child.git" logic
        # Python's urljoin on a file-like URL:
        # if base is .../parent.git (no slash), '..' might eat 'parent.git'
        # The implementation adds a slash if missing to parent.
        self.assertEqual(
            resolve(base, "../child.git"), "https://github.com/org/child.git"
        )

        # Relative child
        self.assertEqual(
            resolve(base, "./sub/mod.git"),
            "https://github.com/org/parent.git/sub/mod.git",
        )

    @patch.object(Utils, "run")
    def test_handle_lfs_no_objects(self, mock_run):
        # Mock directory to exist but empty or no LFS dir
        with patch("pathlib.Path.exists", return_value=False):
            result = self.archiver._handle_lfs(Path("mock_path"), Path("out_path"))
            self.assertFalse(result)

    @patch.object(Utils, "run")
    def test_handle_lfs_with_objects(self, mock_run):
        # We need to mock pathlib path navigations
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.iterdir", return_value=[Path("obj1")]),
            patch("shutil.copytree") as mock_copy,
        ):
            result = self.archiver._handle_lfs(Path("mock_repo"), Path("out_path"))
            self.assertTrue(result)
            mock_copy.assert_called()

    @patch.object(Utils, "run")
    @patch("pathlib.Path.mkdir")
    def test_handle_submodules_parsing(self, mock_mkdir, mock_run):
        # Mock git config output
        mock_output = "submodule.foo.path=foo\nsubmodule.foo.url=../foo.git\nsubmodule.bar.path=bar\nsubmodule.bar.url=https://other.com/bar.git"
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

        # Mock sub-clones
        self.archiver._handle_submodules(
            Path("bare_repo"), Path("artifacts"), Path("temp")
        )

        # Verify run was called for cloning submodules
        args_list = [c[0][0] for c in mock_run.call_args_list]
        self.assertIn(
            ["git", "config", "--blob", "HEAD:.gitmodules", "--list"], args_list
        )
        self.assertTrue(
            any("foo.git" in cmd[3] for cmd in args_list if cmd[1] == "clone")
        )
        self.assertTrue(
            any("bar.git" in cmd[3] for cmd in args_list if cmd[1] == "clone")
        )


class TestGitUnpackerInternals(unittest.TestCase):
    def test_init(self):
        unpacker = GitUnpacker("archive.zip")
        self.assertEqual(unpacker.zip_path.name, "archive.zip")
        # Default output dir
        self.assertEqual(unpacker.output_dir.name, "archive")

    @patch("zipfile.ZipFile")
    @patch("shutil.rmtree")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists")
    def test_unpack_missing_zip(self, mock_exists, mock_mkdir, mock_rm, mock_zip):
        mock_exists.return_value = False
        unpacker = GitUnpacker("missing.zip")
        with self.assertRaises(SystemExit):
            unpacker.unpack()

    @patch("zipfile.ZipFile")
    @patch("shutil.rmtree")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.exists")
    def test_unpack_missing_manifest(self, mock_exists, mock_mkdir, mock_rm, mock_zip):
        # exists logic:
        # 1. zip path -> True
        # 2. temp dir -> False (assuming fresh)
        # 3. manifest -> False (TEST CASE)
        # 4. finally: temp dir checked logic -> True (to clear it)
        mock_exists.side_effect = [True, False, False, True]

        # Mock zip extractall to do nothing
        mock_ctx = MagicMock()
        mock_zip.return_value.__enter__.return_value = mock_ctx

        unpacker = GitUnpacker("archive.zip")
        # Redirect stdout to avoid clutter
        with patch("sys.stdout", new_callable=MagicMock):
            with self.assertRaises(SystemExit) as cm:
                unpacker.unpack()
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
