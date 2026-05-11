import os
import sys
from pathlib import Path

# Make `from app.xxx` work (tests import from the console package)
sys.path.insert(0, str(Path(__file__).parent))

# Static tests use paths like Path("console/app/main.py") — resolve from project root
os.chdir(Path(__file__).parent.parent)
