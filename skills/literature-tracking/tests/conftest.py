"""Put the skill's ``scripts/`` directory on ``sys.path``.

The scripts run directly (``python3 scripts/track.py``), which makes
``scripts/`` the import root at runtime. Tests reproduce that.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
