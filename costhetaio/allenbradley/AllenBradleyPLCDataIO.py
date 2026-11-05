# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
AllenBradleyPLCDataIO: a PLCDataIO for Allen-Bradley Logix / Micro800 PLCs through pycomm3's LogixDriver
(EtherNet/IP), used when plc.vendor=allenbradley (Machine 1). Tags are addressed by their names in the controller.

pycomm3 reports most failures in the result (Tag.error set, Tag.value None) rather than by raising; here both
become PLCIOError, so a failed write is no longer taken for a successful one.

Besides the PLCDataIO methods, the pycomm3-specific diagnostics: readRaw (pycomm3 Tag objects, several tags in one
request), readArray / readArraySlice, findPIDs, printDataTypeAttributes, and missingTags (the controller's tag list,
checked once after the first connection).
"""

import logging
from typing import Any, Iterable, List, Optional

from pycomm3 import LogixDriver

from costhetaio.commonplc.PLCDataIO import PLCDataIO, PLCIOError

logging.getLogger("pycomm3").setLevel(logging.CRITICAL)      # pycomm3's own logger stays silent


class AllenBradleyPLCDataIO(PLCDataIO):
    vendor = "Allen-Bradley"

    def __init__(self, ip: Optional[str] = None, name: str = ""):
        super().__init__(ip=ip, name=name)
        self._driver = None

    # ------------------------------------------------------------------------------------------ connection
    def _open(self) -> None:
        driver = LogixDriver(f"{self.ip}")
        driver.open()
        self._driver = driver

    def _close(self) -> None:
        driver, self._driver = self._driver, None
        if driver is not None:
            driver.close()

    def _isConnected(self) -> bool:
        return self._driver is not None and bool(self._driver.connected)

    # ------------------------------------------------------------------------------------------ read / write
    @staticmethod
    def _checked(result, what: str):
        error = getattr(result, "error", None)
        if error:
            raise PLCIOError(f"{what}: {error}")
        return result

    def _read(self, tag: str) -> Any:
        return self._checked(self._driver.read(tag), f"read {tag}").value

    def _write(self, tag: str, value: Any) -> None:
        self._checked(self._driver.write(tag, value), f"write {tag}={value!r}")

    # ------------------------------------------------------------------------------------------ tag checks
    def missingTags(self, tagNames: Iterable[str]) -> List[str]:
        """Names whose base tag is not in the controller's tag list (program-scoped 'Program:...' names are not
        checked). Empty if the list cannot be had."""
        try:
            known = set(self._driver.tags)
        except Exception:
            return []
        if not known:
            return []
        missing = []
        for name in tagNames:
            if not name or name.startswith("Program:"):
                continue
            base = name.split(".")[0].split("[")[0]
            if base not in known:
                missing.append(name)
        return missing

    # ------------------------------------------------------------------------------------------ pycomm3 diagnostics
    def readRaw(self, *tags: str):
        """pycomm3's own result: a Tag for one name, a list of Tags for several (one request). Errors are in the
        Tags (.error), not raised."""
        if not self.connected:
            raise PLCIOError(f"not connected ({self.describe()})")
        with self._lock:
            return self._driver.read(*tags)

    def readArray(self, arrayName: str, numberOfElements: int):
        """The first numberOfElements elements of an array tag (pycomm3 Tag)."""
        return self.readRaw(f"{arrayName}" + "{" + f"{numberOfElements}" + "}")

    def readArraySlice(self, arrayName: str, index: int, numberOfElements: int):
        """numberOfElements elements of an array tag from index (pycomm3 Tag)."""
        return self.readRaw(f"{arrayName}[{index}]" + "{" + f"{numberOfElements}" + "}")

    def findPIDs(self) -> list:
        """Names of the controller's PID tags."""
        with self._lock:
            return [tag for tag, _def in self._driver.tags.items() if _def['data_type_name'] == 'PID']

    def printDataTypeAttributes(self) -> None:
        """Print the attributes of every data type the controller defines."""
        with self._lock:
            for typ in self._driver.data_types:
                print(f'{typ} attributes: ', self._driver.data_types[typ]['attributes'])
