"""Shared fixtures and path configuration for tests."""

import sys
from pathlib import Path

# Add project root to sys.path so `import git_bundle` works
sys.path.insert(0, str(Path(__file__).parent.parent))
