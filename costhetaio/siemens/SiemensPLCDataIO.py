# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
SiemensPLCDataIO: a PLCDataIO for the Siemens S7-1515 (Machine 2) through pyS7 (S7comm), used when
plc.vendor=siemens.

The IOServer uses symbolic tag names (the siemens.* keys of ApplicationConfiguration.properties); each name is
resolved to its absolute DB address through IOServerProperties.yaml. The S7 layer itself - address parsing, the tag
map with its clash checks, the pyS7 client with its connect retries and the bit-read fall-back - is
PyS7PLCBackend in plc_backend.py, which the command-line tools (test/s7_tag_check.py, test/s7_read_all_tags.py) use as well.

checkTagConfiguration() runs before the IOServer starts: every tag the IOServer uses must have an address in
IOServerProperties.yaml, and no two addresses may clash.
"""

from typing import Any, Dict, Optional

from costhetaio.commonplc.PLCDataIO import PLCDataIO
from costhetaio.siemens.plc_backend import PyS7PLCBackend, load_tag_map, DEFAULT_PROPERTIES_PATH

PROPERTIES_PATH: str = DEFAULT_PROPERTIES_PATH


class SiemensPLCDataIO(PLCDataIO):
    vendor = "Siemens"

    def __init__(self, ip: Optional[str] = None, name: str = "", propertiesPath: str = None):
        self.propertiesPath = propertiesPath or PROPERTIES_PATH
        self._backend = PyS7PLCBackend(ip=ip, properties_path=self.propertiesPath)
        super().__init__(ip=self._backend.ip, name=name)      # plc.address in the yaml wins over the given IP

    # ------------------------------------------------------------------------------------------ connection
    def _open(self) -> None:
        self._backend.open()

    def _close(self) -> None:
        self._backend.close()

    def _isConnected(self) -> bool:
        return self._backend.connected

    # ------------------------------------------------------------------------------------------ read / write
    def _read(self, tag: str) -> Any:
        return self._backend.read(tag).value

    def _write(self, tag: str, value: Any) -> None:
        self._backend.write(tag, value)

    # ------------------------------------------------------------------------------------------ tag checks
    @classmethod
    def checkTagConfiguration(cls, tagNames: Dict[str, str], path: str = None) -> None:
        """Every tag the IOServer uses must have an address (load_tag_map also rejects clashing addresses)."""
        path = path or PROPERTIES_PATH
        _, tags = load_tag_map(path)
        missing = {a: n for a, n in tagNames.items() if n and n not in tags}
        if missing:
            raise ValueError(f"{path}: no address for {', '.join(f'{n} ({a})' for a, n in sorted(missing.items()))}")

    def addressFor(self, tag: str) -> Optional[str]:
        """The tag's DB address (IOServerProperties.yaml)."""
        return self._backend.address_for(tag)

    # ------------------------------------------------------------------------------------------ diagnostics
    def findPIDs(self) -> list:
        """Logix PID tags: not applicable on S7."""
        return []

    def printTagMap(self) -> None:
        """Print every tag of IOServerProperties.yaml with its address, type and direction."""
        _, tags = load_tag_map(self.propertiesPath)
        for name, (address, typ, direction) in tags.items():
            print(f"{name}: {address} {typ} {direction}")
