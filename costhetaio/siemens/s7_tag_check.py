#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
"""
s7_tag_check.py - READ-ONLY commissioning check of every IOServer tag on the Siemens S7-1515.

Uses exactly what the Siemens IOServer uses (PyS7PLCBackend + IOServerProperties.yaml + the siemens.* tag names):
  1. checks that every tag the IOServer uses has a valid, non-clashing DB address;
  2. connects to the PLC (siemens.plc.ip) and reads each tag once, printing name, address and value;
  3. checks the watch tag reads 1234.
Nothing is written to the PLC.

Run from the project root:   python costhetaio/siemens/s7_tag_check.py
Exit code 0 if everything was read and the watch tag is 1234, 1 otherwise.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens")

from Configuration import CosThetaConfigurator
from costhetaio.siemens.plc_backend import PyS7PLCBackend, load_tag_map, DEFAULT_PROPERTIES_PATH


def main() -> int:
    ok = True
    _, tags = load_tag_map(DEFAULT_PROPERTIES_PATH)          # raises on bad / clashing addresses
    import costhetaio.siemens.IOServer as S                  # tag names the IOServer uses
    used = S.tagNamesUsedByIOServer()
    missing = sorted(n for n in used.values() if n not in tags)
    if missing:
        print("FAIL  no address for:", ", ".join(missing))
        return 1
    print(f"PASS  {len(used)} IOServer tags, all with addresses in {os.path.basename(DEFAULT_PROPERTIES_PATH)}")

    client = PyS7PLCBackend(ip=CosThetaConfigurator.getInstance().getPlcIP())
    try:
        client.open()
        print(f"PASS  connected to {client.ip} rack {client.rack} slot {client.slot}")
    except Exception as e:
        print(f"FAIL  connect: {e}")
        return 1
    try:
        for attr, name in sorted(used.items(), key=lambda kv: tags[kv[1]][0]):
            address, typ, direction = tags[name]
            try:
                value = client.read(name).value
                print(f"      {address:<14} {typ:<5} {'PLC->PC' if direction == 'r' else 'PC->PLC'}  {name:<46} = {value!r}")
            except Exception as e:
                ok = False
                print(f"FAIL  {address:<14} {name}: {e}")
        watch = client.read(CosThetaConfigurator.getInstance().getPlcPcWatchTagName()).value
        if watch == 1234:
            print("PASS  watch tag = 1234")
        else:
            ok = False
            print(f"FAIL  watch tag = {watch!r} (the IOServer expects 1234 and reconnects otherwise)")
    finally:
        client.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
