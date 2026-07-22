#!/usr/bin/env python3
"""Network discovery + IP resolution for the CNC controller.

Two subcommands, dispatched on argv[0] (invoked by calibrate.py as
`findmachine.main([cmd, *rest])`):

  ip            print the controller's IP. Prefers a fresh cached value
                (~/.cnc_state.json); on a stale/absent cache it runs an
                mDNS + SSDP scan, caches the hit, and prints it. Falls back
                to a stale cached IP (with a warning) if the scan finds
                nothing, unless --no-cache-fallback is given.
  find-machine  raw Grbl_ESP32 discovery (mDNS + SSDP); prints one
                `IP<TAB>HOSTNAME` per confirmed host, exit 0 if any found.

Both delegate the actual scanning to find_cnc.discover and the cache to
cnc_state — sibling top-level modules in this directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))

import cnc_state  # noqa: E402
import find_cnc  # noqa: E402


def cmd_ip(rest: list[str]) -> int:
    """Print the controller's IP. Prefers cached state; falls back to a scan."""
    p = argparse.ArgumentParser(
        prog="calibrate.py ip",
        description="print the controller's IP (cached if fresh, else mDNS scan)",
    )
    p.add_argument(
        "--max-age-sec",
        type=float,
        default=None,
        help="cache freshness threshold in seconds (default 21600 = 6h)",
    )
    p.add_argument(
        "--discover-timeout",
        type=float,
        default=5.0,
        help="mDNS scan timeout if cache is stale (seconds, default 5)",
    )
    p.add_argument(
        "--no-discover",
        action="store_true",
        help="never scan; only use the cache (exit 1 if cache is missing or stale)",
    )
    p.add_argument(
        "--no-cache-fallback",
        action="store_true",
        help="if a discovery scan fails, do NOT fall back to a stale cached IP",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print cache + scan info to stderr",
    )
    args = p.parse_args(rest)

    max_age = (
        args.max_age_sec
        if args.max_age_sec is not None
        else cnc_state.DEFAULT_FRESHNESS_SEC
    )
    record = cnc_state.get_machine()
    if record and cnc_state.is_fresh(record, max_age_sec=max_age):
        age = record.age_seconds() or 0
        if args.verbose:
            print(
                f"{record.ip}  (cached, last seen {cnc_state.format_age(age)}"
                + (f", MAC {record.mac}" if record.mac else "")
                + ")",
                file=sys.stderr,
            )
        print(record.ip)
        return 0

    # Cache is stale or absent. Try a discovery scan (unless disabled); on a
    # hit, cache it and use it. If discovery fails we fall back to the stale
    # cache below so the user has SOMETHING to try.
    if not args.no_discover:
        if args.verbose:
            print(
                f"no fresh cache; scanning network "
                f"(timeout {args.discover_timeout}s)...",
                file=sys.stderr,
            )
        hits = find_cnc.discover(
            timeout=args.discover_timeout, first_only=True, probe=True
        )
        if hits:
            hit = hits[0]
            try:
                cnc_state.save_machine(hit.ip, hostname=hit.hostname or None)
            except Exception as e:  # noqa: BLE001
                print(f"(cache write failed: {e})", file=sys.stderr)
            print(hit.ip)
            return 0

    if record and not args.no_cache_fallback:
        # Stale cache fallback.
        age = record.age_seconds() or 0
        print(
            f"warning: using stale cache (last seen {cnc_state.format_age(age)}); "
            f"machine may have changed IP",
            file=sys.stderr,
        )
        print(record.ip)
        return 0

    print(
        "error: no machine in cache and discovery found none. "
        "Try inspecting the controller over USB to populate the cache.",
        file=sys.stderr,
    )
    return 1


def cmd_find_machine(rest: list[str]) -> int:
    """Discover Grbl_ESP32 controllers on the LAN via mDNS + SSDP."""
    p = argparse.ArgumentParser(
        prog="calibrate.py find-machine",
        description="discover Grbl_ESP32 controllers on the LAN (mDNS + SSDP)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="scan duration in seconds (default 5)",
    )
    p.add_argument(
        "--first",
        action="store_true",
        help="exit on first confirmed match (for scripting)",
    )
    p.add_argument(
        "--no-probe",
        action="store_true",
        help="skip description.xml fingerprint (faster, noisier)",
    )
    p.add_argument(
        "--cache",
        action="store_true",
        help="write first hit to ~/.cnc_state.json",
    )
    args = p.parse_args(rest)

    print(
        f"scanning mDNS (_http._tcp) and SSDP (upnp:rootdevice) for "
        f"{args.timeout:.1f}s...",
        file=sys.stderr,
    )
    hits = find_cnc.discover(
        timeout=args.timeout,
        first_only=args.first,
        probe=not args.no_probe,
    )

    if not hits:
        print(
            "no Grbl_ESP32 controllers found. Possible causes:\n"
            "  - controller is in AP-fallback mode (failed to join WiFi)\n"
            "  - router has AP isolation / multicast filtering enabled\n"
            "  - controller is off, or USB is connected and rebooting it",
            file=sys.stderr,
        )
        return 1

    if args.cache:
        try:
            cnc_state.save_machine(hits[0].ip, hostname=hits[0].hostname or None)
        except Exception as e:  # noqa: BLE001
            print(f"(cache write failed: {e})", file=sys.stderr)

    for hit in hits:
        print(f"{hit.ip}\t{hit.hostname or '(unknown)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: findmachine.py {ip|find-machine} [options]")
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "ip":
        return cmd_ip(rest)
    if cmd == "find-machine":
        return cmd_find_machine(rest)
    print(
        f"unknown command: {cmd}\nusage: findmachine.py {{ip|find-machine}} [options]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
