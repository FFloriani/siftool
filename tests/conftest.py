"""pytest configuration for siftool tests."""
import sys
from pathlib import Path

# Ensure src/siftool package takes precedence over root siftool.py
project_root = Path(__file__).parent.parent
src_path = project_root / "src"

# Remove root directory if it's in sys.path to avoid shadowing the installed package
try:
    sys.path.remove(str(project_root))
except ValueError:
    pass

# Add src to path if not already there
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
