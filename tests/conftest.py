"""Make the test directory importable, so the captured club-name lists in
league_names.py can be shared between test modules."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
