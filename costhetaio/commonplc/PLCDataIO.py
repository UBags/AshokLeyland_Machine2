# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
PLCDataIO: one connection to a PLC, reading and writing tags by name. The only part of the IOServer that depends on
the PLC make.

    PLCIOServer (costhetaio/commonplc/PLCIOServer.py)   the machine: state machine, handshakes, threads, resets,
                                                         restart detection, Redis messages - the same for every PLC
        holds two PLCDataIO objects (one for reads, one for writes), made by createPLCDataIO() (PLCDataIOFactory.py)

    PLCDataIO (this module, abstract)
        AllenBradleyPLCDataIO   costhetaio/allenbradley/AllenBradleyPLCDataIO.py   pycomm3 LogixDriver (EtherNet/IP)
        SiemensPLCDataIO        costhetaio/siemens/SiemensPLCDataIO.py             pyS7 (S7comm), tag name -> DB address
        MockPLCDataIO           costhetaio/commonplc/MockPLCDataIO.py              in-memory MockPLC (test windows)

The contract every implementation keeps:
  * open() connects (raises on failure), close() disconnects (never raises), .connected says whether it is connected;
  * read(tag) returns the tag's value; write(tag, value) writes it. Both raise PLCIOError when the value could not be
    read or written - also when the driver reports the error in its result instead of raising (pycomm3 does). A
    failure is therefore never mistaken for a value; what to do about it is the IOServer's decision;
  * one object may be used from several threads: read() and write() hold the object's own lock around the driver
    call (neither pycomm3 nor pyS7 is thread-safe);
  * checkTagConfiguration(tagNames) (class method, before connecting) raises ValueError if the configuration cannot
    serve every tag the IOServer uses (Siemens: an address in IOServerProperties.yaml for each, no clashes);
    missingTags(names) (after connecting) lists the names the PLC itself does not know, where the PLC can say.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional


class PLCIOError(Exception):
    """A tag could not be read or written (link down, unknown tag, driver error)."""


class PLCDataIO(ABC):
    """One connection to a PLC. Subclasses implement _open / _close / _isConnected / _read / _write; the public
    methods add the lock, the not-connected check and the error contract."""

    vendor: str = ""

    def __init__(self, ip: Optional[str] = None, name: str = ""):
        self.ip: Optional[str] = ip
        self.name: str = name               # "reads" / "writes", for messages
        self._lock = threading.RLock()

    # ------------------------------------------------------------------------------------------ to implement
    @abstractmethod
    def _open(self) -> None:
        """Connect; raise on failure."""

    @abstractmethod
    def _close(self) -> None:
        """Disconnect (may raise; close() ignores it)."""

    @abstractmethod
    def _isConnected(self) -> bool:
        """True while connected."""

    @abstractmethod
    def _read(self, tag: str) -> Any:
        """The tag's value; raise (anything) on failure. Called with the lock held."""

    @abstractmethod
    def _write(self, tag: str, value: Any) -> None:
        """Write the value; raise (anything) on failure. Called with the lock held."""

    # ------------------------------------------------------------------------------------------ connection
    def open(self) -> None:
        with self._lock:
            self._open()

    def close(self) -> None:
        with self._lock:
            try:
                self._close()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        try:
            return bool(self._isConnected())
        except Exception:
            return False

    def describe(self) -> str:
        """For messages, e.g. 'Siemens PLC at 192.168.1.10'."""
        return f"{self.vendor or type(self).__name__} PLC at {self.ip}"

    # ------------------------------------------------------------------------------------------ read / write
    def read(self, tag: str) -> Any:
        """The tag's value. Raises PLCIOError if it could not be read (never returns None for a failure)."""
        if not self.connected:
            raise PLCIOError(f"not connected ({self.describe()})")
        try:
            with self._lock:
                value = self._read(tag)
        except PLCIOError:
            raise
        except Exception as e:
            raise PLCIOError(f"read {tag}: {e}") from e
        if value is None:
            raise PLCIOError(f"read {tag}: no value")
        return value

    def write(self, tag: str, value: Any) -> None:
        """Write the value. Raises PLCIOError if it could not be written."""
        if not self.connected:
            raise PLCIOError(f"not connected ({self.describe()})")
        try:
            with self._lock:
                self._write(tag, value)
        except PLCIOError:
            raise
        except Exception as e:
            raise PLCIOError(f"write {tag}={value!r}: {e}") from e

    def readMany(self, tags: Iterable[str]) -> Dict[str, Any]:
        """{tag: value} for several tags (one read each unless an implementation can do better). Raises PLCIOError
        on the first that cannot be read."""
        return {tag: self.read(tag) for tag in tags}

    # ------------------------------------------------------------------------------------------ tag checks
    @classmethod
    def checkTagConfiguration(cls, tagNames: Dict[str, str]) -> None:
        """Before connecting: raise ValueError if the configuration cannot serve these tags ({IOServer attribute:
        tag name}). Default: nothing to check."""
        return None

    def missingTags(self, tagNames: Iterable[str]) -> List[str]:
        """After connecting: the names the PLC does not know, where the PLC can list its tags. Default: none known."""
        return []

    def addressFor(self, tag: str) -> Optional[str]:
        """The tag's address on the PLC, where tags have one separate from their name (Siemens). Default: None."""
        return None
