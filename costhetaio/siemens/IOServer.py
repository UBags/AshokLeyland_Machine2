# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
IOServer for the Siemens S7-1515 (Machine 2), used when plc.vendor=siemens in ApplicationConfiguration.properties.

It IS the Allen-Bradley IOServer (costhetaio/allenbradley/IOServer.py) - same 28-state machine, PLC handshakes
(OK, pause, Done), torque reads and delayed tag resets, cycle settings written in state 2, emergency abort, heartbeats,
Redis messages, tag publishing and mock-PLC mode - with only the PLC client replaced: pycomm3's LogixDriver becomes
PyS7PLCBackend (plc_backend.py), which speaks S7comm through pyS7 and resolves each tag name to its DB address through
IOServerProperties.yaml.

Tag names and the PLC IP come from the siemens.* keys of ApplicationConfiguration.properties (Configuration replaces
each key by its siemens.* counterpart when plc.vendor=siemens). Start-up stops with a clear error if any tag the
IOServer uses has no address in IOServerProperties.yaml, or if two addresses clash.
"""

import os
import sys

from Configuration import CosThetaConfigurator
from utils.CosThetaPrintUtils import printBoldGreen, printBoldRed, printBoldBlue, printBlue

if CosThetaConfigurator.getPlcVendor() != "siemens":
    raise ImportError("costhetaio.siemens.IOServer needs plc.vendor=siemens in ApplicationConfiguration.properties "
                      "(or COSTHETA_PLC_VENDOR=siemens): its tag names are the siemens.* ones")

import costhetaio.allenbradley.IOServer as _ab
from costhetaio.siemens.plc_backend import PyS7PLCBackend, load_tag_map, DEFAULT_PROPERTIES_PATH

PROPERTIES_PATH: str = DEFAULT_PROPERTIES_PATH


def tagNamesUsedByIOServer() -> dict:
    """{IOServer attribute: tag name} for every PLC tag the IOServer reads or writes (empty names = not provided)."""
    return {a: getattr(_ab.IOServer, a) for a in vars(_ab.IOServer)
            if a[:6] in ("PLC_PC", "PC_PLC", "PC_PLP") and isinstance(getattr(_ab.IOServer, a), str)
            and getattr(_ab.IOServer, a)}


def checkTagMap(path: str = PROPERTIES_PATH) -> None:
    """Every tag the IOServer uses must have an address (load_tag_map also rejects clashing addresses)."""
    _, tags = load_tag_map(path)
    missing = {a: n for a, n in tagNamesUsedByIOServer().items() if n not in tags}
    if missing:
        raise ValueError(f"{path}: no address for {', '.join(f'{n} ({a})' for a, n in sorted(missing.items()))}")


class IOServer(_ab.IOServer):

    def __init__(self, plc_ip=None, mode: str = "TEST", mockInstance: bool = False):
        checkTagMap()
        super().__init__(plc_ip=plc_ip, mode=mode, mockInstance=mockInstance)

    def connectPLC(self, forceRenew=False):
        """As the Allen-Bradley connectPLC(), with two PyS7PLCBackend clients (reads / writes) instead of two
        LogixDriver clients. Mock mode uses the same in-memory MockPLC (it is keyed by tag name)."""
        if self.mockInstance:
            return super().connectPLC(forceRenew)
        if self.connectionInProgress:
            return None
        self.connectionInProgress = True
        self.connectionAttempts += 1
        try:
            if forceRenew:
                for client in (self.clientForReads, self.clientForWrites):
                    try:
                        if client is not None:
                            client.close()
                    except Exception:
                        pass
                self.clientForReads = None
                self.clientForWrites = None
            self.clientForReads = PyS7PLCBackend(ip=self.plc_ip, properties_path=PROPERTIES_PATH)
            self.clientForReads.open()
            printBoldGreen(f"Connected to Siemens PLC for Reads at {self.clientForReads.ip}")
            self.clientForWrites = PyS7PLCBackend(ip=self.plc_ip, properties_path=PROPERTIES_PATH)
            self.clientForWrites.open()
            printBoldGreen(f"Connected to Siemens PLC for Writes at {self.clientForWrites.ip}")
            self.connectionInProgress = False
            self.connectionAttempts = 0
            return True
        except Exception as e:
            if (self.connectionAttempts == 1) or (
                    (self.connectionAttempts % _ab.IOServer.LOG_CONNECTION_PROBLEM_EVERY_N_SECS) % 5) == 0:
                printBoldRed(f"PLC connection error: {e}")
            for client in (self.clientForReads, self.clientForWrites):
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
            self.clientForReads = None
            self.clientForWrites = None
            self.connectionInProgress = False
            return False

    def get_tag_address(self, tag: str):
        """The DB address of a tag (IOServerProperties.yaml)."""
        client = self.clientForReads
        return client.address_for(tag) if isinstance(client, PyS7PLCBackend) else None

    def find_pids(self):
        return []                      # Logix PID tags: not applicable on S7

    def find_attributes(self):
        _, tags = load_tag_map(PROPERTIES_PATH)
        for name, (address, typ, direction) in tags.items():
            print(f"{name}: {address} {typ} {direction}")


def startIOServer(mode: str = "TEST"):
    io = IOServer(mode=mode)
    printBoldBlue(f"*****************")
    printBlue(f"Started IO Server (Siemens S7)")
    printBoldBlue(f"*****************")
    io.heartbeatThread.join()
    io.monitorStopThread.join()
    sys.exit(0)
