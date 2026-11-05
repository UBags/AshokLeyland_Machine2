# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
MockPLCDataIO: a PLCDataIO on the in-memory MockPLC (costhetaio/commonplc/MockPLC.py), for PLCIOServer(mockInstance=
True) and the interactive test windows. Works for either PLC make: MockPLC is keyed by tag name and starts with every
tag the configured vendor's IOServer uses.

MockPLC is a singleton, so the reads and writes connections share one set of tag values - and one connection state:
closing either closes both, as before.
"""

from typing import Any, Optional

from costhetaio.commonplc.MockPLC import MockPLC
from costhetaio.commonplc.PLCDataIO import PLCDataIO


class MockPLCDataIO(PLCDataIO):
    vendor = "Mock"

    def __init__(self, ip: Optional[str] = None, name: str = ""):
        super().__init__(ip="MockPLC", name=name)
        self.plc = MockPLC()

    def describe(self) -> str:
        return "MockPLC"

    def _open(self) -> None:
        self.plc.open()

    def _close(self) -> None:
        self.plc.close()

    def _isConnected(self) -> bool:
        return self.plc.connected

    def _read(self, tag: str) -> Any:
        return self.plc.read(tag).value          # None for an unknown tag -> PLCIOError

    def _write(self, tag: str, value: Any) -> None:
        self.plc.write(tag, value)
