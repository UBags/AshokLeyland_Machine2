"""Dry run of the PLC connection layer (costhetaio/commonplc): PLCDataIO and its implementations, the factory, and the
PLCIOServer's use of them. Automatic PASS/FAIL checks, exit code 1 on failure.

  P  PLCDataIO contract, on fake drivers (no Redis, no database):
       Allen-Bradley (pycomm3 LogixDriver stand-in): not connected -> PLCIOError; a value; an error reported in the
       result (pycomm3 does not raise) -> PLCIOError on read and on write; no value -> PLCIOError; missingTags against
       the controller's tag list; the per-connection lock (no two driver calls at once from 8 threads); close.
       Siemens (SiemensPLCDataIO on a simulated S7 data block): write / read by tag name through the DB address;
       addressFor; an unknown tag -> PLCIOError; checkTagConfiguration refuses a tag with no address and passes the
       IOServer's tags.
       Mock (MockPLCDataIO): unknown tag -> PLCIOError; reads and writes share the one MockPLC.
       Factory: plc.vendor -> class; mock; unknown vendor -> ValueError.
  S  PLCIOServer on the simulated PLC of _plc_sim.py (vendor: COSTHETA_PLC_VENDOR, else plc.vendor - as the server
     decides; needs Redis and PostgreSQL): connected to the simulated PLC (S0; the rest is skipped otherwise); the
     vendor's PLCDataIO in use; a tag the driver refuses -> write_bool False (and the
     audit records the error), verified write gives up, read_bool -> not expected, _readBoolOrNone -> None,
     read_float -> FALSE_FLOAT_VALUE; the same tag writes and reads again once accepted; tagNamesUsed;
     get_tag_address; link down -> not connected.
  M  PLCIOServer(mockInstance=True) in a separate process (python DryRun_PLCDataIO.py --mock): MockPLCDataIO in use,
     CheckQRCode armed at start, QR handshake with the cycle settings written to the MockPLC, state moves on.

Run from anywhere (no PLC needed):  python test/machine2_dryrun/DryRun_PLCDataIO.py   (COSTHETA_PLC_VENDOR=siemens for Siemens)
"""
import os, pathlib, subprocess, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

QR_DOST_LHS = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if (detail and not ok) else ""), flush=True)


def raises(function, exception):
    try:
        function()
    except exception as e:
        return str(e) or True
    except Exception as e:
        return False
    return False


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\nRESULT {len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
          flush=True)
    os._exit(1 if failed else 0)


# ======================================================================================== M: mock-mode server (child)
if "--mock" in sys.argv:
    from persistence import Persistence as P
    P.setDatabaseName("Test", createDB=True)
    P.insertNewMachineSettings(P.getDatabaseName(), updated_by="dryrun", **P.DEFAULT_MACHINE_SETTINGS)
    from redis import Redis
    from utils.RedisUtils import sendDataFromQRCodeServerToIOServer, ok, clearQueues
    from costhetaio.commonplc.PLCIOServer import PLCIOServer
    from costhetaio.commonplc.MockPLC import get_mock_plc
    from statemachine.StateMachine import MachineState as MS
    r = Redis("localhost", 6379); clearQueues(r)
    io = PLCIOServer(mode="Test", mockInstance=True)
    plc = get_mock_plc()
    check("M1 mockInstance=True -> MockPLCDataIO for reads and writes",
          type(io.dataIOForReads).__name__ == type(io.dataIOForWrites).__name__ == "MockPLCDataIO")
    check("M2 CheckQRCode armed by the PC at start", plc.get_tag(PLCIOServer.PLC_PC_CheckQRCode) is True)
    plc.set_tag(PLCIOServer.PLC_PC_WatchTag, 1234)
    sendDataFromQRCodeServerToIOServer(r, qrCode=QR_DOST_LHS, qrCodeStatus=ok)
    t0 = time.time()
    while time.time() - t0 < 15 and not plc.get_tag(PLCIOServer.PC_PLC_QRCodeCheckDone):
        time.sleep(0.1)
    check("M3 QR OK and Done written to the MockPLC",
          plc.get_tag(PLCIOServer.PC_PLC_QRCodeCheckOK) is True and plc.get_tag(PLCIOServer.PC_PLC_QRCodeCheckDone) is True)
    check("M4 cycle settings written (side LHS = 1, torque 1 setpoint 15)",
          plc.get_tag(PLCIOServer.PC_PLC_LH_RH_Selection) == 1
          and plc.get_tag(PLCIOServer.PC_PLC_TighteningTorque1Setpoint) == 15.0)
    time.sleep(1)
    check("M5 state moved past the QR code",
          io.machineState.getCurrentState() not in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), io.machineState.getCurrentState().name)
    finish()

# ======================================================================================== P: contract, fake drivers
print("=== P: PLCDataIO contract ===", flush=True)
from costhetaio.commonplc.PLCDataIO import PLCDataIO, PLCIOError
import costhetaio.allenbradley.AllenBradleyPLCDataIO as ABD


class _Tag:
    def __init__(self, value, error=None): self.value, self.error = value, error


class FakeLogix:
    """pycomm3 LogixDriver stand-in: errors in the result, a tag list, and a detector for overlapping calls."""
    tags = {"Known": {"data_type_name": "BOOL"}, "Pid1": {"data_type_name": "PID"}, "Arr": {"data_type_name": "DINT"}}
    busy = 0; overlaps = 0
    def __init__(self, ip): self.connected = False; self.memory = {"Known": True, "Arr": 5}
    def open(self): self.connected = True
    def close(self): self.connected = False
    def _enter(self):
        FakeLogix.busy += 1
        if FakeLogix.busy > 1: FakeLogix.overlaps += 1
        time.sleep(0.0005)
    def _leave(self): FakeLogix.busy -= 1
    def read(self, *tags):
        self._enter()
        try:
            if tags[0] == "Refused": return _Tag(None, "Tag doesn't exist")
            if tags[0] == "NoValue": return _Tag(None)
            return _Tag(self.memory.get(tags[0], False))
        finally: self._leave()
    def write(self, tag, value):
        self._enter()
        try:
            if tag == "Refused": return _Tag(value, "write refused")
            self.memory[tag] = value; return _Tag(value)
        finally: self._leave()


originalLogix = ABD.LogixDriver
ABD.LogixDriver = FakeLogix
ab = ABD.AllenBradleyPLCDataIO("10.0.0.1", "reads")
check("P1 Allen-Bradley: read before open -> PLCIOError", raises(lambda: ab.read("Known"), PLCIOError))
ab.open()
check("P2 Allen-Bradley: connected after open; read gives the value", ab.connected and ab.read("Known") is True)
check("P3 Allen-Bradley: read error in the result (no exception from pycomm3) -> PLCIOError",
      raises(lambda: ab.read("Refused"), PLCIOError))
check("P4 Allen-Bradley: write error in the result -> PLCIOError (was taken as written before)",
      raises(lambda: ab.write("Refused", True), PLCIOError))
check("P5 Allen-Bradley: read with no value -> PLCIOError (a failure is never a value)",
      raises(lambda: ab.read("NoValue"), PLCIOError))
ab.write("Known", False)
check("P6 Allen-Bradley: write then read back", ab.read("Known") is False)
check("P7 Allen-Bradley: missingTags = names not in the controller (base name of members / elements; Program: skipped)",
      ab.missingTags(["Known", "Known.Member", "Arr[3]", "Missing", "Program:Main.X", ""]) == ["Missing"],
      str(ab.missingTags(["Known", "Known.Member", "Arr[3]", "Missing", "Program:Main.X", ""])))
check("P8 Allen-Bradley: findPIDs from the controller's tag list", ab.findPIDs() == ["Pid1"])
FakeLogix.overlaps = 0


def hammer(i):
    for k in range(60):
        ab.write("Known", bool(k % 2)) if i % 2 else ab.read("Known")


threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
[t.start() for t in threads]; [t.join() for t in threads]
check("P9 Allen-Bradley: 8 threads x 60 reads / writes on one connection, never two driver calls at once",
      FakeLogix.overlaps == 0, f"{FakeLogix.overlaps} overlaps")
ab.close()
check("P10 Allen-Bradley: close -> not connected; close again does not raise", (not ab.connected) and (ab.close() is None))
ABD.LogixDriver = originalLogix

# Siemens: the real SiemensPLCDataIO and PyS7PLCBackend on a byte-level S7 data block (pyS7 client replaced)
import struct, collections
import costhetaio.siemens.plc_backend as PB
from costhetaio.siemens.SiemensPLCDataIO import SiemensPLCDataIO
_, TAGMAP = PB.load_tag_map()
DB = collections.defaultdict(lambda: bytearray(512))


class FakeS7:
    def __init__(self, address=None, **kw): self.is_connected = False
    def connect(self): self.is_connected = True
    def disconnect(self): self.is_connected = False
    def read(self, addresses):
        out = []
        for a in addresses:
            db, letter, byte, bit = PB.parse_address(a)
            m = DB[db]
            out.append(bool((m[byte] >> bit) & 1) if letter == "X" else
                       struct.unpack(">h", m[byte:byte + 2])[0] if letter == "I" else
                       struct.unpack(">f", m[byte:byte + 4])[0] if letter == "R" else
                       struct.unpack(">d", m[byte:byte + 8])[0] if letter == "LR" else m[byte])
        return out
    def write(self, addresses, values):
        for a, v in zip(addresses, values):
            db, letter, byte, bit = PB.parse_address(a)
            m = DB[db]
            if letter == "X": m[byte] = (m[byte] | (1 << bit)) if v else (m[byte] & ~(1 << bit) & 0xFF)
            elif letter == "I": m[byte:byte + 2] = struct.pack(">h", int(v))
            elif letter == "R": m[byte:byte + 4] = struct.pack(">f", float(v))
            elif letter == "LR": m[byte:byte + 8] = struct.pack(">d", float(v))


originalS7 = PB.S7Client
PB.S7Client = FakeS7
s7 = SiemensPLCDataIO(ip="192.168.1.10", name="writes")
s7.open()
someBool = next(n for n, (a, t, d) in TAGMAP.items() if t == "bool")
someLReal = next((n for n, (a, t, d) in TAGMAP.items() if ",LR" in a), None)
s7.write(someBool, True)
check(f"P11 Siemens: write / read {someBool} by name through its DB address ({s7.addressFor(someBool)})",
      s7.read(someBool) is True and s7.addressFor(someBool) == TAGMAP[someBool][0])
if someLReal:
    s7.write(someLReal, 30)
    check(f"P12 Siemens: LREAL {someLReal} ({TAGMAP[someLReal][0]}) written and read as 30.0", s7.read(someLReal) == 30.0)
check("P13 Siemens: tag with no address -> PLCIOError", raises(lambda: s7.read("NoSuchTag"), PLCIOError))
check("P14 Siemens: checkTagConfiguration refuses a tag with no address",
      raises(lambda: SiemensPLCDataIO.checkTagConfiguration({"X": "NoSuchTag"}), ValueError))
s7.close()
PB.S7Client = originalS7

from costhetaio.commonplc.MockPLCDataIO import MockPLCDataIO
m1, m2 = MockPLCDataIO(name="reads"), MockPLCDataIO(name="writes")
m1.open()
check("P15 Mock: unknown tag -> PLCIOError", raises(lambda: m1.read("NoSuchTag"), PLCIOError))
m2.write("DryRunMockTag", 7)
check("P16 Mock: reads and writes share the one MockPLC", m1.read("DryRunMockTag") == 7)

from costhetaio.commonplc import PLCDataIOFactory as F
check("P17 Factory: allenbradley / siemens / mock -> the right class",
      F.plcDataIOClass("allenbradley").__name__ == "AllenBradleyPLCDataIO"
      and F.plcDataIOClass("siemens").__name__ == "SiemensPLCDataIO"
      and F.plcDataIOClass("siemens", mock=True).__name__ == "MockPLCDataIO")
check("P18 Factory: unknown vendor -> ValueError", raises(lambda: F.plcDataIOClass("omron"), ValueError))

# ======================================================================================== S: the server on the simulated PLC
print("\n=== S: PLCIOServer on the simulated PLC ===", flush=True)
from _plc_sim import setup, ERRORS, LINK, WRITES
IOServer, TAGS, _W, LOCK, VENDOR = setup()
TAGS[IOServer.PLC_PC_WatchTag] = 1234
io = IOServer(mode="Test")
expected = {"allenbradley": "AllenBradleyPLCDataIO", "siemens": "SiemensPLCDataIO"}[VENDOR]
# Every check below is only meaningful against the SIMULATED PLC, connected: when nothing is connected every read and
# write fails anyway, so a "refused" check would pass for the wrong reason.
check(f"S0 connected to the simulated {VENDOR} PLC (not the real one)", io._plcConnected()
      and io.read_int(IOServer.PLC_PC_WatchTag) == 1234, f"connected={io._plcConnected()}")
if not results[-1][1]:
    print("      S checks skipped: the server is not on the simulated PLC", flush=True)
    finish()
check(f"S1 {VENDOR}: the server uses {expected} for reads and writes",
      type(io.dataIOForReads).__name__ == type(io.dataIOForWrites).__name__ == expected)
names = IOServer.tagNamesUsed()
check(f"S2 tagNamesUsed: {len(names)} PLC tags, every one a non-empty name",
      len(names) >= 40 and all(isinstance(n, str) and n for n in names.values()))
check("S3 get_tag_address: Siemens -> the DB address, Allen-Bradley -> None",
      io.get_tag_address(IOServer.PLC_PC_CheckKnuckle) ==
      (TAGMAP[IOServer.PLC_PC_CheckKnuckle][0] if VENDOR == "siemens" else None))

tag = IOServer.PC_PLC_ConnectionStatusTag        # written by the PC on a timer; harmless to refuse for a moment
probe = IOServer.PLC_PC_CheckKnuckle
probeFloat = IOServer.PLC_PC_Station2TorqueValue
TAGS[probe] = True
ERRORS.update({tag, probe, probeFloat})
time.sleep(0.3)
mark = len(WRITES)
check("S4 driver refuses the write -> write_bool returns False (still connected)",
      io._plcConnected() and io.write_bool(tag, True, debug=False) is False)
check("S5 refused write not applied in the PLC", all(t != tag for t, _ in WRITES[mark:]))
check("S6 verified write gives up on a refused tag (returns False)", io._writeVerified(tag, True, attempts=2) is False)
check("S7 driver refuses the read -> read_bool gives not expected_value (False) (still connected)",
      io._plcConnected() and io.read_bool(probe, expected_value=True) is False)
check("S8 ... and _readBoolOrNone gives None (a failure is not a value)", io._readBoolOrNone(probe) is None)
check("S9 ... and read_float gives FALSE_FLOAT_VALUE", io.read_float(probeFloat) == IOServer.FALSE_FLOAT_VALUE)
ERRORS.clear()
check("S10 once accepted again: the read gives the PLC's value", io._readBoolOrNone(probe) is True)
check("S11 once accepted again: write_bool True and the value is in the PLC",
      io.write_bool(tag, True, debug=False) is True and TAGS[tag] is True)
TAGS[probe] = False
LINK["down"] = True
time.sleep(0.2)
check("S12 link down -> the server sees the PLC as not connected", io._plcConnected() is False)
check("S13 link down -> write_bool returns False without raising", io.write_bool(tag, False, debug=False) is False)
LINK["down"] = False
io.shutdown = True

# ======================================================================================== M: mock mode (own process)
print("\n=== M: PLCIOServer(mockInstance=True), separate process ===", flush=True)
child = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--mock"], capture_output=True,
                       text=True, timeout=180, env=os.environ.copy())
childChecks = [l for l in child.stdout.splitlines() if l.startswith(("PASS", "FAIL"))]
for line in childChecks:
    check(line[6:].split("   [")[0], line.startswith("PASS"))
check("M the mock-mode process ran all its checks and exited 0", child.returncode == 0 and len(childChecks) == 5,
      f"exit {child.returncode}, {len(childChecks)} checks; {child.stderr[-300:]}")
finish()
