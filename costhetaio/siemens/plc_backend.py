# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
pyS7 layer for the Siemens S7-1515: the tag map and one S7 connection.

SiemensPLCDataIO (SiemensPLCDataIO.py), the IOServer's PLCDataIO for Siemens, is built on PyS7PLCBackend; the
command-line tools (s7_tag_check.py, s7_read_all_tags.py) use it directly. PyS7PLCBackend offers
    open(), close(), .connected, read(tag) -> object with .value, read(tag1, tag2, ...) -> list, write(tag, value)
with SYMBOLIC tag names. Each name is resolved to its absolute DB address through IOServerProperties.yaml.

The pyS7 calls are the ones proven in s7_smoke_test.py:
  * S7Client constructed with keyword arguments, falling back to the minimal constructor on a TypeError (pyS7
    versions differ: pdu_size vs max_pdu), connect() retried up to `connect_retries` times;
  * client.read([address])[0] and client.write([address], [value]);
  * a bit read that fails with INVALID_DATA_SIZE is retried as a byte read plus bit mask (safe_read_one).
Values are coerced to the address type before writing (bool for X, int for I/DI, float for R / LR).
Address forms: X = BOOL, B = BYTE, I = INT (16-bit), DI = DINT (32-bit), R = REAL (32-bit), LR = LREAL (64-bit).

Install pyS7:  pip install pyS7   (PyPI release of https://github.com/xtimmy86x/pyS7)
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    from pyS7 import S7Client
except ImportError:                       # reported clearly when the Siemens IOServer actually connects
    S7Client = None

try:
    from pyS7 import extract_bit_from_byte  # type: ignore
except Exception:
    def extract_bit_from_byte(byte_value: int, bit_offset: int) -> bool:
        return bool((byte_value >> bit_offset) & 1)

DEFAULT_PROPERTIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "IOServerProperties.yaml")

_ADDRESS = re.compile(r"^DB(\d+),(X(\d+)\.([0-7])|I(\d+)|DI(\d+)|R(\d+)|B(\d+)|LR(\d+))$")
_SIZES = {"X": 0, "B": 1, "I": 2, "DI": 4, "R": 4, "LR": 8}
_TYPE_FOR_LETTER = {"X": "bool", "B": "int", "I": "int", "DI": "int", "R": "float", "LR": "float"}


def parse_address(address: str) -> Tuple[int, str, int, int]:
    """'DB500,X2.5' -> (500, 'X', 2, 5); 'DB500,I16' -> (500, 'I', 16, 0); 'DB101,LR96' -> (101, 'LR', 96, 0).
    Raises ValueError otherwise."""
    m = _ADDRESS.match(address.strip())
    if not m:
        raise ValueError(f"unsupported S7 address {address!r} (use DB<n>,X<b>.<bit> / I<b> / DI<b> / R<b> / LR<b> / B<b>)")
    db = int(m.group(1))
    if m.group(3) is not None:
        return db, "X", int(m.group(3)), int(m.group(4))
    for letter, group in (("I", 5), ("DI", 6), ("R", 7), ("B", 8), ("LR", 9)):
        if m.group(group) is not None:
            return db, letter, int(m.group(group)), 0
    raise ValueError(address)


def load_tag_map(path: str = DEFAULT_PROPERTIES_PATH) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str, str]]]:
    """Read IOServerProperties.yaml -> (plc settings, {tag: (address, type, dir)}), validating every entry:
    address syntax, type agreeing with the address letter, no two tags on the same bit, no overlapping words."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"IOServerProperties not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    tags: Dict[str, Tuple[str, str, str]] = {}
    bits: Dict[Tuple[int, int, int], str] = {}
    spans: List[Tuple[int, int, int, str]] = []          # (db, start, end, tag) for byte/word/real areas
    problems = []
    for name, spec in (cfg.get("tags") or {}).items():
        address = str(spec["address"]).strip()
        typ = str(spec.get("type", "")).lower()
        direction = str(spec.get("dir", "")).lower()
        try:
            db, letter, byte, bit = parse_address(address)
        except ValueError as e:
            problems.append(f"{name}: {e}")
            continue
        if typ != _TYPE_FOR_LETTER[letter]:
            problems.append(f"{name}: type '{typ}' does not match address {address} ({_TYPE_FOR_LETTER[letter]})")
        if letter == "X":
            if (db, byte, bit) in bits:
                problems.append(f"{name} and {bits[(db, byte, bit)]} share {address}")
            bits[(db, byte, bit)] = name
            spans.append((db, byte, byte + 1, name))
        else:
            spans.append((db, byte, byte + _SIZES[letter], name))
        tags[name] = (address, typ, direction)
    # words / reals must not overlap each other or bit bytes (bits in the same byte are fine)
    words = [s for s in spans if tags.get(s[3], ("", "", ""))[1] != "bool"]
    for i, (db, a0, a1, n) in enumerate(words):
        for db2, b0, b1, m in spans:
            if m != n and db == db2 and a0 < b1 and b0 < a1:
                pair = tuple(sorted((n, m)))
                msg = f"{pair[0]} and {pair[1]} overlap in DB{db}"
                if msg not in problems:
                    problems.append(msg)
    if problems:
        raise ValueError("IOServerProperties.yaml: " + "; ".join(problems))
    return cfg.get("plc", {}) or {}, tags


class _TagValue:
    """What pycomm3's LogixDriver.read() returns: an object with .tag, .value, .error."""
    __slots__ = ("tag", "value", "error")

    def __init__(self, tag: str, value: Any, error: Optional[str] = None):
        self.tag, self.value, self.error = tag, value, error

    def __repr__(self):
        return f"_TagValue({self.tag!r}, {self.value!r}{', ' + self.error if self.error else ''})"


def _byte_addr_for_bit(bit_addr: str) -> Tuple[str, int]:
    """'DB100,X3.5' -> ('DB100,B3', 5). (from s7_smoke_test.py)"""
    m = re.match(r"^(DB\d+),X(\d+)\.([0-7])$", bit_addr.strip())
    if not m:
        raise ValueError(f"not a DB bit address: {bit_addr}")
    return f"{m.group(1)},B{int(m.group(2))}", int(m.group(3))


def safe_read_one(client: Any, addr: str) -> Any:
    """Read a single address; on INVALID_DATA_SIZE for a bit, fall back to byte + mask. (from s7_smoke_test.py)"""
    try:
        return client.read([addr])[0]
    except Exception as e:
        if "INVALID_DATA_SIZE" in str(e) and ",X" in addr:
            byte_addr, bit = _byte_addr_for_bit(addr)
            byte_val = client.read([byte_addr])[0]
            return extract_bit_from_byte(int(byte_val), bit)
        raise


class PyS7PLCBackend:
    """One pyS7 connection with symbolic tag names (read results carry .value, as pycomm3's do). Thread-safe: each
    read and write holds the connection's lock."""

    def __init__(self, ip: Optional[str] = None, properties_path: str = DEFAULT_PROPERTIES_PATH, logger=None):
        plc, self._tagmap = load_tag_map(properties_path)
        self.ip: Optional[str] = plc.get("address") or ip
        self.rack: int = int(plc.get("rack", 0))
        self.slot: int = int(plc.get("slot", 1))
        self.pdu_size: int = int(plc.get("pdu_size", 480))
        self.timeout: float = float(plc.get("timeout", 5.0))
        self.connect_retries: int = int(plc.get("connect_retries", 3))
        self.logger = logger
        self._client = None
        self._lock = threading.Lock()

    # ---- tag map ----------------------------------------------------------------------------------------------------
    @property
    def tag_names(self) -> List[str]:
        return list(self._tagmap)

    def address_for(self, tag: str) -> Optional[str]:
        entry = self._tagmap.get(tag)
        return entry[0] if entry else None

    def _resolve(self, tag: str) -> Tuple[str, str]:
        entry = self._tagmap.get(tag)
        if entry is None:
            raise KeyError(f"tag {tag!r} has no address in IOServerProperties.yaml")
        return entry[0], entry[1]

    # ---- connection (LogixDriver: open / close / connected) ---------------------------------------------------------
    def _new_client(self):
        """As s7_smoke_test.connect(): keyword arguments first, minimal constructor on TypeError."""
        if S7Client is None:
            raise ImportError("pyS7 is not installed: pip install pyS7")
        for kwargs in (dict(address=self.ip, rack=self.rack, slot=self.slot, pdu_size=self.pdu_size, timeout=self.timeout),
                       dict(address=self.ip, rack=self.rack, slot=self.slot, max_pdu=self.pdu_size, timeout=self.timeout)):
            try:
                return S7Client(**kwargs)
            except TypeError:
                continue
        return S7Client(address=self.ip, rack=self.rack, slot=self.slot)

    def open(self) -> bool:
        if not self.ip:
            raise ConnectionError("Siemens PLC IP not set (siemens.plc.ip in ApplicationConfiguration.properties)")
        last_error: Optional[Exception] = None
        for attempt in range(1, self.connect_retries + 1):
            try:
                client = self._new_client()
                client.connect()
                if client.is_connected:
                    self._client = client
                    return True
            except Exception as e:
                last_error = e
                time.sleep(min(2 ** (attempt - 1), 5) if attempt < self.connect_retries else 0)
        raise ConnectionError(f"could not connect to S7 PLC {self.ip} rack {self.rack} slot {self.slot}: {last_error}")

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        try:
            return self._client is not None and bool(self._client.is_connected)
        except Exception:
            return False

    # ---- read / write (LogixDriver: read(tag) -> .value, read(*tags) -> list, write(tag, value)) --------------------
    def _read_one(self, tag: str) -> _TagValue:
        address, typ = self._resolve(tag)
        with self._lock:
            value = safe_read_one(self._client, address)
        if typ == "bool":
            value = bool(value)
        elif typ == "float":
            value = float(value)
        elif typ == "int":
            value = int(value)
        return _TagValue(tag, value)

    def read(self, *tags: str):
        if not tags:
            return None
        if len(tags) == 1:
            return self._read_one(tags[0])
        return [self._read_one(t) for t in tags]

    def write(self, tag: str, value: Any) -> _TagValue:
        address, typ = self._resolve(tag)
        if typ == "bool":
            value = bool(value)
        elif typ == "float":
            value = float(value)
        elif typ == "int":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{tag}: {value} is not an integer")
            value = int(value)
            if not -32768 <= value <= 32767 and ",I" in address:
                raise ValueError(f"{tag}: {value} does not fit a 16-bit INT")
        with self._lock:
            self._client.write([address], [value])
        return _TagValue(tag, value)
