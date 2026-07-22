"""Make the cnc_calibrate package dir importable as top-level modules.

The calibrate.py dispatcher puts its own directory on sys.path at runtime
(so `import find_cnc` / `import cnc_state` / `import findmachine` resolve).
Tests need the same shim since they import those modules directly.
"""

import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))
