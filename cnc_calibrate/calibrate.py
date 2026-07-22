#!/usr/bin/env python3
"""cnc_calibrate — CLI dispatcher for the calibration + machine utilities.

Subcommands:
  cal-laser      concentric elliptical speed x pass-count laser calibration plate
  ip             print the controller IP (cache, else mDNS scan)
  find-machine   discover Grbl_ESP32 controllers on the LAN

Each subcommand owns its own argparse; everything after the subcommand name is
passed through verbatim (so leading-dash flags survive).
"""

from __future__ import annotations

import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))

USAGE = "usage: calibrate.py {cal-laser|ip|find-machine} [options]"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "cal-laser":
        import spiral_cal

        return spiral_cal.main(rest)
    if cmd in ("ip", "find-machine"):
        try:
            import findmachine
        except ImportError:
            print(
                "find-machine/ip not installed yet (findmachine.py missing).",
                file=sys.stderr,
            )
            return 2
        return findmachine.main([cmd, *rest])
    print(f"unknown command: {cmd}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
