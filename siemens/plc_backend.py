# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
pyS7 PLC backend for IOServer (Siemens S7-1500 port).

This module isolates every line that touches the PLC driver. It replaces the
pycomm3 `LogixDriver` layer with pyS7's `S7Client`, keeping the *exact* method
signatures and return contracts of IOServer's original primitives so that the
loops, the state machine and the Redis wiring above it do not change.

Design notes
------------
* Two independent S7Client sockets (reads / writes), mirroring the original
  two-LogixDriver split. pyS7 wants one client per thread; the reading/writing
  locks preserve that guarantee when several threads share a client.
* Symbolic tag name -> absolute S7 address resolution comes from
  IOServerProperties.yaml. The tag NAMES stay identical to the IOServer
  constants; only their resolved target changes (symbolic -> DBx,offset).
* Bit writes go out as true single-bit S7 writes (transport size BIT), so a
  PC write never disturbs neighbouring bits on the wire.

Deviation from the original (intentional, flagged):
* IOServer.read_int() returned a value only inside its `if debug:` branch, so
  with the default debug=False it fell through and returned None. That made the
  watchdog check (`read_int(WatchTag) != 1234`) fire spuriously. This port
  returns the value unconditionally.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional, Tuple

import yaml
from pyS7 import S7Client

# ---- fallback sentinels (kept identical to IOServer's constants) -------------
FALSE_FLOAT_VALUE: float = -1234.0
FALSE_INT_VALUE: int = -1234


class PyS7PLCBackend:
    """Holds the two S7Client connections and the tag->address map.

    IOServer delegates its read_*/write_* primitives and connectPLC to an
    instance of this class. Return values match the originals exactly.
    """

    def __init__(self, properties_path: str, logger=None) -> None:
        self.properties_path = properties_path
        self.logger = logger  # optional: pass IOServer.logger for parity

        cfg = self._load(properties_path)
        plc = cfg.get("plc", {}) or {}
        self.address: Optional[str] = plc.get("address")
        self.rack: int = int(plc.get("rack", 0))
        self.slot: int = int(plc.get("slot", 1))
        self.pdu_size: int = int(plc.get("pdu_size", 480))

        # tag name -> (address, type, dir)
        self._tagmap: Dict[str, Tuple[str, str, str]] = {}
        for name, spec in (cfg.get("tags") or {}).items():
            self._tagmap[name] = (
                spec["address"],
                str(spec.get("type", "")).lower(),
                str(spec.get("dir", "")).lower(),
            )

        self.clientForReads: Optional[S7Client] = None
        self.clientForWrites: Optional[S7Client] = None

        self.readingLock = threading.Lock()
        self.writingLock = threading.Lock()

    # ---- config -------------------------------------------------------------

    @staticmethod
    def _load(path: str) -> Dict[str, Any]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"IOServerProperties not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def address_for(self, tag: str) -> Optional[str]:
        """Resolve a logical tag name to its absolute S7 address, or None."""
        entry = self._tagmap.get(tag)
        return entry[0] if entry else None

    def _warn(self, msg: str) -> None:
        if self.logger is not None:
            try:
                self.logger.warning(msg)
            except Exception:
                pass

    # ---- connection (mirrors IOServer.connectPLC / MockPLC removed) ----------

    @property
    def is_connected(self) -> bool:
        return (
            self.clientForReads is not None
            and self.clientForWrites is not None
            and self.clientForReads.is_connected
            and self.clientForWrites.is_connected
        )

    def connect(self, forceRenew: bool = False, ip: Optional[str] = None) -> bool:
        """Open both sockets. Returns True on success, False otherwise.

        `ip` overrides the YAML address (IOServer passes self.plc_ip).
        """
        target = ip or self.address
        if target is None:
            self._warn("PLC address not set in YAML and no ip override given")
            return False

        if forceRenew:
            self.close()

        try:
            self.clientForReads = S7Client(target, self.rack, self.slot, pdu_size=self.pdu_size)
            self.clientForReads.connect()
            self.clientForWrites = S7Client(target, self.rack, self.slot, pdu_size=self.pdu_size)
            self.clientForWrites.connect()
            return True
        except Exception as e:
            self._warn(f"PLC connection error: {e}")
            self.close()
            return False

    def close(self) -> None:
        for attr in ("clientForReads", "clientForWrites"):
            client = getattr(self, attr, None)
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
            setattr(self, attr, None)

    # ---- read primitives (contracts identical to IOServer) ------------------

    def read_bool(self, tag: str, expected_value: bool = True, debug: bool = False) -> Optional[bool]:
        if not tag:
            return False
        addr = self.address_for(tag)
        if addr is None:
            self._warn(f"read_bool: no address mapped for tag {tag}")
            return not expected_value
        if self.clientForReads is None or not self.clientForReads.is_connected:
            return False
        try:
            with self.readingLock:
                value = self.clientForReads.read([addr])[0]
            if value is None:
                self._warn(f"Error reading bool tag {tag}")
                return not expected_value
            return bool(value)
        except Exception as e:
            self._warn(f"Error reading bool tag {tag}: {e}")
            return not expected_value

    def read_float(self, tag: str, debug: bool = False) -> Optional[float]:
        if not tag:
            return FALSE_FLOAT_VALUE
        addr = self.address_for(tag)
        if addr is None:
            self._warn(f"read_float: no address mapped for tag {tag}")
            return FALSE_FLOAT_VALUE
        if self.clientForReads is None or not self.clientForReads.is_connected:
            return FALSE_FLOAT_VALUE
        try:
            with self.readingLock:
                value = self.clientForReads.read([addr])[0]
            if value is None:
                self._warn(f"Error reading float tag {tag}")
                return FALSE_FLOAT_VALUE
            return float(value)
        except Exception as e:
            self._warn(f"Error reading float tag {tag}: {e}")
            return FALSE_FLOAT_VALUE

    def read_int(self, tag: str, debug: bool = False) -> Optional[int]:
        # NOTE: returns unconditionally (original only returned under `if debug`).
        if not tag:
            return FALSE_INT_VALUE
        addr = self.address_for(tag)
        if addr is None:
            self._warn(f"read_int: no address mapped for tag {tag}")
            return FALSE_INT_VALUE
        if self.clientForReads is None or not self.clientForReads.is_connected:
            return FALSE_INT_VALUE
        try:
            with self.readingLock:
                value = self.clientForReads.read([addr])[0]
            if value is None:
                self._warn(f"Error reading int tag {tag}: value is None")
                return FALSE_INT_VALUE
            return int(value)
        except Exception as e:
            self._warn(f"Error reading int tag {tag}: {e}")
            return FALSE_INT_VALUE

    # ---- write primitives (contracts identical to IOServer) -----------------

    def write_bool(self, tag: str, value: bool, debug: bool = True) -> bool:
        if tag is None:
            return False
        addr = self.address_for(tag)
        if addr is None:
            self._warn(f"write_bool: no address mapped for tag {tag}")
            return False
        if self.clientForWrites is None or not self.clientForWrites.is_connected:
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write([addr], [bool(value)])
            return True
        except Exception as e:
            self._warn(f"Error writing bool tag {tag}: {e}")
            return False

    def write_float(self, tag: str, value: float, debug: bool = True) -> bool:
        if tag is None:
            return False
        addr = self.address_for(tag)
        if addr is None:
            self._warn(f"write_float: no address mapped for tag {tag}")
            return False
        if self.clientForWrites is None or not self.clientForWrites.is_connected:
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write([addr], [float(value)])
            return True
        except Exception as e:
            self._warn(f"Error writing float tag {tag}: {e}")
            return False

    def write_int(self, tag: str, value: int, debug: bool = False) -> bool:
        if tag is None or not isinstance(value, int):
            return False
        addr = self.address_for(tag)
        if addr is None:
            self._warn(f"write_int: no address mapped for tag {tag}")
            return False
        if self.clientForWrites is None or not self.clientForWrites.is_connected:
            return False
        try:
            with self.writingLock:
                self.clientForWrites.write([addr], [int(value)])
            return True
        except Exception as e:
            self._warn(f"Error writing int tag {tag}: {e}")
            return False
