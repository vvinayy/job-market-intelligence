"""Makes the project root importable as `cleaning`, `job_database`, etc.
regardless of where pytest is invoked from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
