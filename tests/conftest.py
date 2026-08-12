"""Test configuration.

server.py deliberately fails at import time if AEM credentials are missing.
That is the right behaviour for a server, but it means tests must supply
credentials before importing it. No AEM instance is contacted: every test
here exercises pure functions.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("AEM_USER", "test")
os.environ.setdefault("AEM_PASS", "test")

# Make server.py importable when pytest is run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
