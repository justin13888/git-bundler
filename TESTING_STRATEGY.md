# Testing Strategy for Git Bundler

This document outlines the plan to test the internal logic of `git-bundle.py`, ensuring robustness across various edge cases without relying on external network resources or heavy filesystem operations.

## 1. Test Architecture

We will use Python's built-in `unittest` framework.
Tests will be placed in a new directory `tests/` to keep the project clean.

- **`tests/test_internals.py`**: Focuses on pure logic and method-level unit tests.
- **`tests/test_flow.py`**: Focuses on the orchestration logic (`archive`, `unpack`) using extensive mocking.

## 2. Component Testing Matrix

### A. `Utils` Class

| Component          | Test Case                               | Expected Behavior                              |
| ------------------ | --------------------------------------- | ---------------------------------------------- |
| `check_dependency` | Tool exists                             | Returns silently                               |
| `check_dependency` | Tool missing                            | Exits with status 1                            |
| `run`              | Command success                         | Returns `CompletedProcess` with stdout         |
| `run`              | Command failure (`ignore_errors=False`) | Exits with status 1                            |
| `run`              | Command failure (`ignore_errors=True`)  | Returns error object/result with non-zero code |
| `run`              | `capture_output=False`                  | Passthrough, returns result                    |

### B. `GitArchiver` Class

| Component               | Test Case           | Expected Behavior                                         |
| ----------------------- | ------------------- | --------------------------------------------------------- |
| `_extract_repo_name`    | URL ends in `.git`  | Strips suffix                                             |
| `_extract_repo_name`    | URL no `.git`       | returns path tail                                         |
| `_extract_repo_name`    | Trailing slash      | Handles gracefully                                        |
| `_extract_repo_name`    | Local path          | Extracts directory name                                   |
| `_resolve_relative_url` | Absolute URL        | Returns as is                                             |
| `_resolve_relative_url` | Relative (`../`)    | Resolves against parent URL                               |
| `_resolve_relative_url` | Relative (`./`)     | Resolves against parent URL                               |
| `_handle_submodules`    | Valid `.gitmodules` | Parses names and URLs correctly                           |
| `_handle_submodules`    | No submodules       | Returns gracefully                                        |
| `_handle_submodules`    | Malformed config    | Survives parsing                                          |
| `_handle_lfs`           | LFS objects present | Copies directory, returns True                            |
| `_handle_lfs`           | No LFS objects      | Returns False                                             |
| `archive`               | Logic Flow          | Calls clone -> lfs -> submodule -> bundle -> zip in order |

### C. `GitUnpacker` Class

| Component | Test Case              | Expected Behavior                              |
| --------- | ---------------------- | ---------------------------------------------- |
| `unpack`  | Zip missing            | Exits with error                               |
| `unpack`  | Missing Manifest       | Exits with error                               |
| `unpack`  | Missing `.bundle` file | Exits with error                               |
| `unpack`  | LFS restoration        | Checks manifest, copies LFS objects if flagged |
| `unpack`  | Submodule restoration  | Parses config, initiates local bundle update   |

## 3. Mocking Strategy

To verify "internals used on all possible CLI inputs" without actual execution:

1.  **Mock `subprocess.run` (`Utils.run`)**:
    We will patch `git_bundle.Utils.run` (or `subprocess.run` if we test Utils separately).
    *   *Scenario 1*: Simulate `git clone` success.
    *   *Scenario 2*: Simulate `git config --list` returning specific submodule configurations (some valid, some weird).
    *   *Scenario 3*: Simulate `git bundle create` failures.

2.  **Mock Filesystem**:
    *   Use `tempfile.TemporaryDirectory` (already in code) but mocked to verify files are written to it.
    *   Mock `pathlib.Path.exists`, `glob`, and `open` where necessary to simulate missing files or specific manifest contents.

## 4. Proposed New Files

*   `tests/__init__.py`: (Empty)
*   `tests/test_git_bundle.py`: The implementation of the above plan.

## 5. Next Steps

1.  Confirm this plan meets the "thoroughly testing internals" requirement.
2.  Create `tests/` directory.
3.  Write the test suite.
4.  Run tests and report coverage/issues.
