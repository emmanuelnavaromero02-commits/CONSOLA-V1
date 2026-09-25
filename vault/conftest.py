import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent.parent)
