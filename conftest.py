"""Puts the project root on the path, so `services.cleaning`,
`database.job_database`, `backend.api` etc. import regardless of where pytest
is invoked from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
