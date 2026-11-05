#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
"""
s7_read_all_tags.py - READ-ONLY: read every tag in IOServerProperties.yaml from the real Siemens S7-1515.

Reads all tags, both directions (r = PLC->PC and w = PC->PLC: a PC->PLC tag can still be read, it shows what the PC
last wrote or what the PLC holds there). Each tag is read on its own and any exception is caught and reported, so
one bad address does not stop the run. Nothing is written to the PLC.

Uses the same client as the Siemens IOServer (PyS7PLCBackend: tag name -> DB address from IOServerProperties.yaml,
byte-read fall-back for bits). The PLC IP is siemens.plc.ip in ApplicationConfiguration.properties unless given.

Run from the project root:
    python costhetaio/siemens/test/s7_read_all_tags.py                  # every tag once
    python costhetaio/siemens/test/s7_read_all_tags.py --ip 192.168.1.10
    python costhetaio/siemens/test/s7_read_all_tags.py --repeat 5 --interval 1.0     # 5 passes, 1 s apart
Exit code: 0 if every tag was read, 1 if any read failed, 2 if the PLC could not be reached.
"""
import argparse
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # <root>/costhetaio/siemens/test
sys.path.insert(0, ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens")

from costhetaio.siemens.plc_backend import PyS7PLCBackend, load_tag_map, parse_address, DEFAULT_PROPERTIES_PATH


def _sort_key(item):
    name, (address, typ, direction) = item
    try:
        db, letter, byte, bit = parse_address(address)
        return (db, byte, bit, name)
    except ValueError:
        return (10 ** 9, 0, 0, name)


def read_all(client, tags):
    """One pass over every tag. Returns (results, failures): results = [(name, address, typ, dir, value or None, error)]."""
    results, failures = [], 0
    for name, (address, typ, direction) in sorted(tags.items(), key=_sort_key):
        try:
            value = client.read(name).value
            results.append((name, address, typ, direction, value, None))
        except Exception as e:
            failures += 1
            results.append((name, address, typ, direction, None, f"{type(e).__name__}: {e}"))
    return results, failures


def print_pass(results, failures, pass_no=None):
    head = f"--- pass {pass_no} " if pass_no is not None else "---"
    print(f"{head}{'-' * (100 - len(head))}")
    print(f"{'Address':<13} {'Type':<5} {'Dir':<7} {'Tag':<46} Value")
    for name, address, typ, direction, value, error in results:
        d = "PLC->PC" if direction == "r" else "PC->PLC" if direction == "w" else direction
        shown = f"{value:.3f}" if isinstance(value, float) else repr(value)
        print(f"{address:<13} {typ:<5} {d:<7} {name:<46} {shown if error is None else 'ERROR  ' + error}")
    print(f"{len(results) - failures}/{len(results)} tags read" + (f", {failures} FAILED" if failures else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read every tag in IOServerProperties.yaml from the Siemens PLC (read-only).")
    ap.add_argument("--ip", help="PLC IP (default: siemens.plc.ip in ApplicationConfiguration.properties)")
    ap.add_argument("--properties", default=DEFAULT_PROPERTIES_PATH, help="tag map (default: IOServerProperties.yaml)")
    ap.add_argument("--repeat", type=int, default=1, help="number of passes (default 1)")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between passes (default 1.0)")
    args = ap.parse_args()

    try:
        plc_cfg, tags = load_tag_map(args.properties)
    except Exception as e:
        print(f"FAIL  tag map {args.properties}: {e}")
        return 2
    ip = args.ip
    if not ip and not plc_cfg.get("address"):
        try:
            from Configuration import CosThetaConfigurator
            ip = CosThetaConfigurator.getInstance().getPlcIP()
        except Exception as e:
            print(f"FAIL  could not read siemens.plc.ip from ApplicationConfiguration.properties: {e}  (use --ip)")
            return 2
    print(f"{len(tags)} tags in {args.properties}")

    try:
        client = PyS7PLCBackend(ip=ip, properties_path=args.properties)
        client.open()
        print(f"Connected to {client.ip} rack {client.rack} slot {client.slot}")
    except Exception as e:
        print(f"FAIL  connect to {ip}: {type(e).__name__}: {e}")
        return 2

    any_failed = False
    try:
        for n in range(1, max(1, args.repeat) + 1):
            if not client.connected:
                try:
                    client.close(); client.open()
                    print(f"Reconnected to {client.ip}")
                except Exception as e:
                    print(f"FAIL  reconnect: {type(e).__name__}: {e}")
                    return 2
            results, failures = read_all(client, tags)
            print_pass(results, failures, n if args.repeat > 1 else None)
            any_failed = any_failed or failures > 0
            if n < args.repeat:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception:
        traceback.print_exc()
        any_failed = True
    finally:
        client.close()
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
