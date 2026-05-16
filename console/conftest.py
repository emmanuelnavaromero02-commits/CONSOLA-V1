import os
import sys
from pathlib import Path

# v1.43.2 (Codex P1-2): production code now defaults APP_ENV to
# ``production`` so unset envs fail closed. Test runs explicitly
# opt in to ``test`` mode — mirrors the compose-file pattern.
# Without this, importing app.services.auth (which runs
# _require_pair_keys_in_production at module-load) trips the
# production check and fails collection in CI.
os.environ.setdefault("APP_ENV", "test")

# Make `from app.xxx` work (tests import from the console package)
sys.path.insert(0, str(Path(__file__).parent))

# Static tests use paths like Path("console/app/main.py") — resolve from project root
os.chdir(Path(__file__).parent.parent)
