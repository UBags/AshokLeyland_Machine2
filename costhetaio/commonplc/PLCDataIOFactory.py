# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Chooses the PLCDataIO for the PLC make: plc.vendor in ApplicationConfiguration.properties (or the environment
variable COSTHETA_PLC_VENDOR), see Configuration.getPlcVendor().

    allenbradley -> AllenBradleyPLCDataIO (pycomm3)
    siemens      -> SiemensPLCDataIO (pyS7)
    mock = True  -> MockPLCDataIO (in-memory MockPLC), for either make

The vendor modules are imported only when chosen, so a Siemens installation does not need pycomm3 and an
Allen-Bradley one does not need pyS7.
"""

from typing import Optional, Type

from Configuration import CosThetaConfigurator
from costhetaio.commonplc.PLCDataIO import PLCDataIO

VENDORS = ("allenbradley", "siemens")


def plcVendor() -> str:
    return CosThetaConfigurator.getPlcVendor()


def plcDataIOClass(vendor: Optional[str] = None, mock: bool = False) -> Type[PLCDataIO]:
    if mock:
        from costhetaio.commonplc.MockPLCDataIO import MockPLCDataIO
        return MockPLCDataIO
    vendor = (vendor or plcVendor()).strip().lower()
    if vendor == "siemens":
        from costhetaio.siemens.SiemensPLCDataIO import SiemensPLCDataIO
        return SiemensPLCDataIO
    if vendor == "allenbradley":
        from costhetaio.allenbradley.AllenBradleyPLCDataIO import AllenBradleyPLCDataIO
        return AllenBradleyPLCDataIO
    raise ValueError(f"unknown PLC vendor {vendor!r} (plc.vendor must be one of {', '.join(VENDORS)})")


def createPLCDataIO(ip: Optional[str], name: str = "", vendor: Optional[str] = None, mock: bool = False) -> PLCDataIO:
    """A new, not yet opened, PLCDataIO for the configured (or given) vendor."""
    return plcDataIOClass(vendor, mock)(ip=ip, name=name)
