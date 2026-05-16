import os
import sys
from pathlib import Path

# v1.43.2 (Codex P1-2): see console/conftest.py for the rationale —
# the production APP_ENV default would trip vault's pair-key check
# at import time during CI test collection.
os.environ.setdefault("APP_ENV", "test")

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent.parent)
