#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
"""
s7_smoke_test.py
================
Self-contained connectivity + read/write test for a Siemens S7-1500 (e.g. 1515)
using the pyS7 library. No external config files: every setting lives in the
CONFIG block below.

INSTALL
-------
    pip install git+https://github.com/xtimmy86x/pyS7

PLC-SIDE PREREQUISITES (the test will fail without these)
--------------------------------------------------------
  1. CPU -> Protection & Security -> permit PUT/GET from remote partner.
  2. The test DB must have "Optimized block access" UNCHECKED, so absolute
     offsets exist.
  3. Use a DEDICATED SCRATCH DB with NO ladder logic reading it. Writing to a
     live interface DB can move the machine. This script writes real values.

USAGE
-----
    python costhetaio/siemens/test/s7_smoke_test.py            # reads only (writes disabled by default)
    # set ALLOW_WRITES = True below to enable the write-and-verify tests
    # set PLC_IP = "MOCK" to run the whole suite against an in-memory fake PLC

Exit code is 0 if every executed check passed, 1 otherwise.
"""

from __future__ import annotations

import math
import re
import sys
import time
from typing import Any, List, Optional, Tuple

# ============================ CONFIG — EDIT THESE ============================

PLC_IP: str = "192.168.1.10"   # PLC IP, or "MOCK" for an offline dry run
# PLC_IP: str = "MOCK"   # PLC IP, or "MOCK" for an offline dry run
RACK: int = 0                  # S7-1500 family: rack 0
SLOT: int = 1                  # S7-1500 family: slot 1
PDU_SIZE: int = 480            # try 480 or 960 on a 1500 if the CPU allows
TIMEOUT: float = 5.0           # socket timeout, seconds
CONNECT_RETRIES: int = 3       # connection attempts before giving up

# Scratch addresses in a NON-OPTIMIZED test DB you can safely clobber.
# Letters: X = BIT (bool), R = REAL (float), I = INT (16-bit), DI = DINT (32-bit).
# NOTE: I6 is a 16-bit INT (2 bytes, bytes 6-7). The PLC-side DB element at
# byte 6 must be declared "Int" (not "DInt"), or the read returns wrong data.
BOOL_ADDR: str = "DB100,X0.0"
FLOAT_ADDR: str = "DB100,R2"
INT_ADDR: str = "DB100,I6"

# Values written during the write-and-verify test.
BOOL_TEST_VALUE: bool = True
FLOAT_TEST_VALUE: float = 123.456
INT_TEST_VALUE: int = 4242

FLOAT_TOLERANCE: float = 1e-3  # REAL is 32-bit; compare with tolerance

# Safety gates.
ALLOW_WRITES: bool = True        # must be True to run any write test
RESTORE_AFTER_WRITE: bool = False  # read original, write test value, restore original

# ============================================================================

# ---- pyS7 import (with a friendly message if it's missing) -----------------
if PLC_IP != "MOCK":
    try:
        from pyS7 import S7Client
    except ImportError:
        sys.exit(
            "pyS7 is not installed. Run:\n"
            "    pip install git+https://github.com/xtimmy86x/pyS7"
        )
    # extract_bit_from_byte may not exist on every version — provide a fallback.
    try:
        from pyS7 import extract_bit_from_byte  # type: ignore
    except Exception:
        def extract_bit_from_byte(byte_value: int, bit_offset: int) -> bool:
            return bool((byte_value >> bit_offset) & 1)
else:
    def extract_bit_from_byte(byte_value: int, bit_offset: int) -> bool:
        return bool((byte_value >> bit_offset) & 1)


# ---- console helpers (no external color dependency) ------------------------

_PASS, _FAIL, _INFO, _WARN = "PASS", "FAIL", "INFO", "WARN"

def _log(kind: str, msg: str) -> None:
    print(f"[{kind}] {msg}", flush=True)

class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
    def record(self, ok: bool, label: str, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            _log(_PASS, f"{label} {detail}".rstrip())
        else:
            self.failed += 1
            _log(_FAIL, f"{label} {detail}".rstrip())
        return ok


# ---- address helper: byte-read fallback for single-bit reads ---------------

def _byte_addr_for_bit(bit_addr: str) -> Tuple[str, int]:
    """'DB100,X3.5' -> ('DB100,B3', 5). Raises ValueError if not a bit address."""
    m = re.match(r"^(DB\d+),X(\d+)\.([0-7])$", bit_addr.strip())
    if not m:
        raise ValueError(f"not a DB bit address: {bit_addr}")
    db, byte, bit = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{db},B{byte}", bit


def safe_read_one(client: Any, addr: str) -> Any:
    """Read a single tag; on INVALID_DATA_SIZE for a bit, fall back to byte+mask."""
    try:
        return client.read([addr])[0]
    except Exception as e:
        if "INVALID_DATA_SIZE" in str(e) and ",X" in addr:
            byte_addr, bit = _byte_addr_for_bit(addr)
            byte_val = client.read([byte_addr])[0]
            return extract_bit_from_byte(int(byte_val), bit)
        raise


# ---- connection ------------------------------------------------------------

def connect(res: Results) -> Optional[Any]:
    if PLC_IP == "MOCK":
        _log(_INFO, "MOCK mode: using in-memory fake PLC (no network)")
        c = MockS7Client()
        c.connect()
        res.record(c.is_connected, "connect", "(mock)")
        return c

    # Build kwargs defensively: not every pyS7 version accepts pdu_size/timeout.
    kwargs = dict(address=PLC_IP, rack=RACK, slot=SLOT)
    for opt_key, opt_val in (("pdu_size", PDU_SIZE), ("timeout", TIMEOUT)):
        kwargs[opt_key] = opt_val

    last_err: Optional[Exception] = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            try:
                client = S7Client(**kwargs)
            except TypeError:
                # older signature: fall back to the minimal constructor
                client = S7Client(address=PLC_IP, rack=RACK, slot=SLOT)
            client.connect()
            if client.is_connected:
                res.record(True, "connect", f"to {PLC_IP} rack {RACK} slot {SLOT} (attempt {attempt})")
                return client
        except Exception as e:
            last_err = e
            _log(_WARN, f"connect attempt {attempt}/{CONNECT_RETRIES} failed: {e}")
            time.sleep(min(2 ** (attempt - 1), 5))

    res.record(False, "connect", f"to {PLC_IP}: {last_err}")
    return None


def probe_cpu(client: Any, res: Results) -> Optional[str]:
    """Read CPU status/info. Status uses the SZL path and works even with PUT/GET off,
    so it helps separate 'no network' from 'PUT/GET disabled or DB optimized'."""
    status = None
    try:
        status = client.get_cpu_status()
        res.record(status in ("RUN", "STOP"), "cpu_status", f"= {status}")
        if status == "STOP":
            _log(_WARN, "CPU is in STOP — writes may be rejected or ineffective")
    except Exception as e:
        res.record(False, "cpu_status", f"error: {e}")
    try:
        info = client.get_cpu_info()
        _log(_INFO, f"CPU model={info.get('module_type_name')} "
                    f"fw={info.get('firmware_version')} hw={info.get('hardware_version')}")
    except Exception as e:
        _log(_WARN, f"cpu_info unavailable: {e}")
    return status


# ---- read tests ------------------------------------------------------------

def test_reads(client: Any, res: Results) -> None:
    for label, addr, pytype in (
        ("read_bool",  BOOL_ADDR,  bool),
        ("read_float", FLOAT_ADDR, float),
        ("read_int",   INT_ADDR,   int),
    ):
        try:
            val = safe_read_one(client, addr)
            ok = val is not None and isinstance(val, pytype if pytype is not float else (int, float))
            res.record(ok, label, f"{addr} -> {val!r}")
        except Exception as e:
            res.record(False, label, f"{addr} error: {e}")


# ---- write-and-verify tests ------------------------------------------------

def _values_match(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=FLOAT_TOLERANCE)
    return a == b


def test_write_verify(client: Any, addr: str, test_value: Any, label: str, res: Results) -> None:
    """Read original, write test value, read back and compare, then restore original."""
    if not ALLOW_WRITES:
        _log(_INFO, f"{label} skipped (ALLOW_WRITES is False)")
        return

    # capture original so the test is non-destructive
    original: Any = None
    have_original = False
    try:
        original = safe_read_one(client, addr)
        have_original = True
    except Exception as e:
        _log(_WARN, f"{label}: could not read original at {addr} ({e}); restore disabled")

    try:
        client.write([addr], [test_value])
        readback = safe_read_one(client, addr)
        ok = _values_match(readback, test_value)
        res.record(ok, label, f"{addr}: wrote {test_value!r}, read {readback!r}")
    except Exception as e:
        res.record(False, label, f"{addr} error: {e}")
    finally:
        if RESTORE_AFTER_WRITE and have_original:
            try:
                client.write([addr], [original])
                _log(_INFO, f"{label}: restored original {original!r} at {addr}")
            except Exception as e:
                _log(_WARN, f"{label}: FAILED to restore original at {addr}: {e}")


# ---- optional metrics dump -------------------------------------------------

def dump_metrics(client: Any) -> None:
    try:
        m = client.metrics
        _log(_INFO, f"metrics: ops={m.total_operations} success={m.success_rate}% "
                    f"avg_read={m.avg_read_duration*1000:.1f}ms uptime={m.connection_uptime:.1f}s")
    except Exception:
        pass  # metrics are a bonus, never a failure


# ---- minimal in-memory fake PLC for MOCK mode ------------------------------

class MockS7Client:
    """Tiny stand-in so the harness can run with no hardware. Supports the exact
    surface this script uses: connect/disconnect/is_connected/read/write/get_cpu_*."""
    def __init__(self, *_a: Any, **_k: Any) -> None:
        self._connected = False
        self._mem: dict = {BOOL_ADDR: False, FLOAT_ADDR: 0.0, INT_ADDR: 0}
    def connect(self) -> None:
        self._connected = True
    def disconnect(self) -> None:
        self._connected = False
    @property
    def is_connected(self) -> bool:
        return self._connected
    def read(self, addrs: List[str]) -> List[Any]:
        return [self._mem.get(a, 0) for a in addrs]
    def write(self, addrs: List[str], vals: List[Any]) -> None:
        for a, v in zip(addrs, vals):
            self._mem[a] = v
    def get_cpu_status(self) -> str:
        return "RUN"
    def get_cpu_info(self) -> dict:
        return {"module_type_name": "MOCK-1515", "firmware_version": "N/A", "hardware_version": "N/A"}


# ---- runner ----------------------------------------------------------------

def main() -> int:
    print("=" * 68)
    print(" pyS7 S7-1500 smoke test")
    print("=" * 68)
    if ALLOW_WRITES and PLC_IP != "MOCK":
        _log(_WARN, "ALLOW_WRITES is TRUE — this will write to the PLC. "
                    "Ensure the target DB is a scratch DB with no ladder logic.")

    res = Results()
    client = connect(res)
    if client is None:
        _log(_INFO, "aborting: no connection")
        _summary(res)
        return 1

    BOOL_NEW_VALUE : bool = False
    FLOAT_NEW_VALUE : float = 456.123
    INT_NEW_VALUE : int = 8484
    try:
        probe_cpu(client, res)
        time.sleep(2)
        test_reads(client, res)
        time.sleep(2)
        test_write_verify(client, BOOL_ADDR,  BOOL_NEW_VALUE,  "write_verify_bool",  res)
        time.sleep(2)
        test_write_verify(client, FLOAT_ADDR, FLOAT_NEW_VALUE, "write_verify_float", res)
        time.sleep(2)
        test_write_verify(client, INT_ADDR,   INT_NEW_VALUE,   "write_verify_int",   res)
        time.sleep(2)
        dump_metrics(client)
    finally:
        try:
            client.disconnect()
            _log(_INFO, "disconnected")
        except Exception:
            pass

    return _summary(res)


def _summary(res: Results) -> int:
    print("-" * 68)
    total = res.passed + res.failed
    print(f" SUMMARY: {res.passed}/{total} passed, {res.failed} failed")
    print("=" * 68)
    return 0 if res.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
